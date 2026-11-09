"""Alert rules, repeats, quiet hours and delivery (spec, decision 12)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from hostwatch.core.alerts import (
    AccountView,
    AlertSettings,
    LoadStreaks,
    QuietHours,
    Reason,
    Sent,
    Severity,
    Thresholds,
    evaluate,
    plan,
)
from hostwatch.core.forecast import forecast
from hostwatch.core.models import (
    Account,
    AccountStatus,
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Server,
    ServerKind,
    ServerStatus,
    Usage,
)
from hostwatch.scheduler.alerts import AlertService, DailySummary
from hostwatch.storage.repositories import AlertRepository, SettingsRepository

MSK = ZoneInfo("Europe/Moscow")
# 12:00 in Moscow: outside the default quiet hours.
NOON = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
# 02:00 in Moscow.
NIGHT = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
OWN_IP = "203.0.113.7"


def settings(quiet: str | None = "23:00-08:00", **kwargs: object) -> AlertSettings:
    return AlertSettings(
        lang="ru",
        tz=MSK,
        thresholds=Thresholds(),
        quiet=None if quiet is None else QuietHours.parse(quiet),
        **kwargs,  # type: ignore[arg-type]
    )


def account(
    acc: int = 1, status: AccountStatus = AccountStatus.OK, error: str | None = None
) -> Account:
    return Account(acc, "fake", "main", status, error, None)


def server(
    sid: str = "s1",
    status: ServerStatus = ServerStatus.RUNNING,
    ip: str = "198.51.100.1",
    **kwargs: object,
) -> Server:
    fields: dict[str, object] = {
        "external_id": sid,
        "name": f"vps-{sid}",
        "kind": ServerKind.VPS,
        "ip": ip,
        "status": status,
        "raw_status": status.value,
        "price": Money.of("1", "EUR"),
        "autorenew": Autorenew.NOT_AVAILABLE,
        "billing": BillingModel.HOURLY,
    }
    fields.update(kwargs)
    return Server.model_validate(fields)


def view(
    days_left: float | None = None,
    servers: list[Server] | None = None,
    now: datetime = NOON,
    **kwargs: object,
) -> AccountView:
    """An account whose balance runs out in `days_left` days (the hoster's own estimate)."""
    servers = [server()] if servers is None else servers
    result = None
    if days_left is not None:
        balance = Balance(money=Money.of("10", "EUR"), hours_left=days_left * 24)
        result = forecast(balance, servers, now)
    fields: dict[str, object] = {"account": account(), "title": "Fake", "forecast": result}
    fields.update(kwargs)
    return AccountView(servers=servers, **fields)  # type: ignore[arg-type]


class Book:
    """Runs the rules the way the alert service does and remembers what was delivered."""

    def __init__(self, config: AlertSettings | None = None) -> None:
        self.config = config or settings()
        self.sent: dict[str, Sent] = {}
        self.streaks = LoadStreaks()

    def tick(self, now: datetime, *views: AccountView) -> list[tuple[Reason, str]]:
        events = evaluate(views, self.config, now, self.streaks, self.sent.keys())
        live = {v.account.id for v in views if v.account.status is not AccountStatus.DISABLED}
        result = plan(events, self.sent, now, self.config, live)
        for key, after in result.silent:
            self._apply(key, after)
        for message in result.messages:
            self._apply(message.key, message.after)
        return [(m.reason, m.text) for m in result.messages]

    def _apply(self, key: str, after: Sent | None) -> None:
        if after is None:
            self.sent.pop(key, None)
        else:
            self.sent[key] = after


def reasons(messages: list[tuple[Reason, str]]) -> list[Reason]:
    return [reason for reason, _ in messages]


def test_an_event_comes_once() -> None:
    book = Book()
    first = book.tick(NOON, view(days_left=4))
    assert reasons(first) == [Reason.NEW]
    assert "Fake" in first[0][1]
    assert book.tick(NOON + timedelta(minutes=1), view(days_left=4)) == []
    assert book.tick(NOON + timedelta(hours=3), view(days_left=3.9)) == []


def test_worse_state_comes_again_and_better_one_is_silent() -> None:
    book = Book()
    assert reasons(book.tick(NOON, view(days_left=4.5))) == [Reason.NEW]
    assert book.sent["balance_low:1"].state == "5d"
    assert reasons(book.tick(NOON, view(days_left=1.5))) == [Reason.CHANGED]
    assert book.sent["balance_low:1"].state == "2d"
    # A partial top-up: better, but still low. Nothing to say.
    assert book.tick(NOON, view(days_left=3)) == []
    assert book.sent["balance_low:1"].state == "5d"
    assert reasons(book.tick(NOON, view(days_left=1.5))) == [Reason.CHANGED]


def test_less_than_a_day_is_critical_and_reminded_daily() -> None:
    book = Book()
    book.tick(NOON, view(days_left=0.9))
    assert book.sent["balance_low:1"].severity is Severity.CRITICAL
    later = NOON + timedelta(hours=23)
    assert book.tick(later, view(days_left=0.9, now=later)) == []
    next_day = NOON + timedelta(days=1)
    messages = book.tick(next_day, view(days_left=0.9, now=next_day))
    assert reasons(messages) == [Reason.REMINDER]
    assert messages[0][1].startswith("⏰ Напоминание\n")


def test_resolved_comes_when_the_event_is_gone() -> None:
    book = Book()
    book.tick(NOON, view(days_left=2.5))
    messages = book.tick(NOON, view(days_left=30))
    assert reasons(messages) == [Reason.RESOLVED]
    assert "Решено" in messages[0][1]
    assert book.sent == {}
    assert book.tick(NOON, view(days_left=30)) == []


def test_ordinary_event_waits_for_the_end_of_quiet_hours() -> None:
    book = Book()
    assert book.tick(NIGHT, view(days_left=3, now=NIGHT)) == []
    morning = datetime(2026, 11, 8, 5, 0, tzinfo=UTC)  # 08:00 in Moscow
    assert reasons(book.tick(morning, view(days_left=2.7, now=morning))) == [Reason.NEW]


def test_critical_event_comes_during_quiet_hours() -> None:
    book = Book()
    stopped = view(servers=[server(status=ServerStatus.SUSPENDED)], now=NIGHT)
    messages = book.tick(NIGHT, stopped)
    assert reasons(messages) == [Reason.NEW]
    assert book.sent["server_status:1:s1"].severity is Severity.CRITICAL


def test_quiet_hours_wrap_midnight() -> None:
    quiet = QuietHours.parse("23:00-08:00")
    assert quiet is not None
    assert time(23, 30) in quiet and time(2, 0) in quiet
    assert time(8, 0) not in quiet and time(12, 0) not in quiet
    day = QuietHours.parse("13:00-15:00")
    assert day is not None and time(14, 0) in day and time(16, 0) not in day
    assert QuietHours.parse("25:00-08:00") is None
    assert QuietHours.parse("08:00-08:00") is None


def test_status_change_is_a_new_message() -> None:
    book = Book()
    book.tick(NOON, view(servers=[server(status=ServerStatus.STOPPED)]))
    messages = book.tick(NOON, view(servers=[server(status=ServerStatus.UNPAID)]))
    assert reasons(messages) == [Reason.CHANGED]
    assert reasons(book.tick(NOON, view(servers=[server()]))) == [Reason.RESOLVED]


def test_own_server_is_warned_three_days_earlier() -> None:
    book = Book(settings(own_ip=OWN_IP))
    other = view(days_left=7)
    assert book.tick(NOON, other) == []
    own = view(days_left=7, servers=[server(ip=OWN_IP)])
    messages = book.tick(NOON, own)
    assert reasons(messages) == [Reason.NEW]
    assert "hostwatch" in messages[0][1]


def test_ends_soon_only_without_renewal() -> None:
    ends = NOON + timedelta(days=2)
    periodic = {
        "billing": BillingModel.PERIODIC,
        "price": Money.of("5", "EUR"),
        "period_months": 1,
        "next_charge_at": ends,
    }
    renewing = server(autorenew=Autorenew.ON, **periodic)
    book = Book()
    # Enough money: the server renews, nothing to say.
    rich = AccountView(
        account(),
        "Fake",
        [renewing],
        forecast(Balance(money=Money.of("100", "EUR")), [renewing], NOON),
    )
    assert book.tick(NOON, rich) == []
    # Not enough money for the renewal.
    poor = AccountView(
        account(),
        "Fake",
        [renewing],
        forecast(Balance(money=Money.of("1", "EUR")), [renewing], NOON),
    )
    keys = [m for m in book.tick(NOON, poor) if "закончится" in m[1]]
    assert len(keys) == 1 and "не хватит" in keys[0][1]
    lapsing = server(autorenew=Autorenew.OFF, **periodic)
    book = Book()
    messages = book.tick(NOON, AccountView(account(), "Fake", [lapsing], None))
    assert reasons(messages) == [Reason.NEW]
    assert "Автопродление выключено" in messages[0][1]


def test_load_needs_three_readings_in_a_row_and_disk_one() -> None:
    book = Book()
    srv = server()

    def reading(minute: int, cpu: float, disk: float = 10) -> AccountView:
        usage = Usage(
            server_id="s1",
            measured_at=NOON + timedelta(minutes=minute),
            cpu_percent=cpu,
            disk_percent=disk,
        )
        book.streaks.observe(1, usage, 90)
        return view(servers=[srv], usage={"s1": usage})

    assert book.tick(NOON, reading(0, 95)) == []
    assert book.tick(NOON, reading(15, 95)) == []
    # The same reading again does not count.
    assert book.tick(NOON, reading(15, 95)) == []
    messages = book.tick(NOON, reading(30, 95))
    assert reasons(messages) == [Reason.NEW] and "CPU" in messages[0][1]
    assert reasons(book.tick(NOON, reading(45, 50))) == [Reason.RESOLVED]
    messages = book.tick(NOON, reading(60, 50, disk=95))
    assert reasons(messages) == [Reason.NEW] and "диск" in messages[0][1]


def test_relogin_once_and_broken_after_three_failures() -> None:
    book = Book()
    relogin = view(account=account(status=AccountStatus.RELOGIN))
    assert reasons(book.tick(NOON, relogin)) == [Reason.NEW]
    assert book.tick(NOON + timedelta(days=2), relogin) == []

    book = Book()
    failing = account(status=AccountStatus.ERROR, error="timeout")
    assert book.tick(NOON, view(account=failing, failures=2)) == []
    messages = book.tick(NOON, view(account=failing, failures=3))
    assert reasons(messages) == [Reason.NEW] and "не ответил" in messages[0][1]
    # After a restart the failure count is 0, but the account still fails: not resolved.
    assert book.tick(NOON, view(account=failing, failures=0)) == []
    assert reasons(book.tick(NOON, view())) == [Reason.RESOLVED]


def test_disabled_or_deleted_account_is_forgotten_silently() -> None:
    book = Book()
    book.tick(NOON, view(days_left=2))
    disabled = view(days_left=2, account=account(status=AccountStatus.DISABLED))
    assert book.tick(NOON, disabled) == []
    assert book.sent == {}


@pytest.fixture
def store(conn: sqlite3.Connection) -> AlertRepository:
    return AlertRepository(conn)


async def test_service_keeps_undelivered_alerts_for_the_next_tick(store: AlertRepository) -> None:
    delivered: list[str] = []
    online = False

    async def send(text: str) -> bool:
        if online:
            delivered.append(text)
        return online

    service = AlertService(lambda: [view(days_left=2.5)], settings, store, send, lambda: NOON)
    await service.tick()
    assert store.all() == []
    online = True
    await service.tick()
    await service.tick()
    assert len(delivered) == 1
    assert [a.key for a in store.all()] == ["balance_low:1"]


async def test_service_batches_critical_and_ordinary(store: AlertRepository) -> None:
    delivered: list[str] = []

    async def send(text: str) -> bool:
        delivered.append(text)
        return True

    servers = [server("a", ServerStatus.SUSPENDED), server("b", ServerStatus.UNPAID)]
    accounts = [view(days_left=3, servers=servers)]
    service = AlertService(lambda: accounts, settings, store, send, lambda: NOON)
    await service.tick()
    assert len(delivered) == 2
    assert delivered[0].count("🔴") == 2
    assert "На балансе" in delivered[1]


async def test_summary_once_a_day_at_the_owner_time(conn: sqlite3.Connection) -> None:
    sent: list[str] = []
    now = datetime(2026, 11, 7, 5, 59, tzinfo=UTC)  # 08:59 in Moscow

    async def send(text: str) -> bool:
        sent.append(text)
        return True

    async def text() -> str:
        return "summary"

    summary = DailySummary(
        text, lambda: (time(9, 0), MSK), SettingsRepository(conn), send, lambda: now
    )
    await summary.tick()
    assert sent == []
    now = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)
    await summary.tick()
    await summary.tick()
    assert sent == ["summary"]
    # Down at 09:00, back at 11:00: today's summary is skipped, tomorrow's comes.
    now = datetime(2026, 11, 8, 8, 0, tzinfo=UTC)
    await summary.tick()
    assert sent == ["summary"]
    now = datetime(2026, 11, 9, 6, 30, tzinfo=UTC)
    await summary.tick()
    assert sent == ["summary", "summary"]
