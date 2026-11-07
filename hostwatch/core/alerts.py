"""Notification rules (spec, decision 12).

The rules see core models only and know nothing about hosters. `evaluate` turns the current
data into active events; `plan` compares them with what the owner was already told and
decides what to send now.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, tzinfo
from enum import StrEnum
from html import escape

from hostwatch.core.errors import explain
from hostwatch.core.forecast import Forecast
from hostwatch.core.models import (
    Account,
    AccountStatus,
    Autorenew,
    BillingModel,
    Server,
    ServerStatus,
    Usage,
)
from hostwatch.locales import t

REMINDER_EVERY = timedelta(days=1)
BAD_STATUSES = (ServerStatus.STOPPED, ServerStatus.SUSPENDED, ServerStatus.UNPAID)


class Severity(StrEnum):
    NORMAL = "normal"
    CRITICAL = "critical"


class Kind(StrEnum):
    BALANCE_LOW = "balance_low"
    SERVER_STATUS = "server_status"
    ENDS_SOON = "ends_soon"
    LOAD = "load"
    DISK = "disk"
    RELOGIN = "relogin"
    PLUGIN_BROKEN = "plugin_broken"


@dataclass(frozen=True)
class Thresholds:
    balance_days: int = 5
    # The account that pays for hostwatch's own server is warned this much earlier.
    own_extra_days: int = 3
    load_percent: int = 90
    load_polls: int = 3
    disk_percent: int = 90
    ends_days: int = 3
    failures: int = 3


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time

    @classmethod
    def parse(cls, text: str) -> QuietHours | None:
        """«23:00-08:00» → QuietHours; None if the text is not a range or the range is empty."""
        match = re.fullmatch(r"(\d\d):(\d\d)-(\d\d):(\d\d)", text.strip())
        if match is None:
            return None
        h1, m1, h2, m2 = (int(part) for part in match.groups())
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) == (h2, m2):
            return None
        return cls(time(h1, m1), time(h2, m2))

    def __contains__(self, moment: time) -> bool:
        if self.start < self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


@dataclass(frozen=True)
class AlertSettings:
    lang: str
    tz: tzinfo
    thresholds: Thresholds = Thresholds()
    quiet: QuietHours | None = None
    # Public IP of the server hostwatch runs on, if known (spec, decision 17).
    own_ip: str | None = None

    def is_quiet(self, now: datetime) -> bool:
        if self.quiet is None:
            return False
        return now.astimezone(self.tz).time().replace(tzinfo=None) in self.quiet


@dataclass(frozen=True)
class AccountView:
    """What the rules need to know about one account."""

    account: Account
    title: str
    servers: Sequence[Server]
    forecast: Forecast | None
    usage: Mapping[str, Usage] = field(default_factory=dict)
    # Failed polls in a row since the last success.
    failures: int = 0


@dataclass(frozen=True)
class Event:
    key: str
    kind: Kind
    account_id: int
    state: str
    # A higher rank is worse. Equal ranks with different states are a change, not an improvement.
    rank: int
    severity: Severity
    text: str
    # Short name of what happened, for «resolved» and reminders.
    subject: str


@dataclass(frozen=True)
class Sent:
    """What the owner was last told about an event."""

    key: str
    account_id: int
    state: str
    rank: int
    severity: Severity
    subject: str
    sent_at: datetime


class Reason(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    REMINDER = "reminder"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class Message:
    key: str
    reason: Reason
    severity: Severity
    text: str
    # What to remember once the message is delivered; None forgets the event.
    after: Sent | None


@dataclass(frozen=True)
class Plan:
    messages: list[Message]
    # Changes that need no message: an event got better, or its account is gone.
    silent: list[tuple[str, Sent | None]]

    def batches(self) -> list[list[Message]]:
        """Critical messages go in one Telegram message, the rest in another."""
        critical = [m for m in self.messages if m.severity is Severity.CRITICAL]
        normal = [m for m in self.messages if m.severity is Severity.NORMAL]
        return [batch for batch in (critical, normal) if batch]


class LoadStreaks:
    """How many readings in a row each server's CPU or RAM stayed above the threshold.

    Kept in memory: after a restart the count starts again, and an active alert stays
    until the first new reading.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[int, str], datetime] = {}
        self._counts: dict[tuple[int, str, str], int] = {}

    def observe(self, account_id: int, usage: Usage, percent: int) -> None:
        """Count a reading once, however often it is read from the cache."""
        server = (account_id, usage.server_id)
        if self._seen.get(server) == usage.measured_at:
            return
        self._seen[server] = usage.measured_at
        for metric, value in (("cpu", usage.cpu_percent), ("ram", usage.ram_percent)):
            key = (account_id, usage.server_id, metric)
            if value is None:
                self._counts.pop(key, None)
            elif value > percent:
                self._counts[key] = self._counts.get(key, 0) + 1
            else:
                self._counts[key] = 0

    def count(self, account_id: int, server_id: str, metric: str) -> int | None:
        """None: no reading since the start."""
        return self._counts.get((account_id, server_id, metric))


def evaluate(
    accounts: Iterable[AccountView],
    settings: AlertSettings,
    now: datetime,
    streaks: LoadStreaks,
    active: Collection[str] = (),
) -> list[Event]:
    """Events that are true now. `active` holds keys the owner was told about and that are
    not resolved yet: some conditions end only when the problem is really gone."""
    events: list[Event] = []
    for view in accounts:
        if view.account.status is AccountStatus.DISABLED:
            continue
        rules = _Rules(view, settings, now, streaks, active)
        events.extend(rules.all())
    return events


def plan(
    events: Sequence[Event],
    sent: Mapping[str, Sent],
    now: datetime,
    settings: AlertSettings,
    live_accounts: Collection[int],
) -> Plan:
    quiet = settings.is_quiet(now)
    lang = settings.lang
    messages: list[Message] = []
    silent: list[tuple[str, Sent | None]] = []
    current = {event.key for event in events}

    for event in events:
        before = sent.get(event.key)
        remembered = Sent(
            event.key,
            event.account_id,
            event.state,
            event.rank,
            event.severity,
            event.subject,
            now,
        )
        if before is None:
            reason = Reason.NEW
        elif _changed(event, before):
            reason = Reason.CHANGED
        elif event.rank < before.rank:
            silent.append((event.key, replace(remembered, sent_at=before.sent_at)))
            continue
        elif event.severity is Severity.CRITICAL and now - before.sent_at >= REMINDER_EVERY:
            reason = Reason.REMINDER
        else:
            continue
        if quiet and event.severity is Severity.NORMAL:
            # Waits for the end of the quiet hours; then it is still new or changed.
            continue
        text = event.text
        if reason is Reason.REMINDER:
            text = t("alert.reminder", lang, text=text)
        messages.append(Message(event.key, reason, event.severity, text, remembered))

    for key, before in sent.items():
        if key in current:
            continue
        if before.account_id not in live_accounts:
            silent.append((key, None))
            continue
        if quiet and before.severity is Severity.NORMAL:
            continue
        text = t("alert.resolved", lang, subject=before.subject)
        messages.append(Message(key, Reason.RESOLVED, Severity.NORMAL, text, None))
    return Plan(messages, silent)


def _changed(event: Event, before: Sent) -> bool:
    if event.rank != before.rank:
        return event.rank > before.rank
    return event.state != before.state


def render(batch: Sequence[Message]) -> str:
    texts = [message.text for message in batch]
    return "\n\n".join(texts)


class _Rules:
    def __init__(
        self,
        view: AccountView,
        settings: AlertSettings,
        now: datetime,
        streaks: LoadStreaks,
        active: Collection[str],
    ) -> None:
        self.view = view
        self.acc = view.account.id
        self.title = escape(view.title)
        self.settings = settings
        self.th = settings.thresholds
        self.lang = settings.lang
        self.now = now
        self.streaks = streaks
        self.active = active

    def t(self, key: str, **kwargs: object) -> str:
        return t(key, self.lang, title=self.title, **kwargs)

    def day(self, value: datetime) -> str:
        return value.astimezone(self.settings.tz).strftime("%d.%m")

    def moment(self, value: datetime) -> str:
        return value.astimezone(self.settings.tz).strftime("%d.%m %H:%M")

    def event(
        self, kind: Kind, key: str, state: str, text: str, subject: str, rank: int = 1
    ) -> Event:
        severity = Severity.CRITICAL if kind is Kind.SERVER_STATUS else Severity.NORMAL
        return Event(key, kind, self.acc, state, rank, severity, text, subject)

    def all(self) -> list[Event]:
        events = [*self.account_state()]
        balance = self.balance()
        if balance is not None:
            events.append(balance)
        for server in self.view.servers:
            events.extend(self.server(server))
        return events

    def account_state(self) -> list[Event]:
        account = self.view.account
        if account.status is AccountStatus.RELOGIN:
            key = f"{Kind.RELOGIN}:{self.acc}"
            text = self.t("alert.relogin")
            return [self.event(Kind.RELOGIN, key, "relogin", text, self.t("alert.subject.relogin"))]
        key = f"{Kind.PLUGIN_BROKEN}:{self.acc}"
        failing = self.view.failures >= self.th.failures or key in self.active
        if account.status is AccountStatus.ERROR and failing:
            text = self.t(
                "alert.plugin_broken", reason=escape(explain(account.last_error, self.lang))
            )
            subject = self.t("alert.subject.plugin_broken")
            return [self.event(Kind.PLUGIN_BROKEN, key, "broken", text, subject)]
        return []

    def own(self) -> bool:
        own_ip = self.settings.own_ip
        return own_ip is not None and any(s.ip == own_ip for s in self.view.servers)

    def balance(self) -> Event | None:
        forecast = self.view.forecast
        if forecast is None or forecast.runs_out_at is None:
            return None
        extra = self.th.own_extra_days if self.own() else 0
        days_left = (forecast.runs_out_at - self.now) / timedelta(days=1) - extra
        if days_left >= self.th.balance_days:
            return None
        if days_left < 1:
            state, rank, severity = "1d", 3, Severity.CRITICAL
        elif days_left < 2:
            state, rank, severity = "2d", 2, Severity.NORMAL
        else:
            state, rank, severity = f"{self.th.balance_days}d", 1, Severity.NORMAL
        text = self.t(
            "alert.balance_low",
            amount=forecast.balance,
            date=self.moment(forecast.runs_out_at),
        )
        if extra:
            text += "\n" + self.t("alert.balance_low_own", days=extra)
        key = f"{Kind.BALANCE_LOW}:{self.acc}"
        subject = self.t("alert.subject.balance_low")
        return Event(key, Kind.BALANCE_LOW, self.acc, state, rank, severity, text, subject)

    def server(self, server: Server) -> list[Event]:
        events = []
        name = escape(server.name)
        sid = server.external_id
        # Stage 10 adds power actions; then a stop made through the bot will not alert.
        if server.status in BAD_STATUSES:
            status = t(f"status.{server.status.value}", self.lang)
            events.append(
                self.event(
                    Kind.SERVER_STATUS,
                    f"{Kind.SERVER_STATUS}:{self.acc}:{sid}",
                    server.status.value,
                    self.t("alert.server_status", server=name, status=status),
                    self.t("alert.subject.server", server=name),
                )
            )
        ends = self.ends_soon(server)
        if ends is not None:
            events.append(ends)
        events.extend(self.load(server))
        return events

    def ends_soon(self, server: Server) -> Event | None:
        ends_at = server.next_charge_at
        if server.billing is not BillingModel.PERIODIC or ends_at is None:
            return None
        if not self.now <= ends_at <= self.now + timedelta(days=self.th.ends_days):
            return None
        forecast = self.view.forecast
        short = (
            forecast is not None
            and forecast.shortfall is not None
            and forecast.shortfall.server == server.name
            and forecast.shortfall.at <= ends_at
        )
        if server.autorenew is Autorenew.ON and not short:
            return None
        name = escape(server.name)
        key = "alert.ends_soon_short" if short else "alert.ends_soon"
        return self.event(
            Kind.ENDS_SOON,
            f"{Kind.ENDS_SOON}:{self.acc}:{server.external_id}",
            "short" if short else "not_renewing",
            self.t(key, server=name, date=self.day(ends_at)),
            self.t("alert.subject.ends_soon", server=name),
        )

    def load(self, server: Server) -> list[Event]:
        events = []
        sid = server.external_id
        name = escape(server.name)
        for metric in ("cpu", "ram"):
            key = f"{Kind.LOAD}:{self.acc}:{sid}:{metric}"
            count = self.streaks.count(self.acc, sid, metric)
            high = count is not None and count >= self.th.load_polls
            still = key in self.active and (count is None or count > 0)
            if high or still:
                label = metric.upper()
                events.append(
                    self.event(
                        Kind.LOAD,
                        key,
                        "high",
                        self.t(
                            "alert.load",
                            server=name,
                            metric=label,
                            percent=self.th.load_percent,
                            polls=self.th.load_polls,
                        ),
                        self.t("alert.subject.load", server=name, metric=label),
                    )
                )
        usage = self.view.usage.get(sid)
        disk = None if usage is None else usage.disk_percent
        if disk is not None and disk > self.th.disk_percent:
            events.append(
                self.event(
                    Kind.DISK,
                    f"{Kind.DISK}:{self.acc}:{sid}",
                    "high",
                    self.t("alert.disk", server=name, percent=f"{disk:.0f}"),
                    self.t("alert.subject.disk", server=name),
                )
            )
        return events
