"""Sends alerts and the daily summary to the owner. Rules live in `core/alerts.py`."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, time, timedelta, tzinfo

from hostwatch.core.alerts import (
    AccountView,
    AlertSettings,
    LoadStreaks,
    Message,
    Sent,
    Severity,
    evaluate,
    plan,
    render,
)
from hostwatch.core.models import AccountStatus
from hostwatch.storage.repositories import Alert, AlertRepository, SettingsRepository

log = logging.getLogger(__name__)

# Delivers a message to the owner; False when Telegram did not take it.
Send = Callable[[str], Awaitable[bool]]
# Delivers one batch of alerts (text and buttons); False when Telegram did not take it.
Deliver = Callable[[Sequence[Message]], Awaitable[bool]]
SUMMARY_WINDOW = timedelta(hours=1)
SUMMARY_SENT_KEY = "summary.last_date"


def encode(sent: Sent) -> str:
    return json.dumps(
        {
            "account_id": sent.account_id,
            "state": sent.state,
            "rank": sent.rank,
            "severity": sent.severity.value,
            "subject": sent.subject,
        },
        ensure_ascii=False,
    )


def decode(alert: Alert) -> Sent | None:
    try:
        data = json.loads(alert.value)
        return Sent(
            key=alert.key,
            account_id=int(data["account_id"]),
            state=str(data["state"]),
            rank=int(data["rank"]),
            severity=Severity(data["severity"]),
            subject=str(data["subject"]),
            sent_at=alert.sent_at,
        )
    except (ValueError, KeyError, TypeError):
        return None


class AlertService:
    def __init__(
        self,
        load: Callable[[], Sequence[AccountView]],
        settings: Callable[[], AlertSettings],
        store: AlertRepository,
        send: Send,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        deliver: Deliver | None = None,
    ) -> None:
        self._load = load
        self._settings = settings
        self._store = store
        self._send = send
        self._now = now
        # The bot's delivery puts buttons under the alerts; `send` gets the text alone.
        self._deliver = deliver
        self.streaks = LoadStreaks()

    async def tick(self) -> None:
        now = self._now()
        settings = self._settings()
        views = self._load()
        for view in views:
            for usage in view.usage.values():
                self.streaks.observe(view.account.id, usage, settings.thresholds.load_percent)
        sent: dict[str, Sent] = {}
        for alert in self._store.all():
            decoded = decode(alert)
            if decoded is None:
                self._store.delete(alert.key)
            else:
                sent[alert.key] = decoded
        events = evaluate(views, settings, now, self.streaks, sent.keys())
        live = {v.account.id for v in views if v.account.status is not AccountStatus.DISABLED}
        result = plan(events, sent, now, settings, live)
        for key, after in result.silent:
            self._remember(key, after)
        for batch in result.batches():
            # Not delivered: nothing is remembered, so the next tick tries again.
            delivered = (
                await self._deliver(batch) if self._deliver else await self._send(render(batch))
            )
            if delivered:
                for message in batch:
                    self._remember(message.key, message.after)

    def _remember(self, key: str, sent: Sent | None) -> None:
        if sent is None:
            self._store.delete(key)
        else:
            self._store.put(key, encode(sent), sent.sent_at)


class DailySummary:
    """The summary at the owner's time. Sent once a day, up to an hour late after a restart."""

    def __init__(
        self,
        text: Callable[[], Awaitable[str]],
        when: Callable[[], tuple[time, tzinfo]],
        settings: SettingsRepository,
        send: Send,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._text = text
        self._when = when
        self._settings = settings
        self._send = send
        self._now = now

    async def tick(self) -> None:
        at, tz = self._when()
        local = self._now().astimezone(tz)
        due = datetime.combine(local.date(), at, tz)
        if not due <= local < due + SUMMARY_WINDOW:
            return
        today = local.date().isoformat()
        if self._settings.get(SUMMARY_SENT_KEY) == today:
            return
        if await self._send(await self._text()):
            self._settings.set(SUMMARY_SENT_KEY, today)
