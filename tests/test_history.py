"""History (1.1, part 4): migrations, load samples and rollup, server and money events,
balance dedupe with the forecast, the wider journal, past load from the hoster."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.errors import RateLimited
from hostwatch.core.forecast import forecast
from hostwatch.core.history import (
    MoneyKind,
    MoneySource,
    ServerEventKind,
    balance_move,
    hoster_charges,
    server_changes,
)
from hostwatch.core.models import (
    AccountStatus,
    Autorenew,
    Balance,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Payment,
    Server,
    ServerKind,
    ServerStatus,
    Usage,
)
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.core.registry import Registry
from hostwatch.plugins.aeza import AezaPlugin
from hostwatch.plugins.fake import FakePlugin
from hostwatch.plugins.timeweb import TimewebPlugin
from hostwatch.scheduler.maintenance import Backups
from hostwatch.scheduler.polling import Poller, PollEvent
from hostwatch.storage.autopay import AutopayStore
from hostwatch.storage.db import MIGRATIONS, connect, migrate, schema_version
from hostwatch.storage.history import HistoryRepository
from hostwatch.storage.repositories import (
    AccountRepository,
    BalanceHistoryRepository,
    JournalRepository,
    PaymentRepository,
    ServerRepository,
    SettingsRepository,
    UsageRepository,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
PLANTED = "planted-value-4f9a2c"


def eur(amount: str) -> Money:
    return Money.of(amount, "EUR")


def server(sid: str = "1", **changes: object) -> Server:
    base = Server(
        external_id=sid,
        name=f"srv{sid}",
        kind=ServerKind.VPS,
        ip="10.0.0.1",
        plan="small",
        status=ServerStatus.RUNNING,
        raw_status="active",
        price=eur("4.50"),
        period_months=1,
        next_charge_at=NOW + timedelta(days=10),
        autorenew=Autorenew.ON,
        billing=BillingModel.PERIODIC,
    )
    return base.model_copy(update=changes)


def usage(sid: str, at: datetime, cpu: float | None, ram: float | None = None) -> Usage:
    return Usage(server_id=sid, measured_at=at, cpu_percent=cpu, ram_percent=ram)


# Migrations


def test_migration_from_schema_5_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.execute("PRAGMA foreign_keys = ON")
    for version, sql in enumerate(MIGRATIONS[:5], start=1):
        old.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
    old.execute(
        "INSERT INTO accounts (id, plugin_id, label, secrets) VALUES (1, 'fake', 'a', x'00')"
    )
    old.executemany(
        "INSERT INTO journal (id, at, actor, account_id, action, params, response, ok)"
        " VALUES (?, ?, ?, 1, ?, '{}', 'done', 1)",
        [
            (7, "2026-11-01T10:00:00+00:00", "owner", "restart"),
            (9, "2026-11-02T10:00:00+00:00", "autopay", "pay_invoice"),
        ],
    )
    old.execute(
        "INSERT INTO balance_history (account_id, at, amount, currency) VALUES"
        " (1, '2026-11-01T10:00:00+00:00', '10.00', 'EUR')"
    )
    old.commit()
    old.close()

    conn = connect(path)

    assert schema_version(conn) == len(MIGRATIONS) == 8
    rows = conn.execute("SELECT id, actor, action, response FROM journal ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        (7, "owner", "restart", "done"),
        (9, "autopay", "pay_invoice", "done"),
    ]
    JournalRepository(conn).note("system", "probe", 1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO journal (at, actor, action, params, response, ok)"
            " VALUES ('x', 'stranger', 'a', '{}', '', 1)"
        )
    indexes = {r["name"] for r in conn.execute("PRAGMA index_list(journal)")}
    assert {"journal_at", "journal_account_at", "journal_action"} <= indexes
    latest = BalanceHistoryRepository(conn).latest(1)
    assert latest is not None and latest[1].money == eur("10.00")
    # The rebuilt journal still lets go of a deleted account.
    AccountRepository(conn, b"0" * 32).delete(1)
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE account_id IS NULL").fetchone()[0] == 4
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_fresh_database_migrates_and_reopens(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    assert schema_version(conn) == len(MIGRATIONS)
    conn.close()


# Load samples, rollup and series


def test_samples_skip_empty_and_repeated(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    acc = accounts.add("fake", "main", {})
    history = HistoryRepository(conn)
    at = NOW.replace(microsecond=123456)
    assert history.add_samples(acc, [usage("1", at, 10.0), usage("2", at, None)]) == 1
    assert history.add_samples(acc, [usage("1", at, 99.0)]) == 0
    series = history.usage_series(acc, "1", NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert series.resolution == "samples"
    assert [(p.at, p.avg["cpu"]) for p in series.points] == [(NOW, 10.0)]


def test_rollup_moves_old_whole_hours_and_merges(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    acc = accounts.add("fake", "main", {})
    history = HistoryRepository(conn)
    old_hour = NOW - timedelta(days=31)
    old = [
        usage("1", old_hour + timedelta(minutes=5), 10.0, 50.0),
        usage("1", old_hour + timedelta(minutes=20), 30.0, None),
        usage("1", old_hour + timedelta(minutes=35), 20.0, 70.0),
    ]
    fresh = [usage("1", NOW - timedelta(days=2), 80.0)]
    history.add_samples(acc, old + fresh)

    assert history.rollup(NOW) == 3

    left = conn.execute("SELECT COUNT(*) FROM usage_samples").fetchone()[0]
    assert left == 1
    row = conn.execute("SELECT * FROM usage_hourly").fetchone()
    assert row["hour"] == old_hour.isoformat()
    assert (row["cpu_avg"], row["cpu_max"], row["samples"]) == (20.0, 30.0, 3)
    assert (row["ram_avg"], row["ram_max"]) == (60.0, 70.0)

    # A late sample of the same hour is merged, weighted by the count.
    history.add_samples(acc, [usage("1", old_hour + timedelta(minutes=50), 60.0)])
    assert history.rollup(NOW) == 1
    row = conn.execute("SELECT * FROM usage_hourly").fetchone()
    assert (row["cpu_avg"], row["cpu_max"], row["samples"]) == (30.0, 60.0, 4)
    assert row["ram_avg"] == 60.0
    assert history.rollup(NOW) == 0


def test_long_series_uses_hours_and_fresh_samples(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    acc = accounts.add("fake", "main", {})
    history = HistoryRepository(conn)
    rolled = NOW - timedelta(days=40)
    history.add_samples(
        acc, [usage("1", rolled, 10.0), usage("1", rolled + timedelta(minutes=30), 20.0)]
    )
    history.rollup(NOW)
    recent = NOW - timedelta(hours=3)
    history.add_samples(
        acc, [usage("1", recent, 40.0), usage("1", recent + timedelta(minutes=15), 60.0)]
    )

    series = history.usage_series(acc, "1", NOW - timedelta(days=60), NOW)

    assert series.resolution == "hourly"
    assert [(p.at, p.avg["cpu"], p.max["cpu"], p.samples) for p in series.points] == [
        (rolled, 15.0, 20.0, 2),
        (recent, 50.0, 60.0, 2),
    ]
    short = history.usage_series(acc, "1", NOW - timedelta(hours=24), NOW)
    assert short.resolution == "samples" and len(short.points) == 2


async def test_nightly_backup_rolls_up(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite3")
    acc = AccountRepository(conn, b"0" * 32).add("fake", "main", {})
    history = HistoryRepository(conn)
    history.add_samples(acc, [usage("1", NOW - timedelta(days=45), 5.0)])
    settings = SettingsRepository(conn)
    backups = Backups(conn, tmp_path / "backups", settings, lambda: ZoneInfo("UTC"), lambda: NOW)
    await backups.tick()
    assert len(backups.files()) == 1
    assert conn.execute("SELECT COUNT(*) FROM usage_samples").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM usage_hourly").fetchone()[0] == 1
    conn.close()


# Server events


def test_server_changes() -> None:
    before = [server("1"), server("2"), server("3")]
    after = [
        server("1", plan="big", price=eur("9.00"), ip="10.0.0.2"),
        server("2", status=ServerStatus.STOPPED, autorenew=Autorenew.OFF, period_months=3),
        server("4"),
    ]
    changes = {(c.server_id, c.kind): (c.old, c.new) for c in server_changes(before, after)}
    assert changes == {
        ("1", ServerEventKind.PLAN): ("small", "big"),
        ("1", ServerEventKind.PRICE): ("4.50 EUR", "9.00 EUR"),
        ("1", ServerEventKind.IP): ("10.0.0.1", "10.0.0.2"),
        ("2", ServerEventKind.STATUS): ("running", "stopped"),
        ("2", ServerEventKind.AUTORENEW): ("on", "off"),
        ("2", ServerEventKind.PERIOD): ("1", "3"),
        ("4", ServerEventKind.APPEARED): (None, "srv4"),
        ("3", ServerEventKind.REMOVED): ("srv3", None),
    }
    assert server_changes(after, after) == []


# Money


def test_balance_move_and_hoster_charges() -> None:
    up = balance_move(Balance(money=eur("10")), Balance(money=eur("15.5")), NOW)
    assert up is not None and up.kind is MoneyKind.TOPUP and up.amount == eur("5.5")
    down = balance_move(Balance(money=eur("10")), Balance(money=eur("9")), NOW)
    assert down is not None and down.kind is MoneyKind.CHARGE and down.amount == eur("1")
    assert balance_move(Balance(money=eur("10")), Balance(money=eur("10.00")), NOW) is None
    assert balance_move(None, Balance(money=eur("10")), NOW) is None
    assert balance_move(Balance(money=eur("10")), Balance(money=Money.of("10", "RUB")), NOW) is None

    invoices = [
        Invoice(id="1", amount=eur("4.5"), status=InvoiceStatus.PAID, server_id="7", due_at=NOW),
        Invoice(id="2", amount=eur("4.5"), status=InvoiceStatus.UNPAID, server_id="7"),
        Invoice(id="3", amount=eur("20"), status=InvoiceStatus.PAID),
    ]
    [charge] = hoster_charges(invoices, NOW)
    assert (charge.server_id, charge.external_id, charge.source) == ("7", "1", MoneySource.HOSTER)


def test_money_by_month(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    acc = accounts.add("fake", "main", {})
    history = HistoryRepository(conn)
    moves = [
        balance_move(Balance(money=eur("10")), Balance(money=eur("30")), NOW),
        balance_move(Balance(money=eur("30")), Balance(money=eur("25")), NOW),
        balance_move(Balance(money=eur("25")), Balance(money=eur("20")), NOW - timedelta(days=40)),
    ]
    history.add_money(acc, [m for m in moves if m is not None])
    charge = Invoice(id="9", amount=eur("5"), status=InvoiceStatus.PAID, server_id="1", due_at=NOW)
    assert history.add_money(acc, hoster_charges([charge], NOW)) == 1
    assert history.add_money(acc, hoster_charges([charge], NOW)) == 0

    months = history.money_by_month(MoneySource.OBSERVED, account_id=acc)
    assert [(m.month, m.topup, m.charge) for m in months] == [
        ("2026-09", Decimal(0), Decimal(5)),
        ("2026-11", Decimal(20), Decimal(5)),
    ]
    by_server = history.money_by_month(MoneySource.HOSTER, by_server=True)
    assert [(m.server_id, m.charge) for m in by_server] == [("1", Decimal(5))]
    assert len(history.money_events(acc, source=MoneySource.OBSERVED)) == 3
    assert [e.external_id for e in history.money_events(server_id="1")] == ["9"]


# Balance dedupe and the forecast


def test_balance_row_only_on_change(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    acc = accounts.add("fake", "main", {})
    repo = BalanceHistoryRepository(conn)
    assert repo.add(acc, eur("10"), NOW - timedelta(hours=2))
    assert not repo.add(acc, eur("10.00"), NOW - timedelta(hours=1))
    assert repo.latest(acc) == (NOW - timedelta(hours=1), Balance(money=eur("10")))
    assert repo.add(acc, eur("10"), NOW, hours_left=40.0)
    assert repo.add(acc, eur("9"), NOW + timedelta(hours=1), hours_left=40.0)
    assert conn.execute("SELECT COUNT(*) FROM balance_history").fetchone()[0] == 3
    # Since a moment: led by the balance at that moment.
    points = repo.since(acc, NOW - timedelta(minutes=30))
    assert [at for at, _ in points] == [NOW - timedelta(hours=2), NOW, NOW + timedelta(hours=1)]


def test_forecast_reconciles_sparse_history() -> None:
    """With rows only on change, the window starts with a row from before it."""
    cloud = server("c", billing=BillingModel.HOURLY, price=eur("0.05"), period_months=None)
    history = [(NOW - timedelta(days=20), eur("100")), (NOW - timedelta(days=5), eur("90"))]

    result = forecast(Balance(money=eur("83.20")), [cloud], NOW, history)

    assert result.reconciliation is not None
    assert result.reconciliation.actual == eur("16.80")
    assert result.reconciliation.predicted == eur("16.80")
    unchanged = forecast(
        Balance(money=eur("100")), [], NOW, [(NOW - timedelta(days=30), eur("100"))]
    )
    assert unchanged.reconciliation is not None
    assert unchanged.reconciliation.actual == eur("0")


# The poller


class Hoster(FakePlugin):
    """A fake hoster whose servers, balance and charges a test changes."""

    servers_now: ClassVar[list[Server]] = []
    balance_now: ClassVar[Money] = eur("20")
    charges: ClassVar[list[Invoice]] = []
    history_error: ClassVar[Exception | None] = None
    history_calls: ClassVar[list[str]] = []

    async def servers(self) -> list[Server]:
        return list(self.servers_now)

    async def balance(self) -> Balance:
        return Balance(money=self.balance_now)

    async def invoices(self) -> list[Invoice]:
        return list(self.charges)

    async def usage_history(self, server_id: str, since: datetime) -> list[Usage]:
        self.history_calls.append(server_id)
        if self.history_error is not None:
            raise self.history_error
        return [usage(server_id, NOW - timedelta(hours=h), 5.0) for h in (3, 2, 1)]


@pytest.fixture
def hoster() -> type[Hoster]:
    Hoster.servers_now = [server("1"), server("2")]
    Hoster.balance_now = eur("20")
    Hoster.charges = []
    Hoster.history_error = None
    Hoster.history_calls = []
    return Hoster


def make_poller(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> tuple[Poller, list[float]]:
    clock = [0.0]

    async def notify(_: PollEvent) -> None:
        pass

    runner = AccountRunner(accounts, Registry({"fake": Hoster}), lambda _: httpx.AsyncClient())
    poller = Poller(
        runner,
        accounts,
        ServerRepository(conn),
        UsageRepository(conn),
        BalanceHistoryRepository(conn),
        notify,
        clock=lambda: clock[0],
        now=lambda: NOW,
        history=HistoryRepository(conn),
    )
    return poller, clock


async def test_poll_records_history(
    conn: sqlite3.Connection, accounts: AccountRepository, hoster: type[Hoster]
) -> None:
    acc = accounts.add("fake", "main", {"token": PLANTED})
    poller, clock = make_poller(conn, accounts)
    history = HistoryRepository(conn)

    await poller.poll_account(acc)

    kinds = sorted((e.server_id, e.kind) for e in history.server_events(acc))
    assert kinds == [("1", "appeared"), ("2", "appeared")]
    # Usage of this poll and three past hours per server.
    assert conn.execute("SELECT COUNT(*) FROM usage_samples").fetchone()[0] == 2 + 6
    assert sorted(hoster.history_calls) == ["1", "2"]

    hoster.servers_now = [server("1", price=eur("6.00"))]
    hoster.balance_now = eur("35")
    hoster.charges = [
        Invoice(id="77", amount=eur("4.5"), status=InvoiceStatus.PAID, server_id="1", due_at=NOW)
    ]
    clock[0] += 3600 * 25
    await poller.poll_account(acc)

    events = {(e.server_id, e.kind, e.old, e.new) for e in history.server_events(acc)}
    assert ("1", "price", "4.50 EUR", "6.00 EUR") in events
    assert ("2", "removed", "srv2", None) in events
    moves = {(m.kind, m.amount.amount, m.source) for m in history.money_events(acc)}
    assert moves == {
        (MoneyKind.TOPUP, Decimal(15), MoneySource.OBSERVED),
        (MoneyKind.CHARGE, Decimal("4.5"), MoneySource.HOSTER),
    }
    # The past load is read once per server.
    assert sorted(hoster.history_calls) == ["1", "2"]
    assert conn.execute("SELECT COUNT(*) FROM balance_history").fetchone()[0] == 2


async def test_backfill_waits_out_a_rate_limit(
    conn: sqlite3.Connection, accounts: AccountRepository, hoster: type[Hoster]
) -> None:
    acc = accounts.add("fake", "main", {})
    poller, _ = make_poller(conn, accounts)
    history = HistoryRepository(conn)
    hoster.history_error = RateLimited()

    await poller.poll_account(acc)
    assert history.needs_backfill(acc, ["1", "2"]) == ["1", "2"]
    assert accounts.get(acc).status is AccountStatus.OK

    hoster.history_error = ValueError("odd answer")
    for _ in range(3):
        await poller.poll_account(acc)
    assert history.needs_backfill(acc, ["1", "2"]) == []


# The journal


def test_journal_records_owner_and_bot_actions(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    journal = JournalRepository(conn)
    acc = accounts.add("fake", "main", {"token": PLANTED})
    accounts.set_status(acc, AccountStatus.ERROR, "flaky")
    accounts.set_status(acc, AccountStatus.OK)
    accounts.set_status(acc, AccountStatus.RELOGIN, "expired")
    accounts.set_status(acc, AccountStatus.OK, actor="owner")
    settings = SettingsRepository(conn)
    settings.set("lang", "en")
    settings.set("lang", "en")
    settings.set("order.ssh_key", "ssh-ed25519 AAAAC3Nza owner@pc")
    settings.set("menu.message_id", "42")
    store = AutopayStore(conn)
    store.set_limit(acc, Decimal("30"))
    store.mark(acc, "1", True, datetime.now(UTC))
    payments = PaymentRepository(conn)
    pay = payments.add(
        acc,
        Payment(id="inv-5", amount=eur("10"), method="sbp", created_at=NOW, url="https://pay"),
    )
    payments.set_status(pay, "waiting", eur("20"))
    payments.set_status(pay, "arrived")

    actions = [(e.actor, e.action) for e in reversed(journal.page(100))]
    assert actions == [
        ("owner", "connect"),
        ("system", "account_status"),
        ("owner", "account_status"),
        ("owner", "settings"),
        ("owner", "settings"),
        ("owner", "settings"),
        ("owner", "autopay_mark"),
        ("owner", "topup_paid"),
        ("system", "topup_result"),
    ]
    limit = journal.page(10, actions=["settings"], account_id=acc)
    assert [e.params["key"] for e in limit] == ["autopay.limit.1"]
    [ssh] = [
        e for e in journal.page(10, actions=["settings"]) if e.params["key"] == "order.ssh_key"
    ]
    assert ssh.params == {"key": "order.ssh_key", "old": "", "new": "set"}
    assert all("AAAA" not in json.dumps(e.params) for e in journal.page(100))
    assert journal.page(10, actor="system", since=NOW - timedelta(days=1000))
    # The chat journal lists them too: each has a label now.
    assert len(journal.recent(100)) == len(actions)

    accounts.delete(acc)
    [gone] = journal.page(1, actions=["disconnect"])
    assert gone.params == {"plugin": "fake", "label": "main"} and gone.account_id is None


async def test_nothing_secret_is_stored_outside_the_account(
    conn: sqlite3.Connection, accounts: AccountRepository, hoster: type[Hoster]
) -> None:
    acc = accounts.add("fake", "main", {"token": PLANTED, "cookie_session": PLANTED + "c"})
    poller, _ = make_poller(conn, accounts)
    await poller.poll_account(acc)
    SettingsRepository(conn).set("order.ssh_key", "ssh-ed25519 AAAAsecretish")
    accounts.set_status(acc, AccountStatus.RELOGIN, "expired")

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    found = []
    for table in tables:
        if table in ("accounts", "settings"):
            continue
        for row in conn.execute(f"SELECT * FROM {table}"):
            text = " ".join(str(v) for v in tuple(row))
            if PLANTED in text or "AAAA" in text or re.search(r"(?i)cookie|bearer", text):
                found.append((table, text))
    assert found == []


# Plugins: past load


async def ignore(_: Secrets) -> None:
    pass


async def test_aeza_usage_history_reads_hours_one_metric_at_a_time() -> None:
    plugin = AezaPlugin(PluginContext({"api_key": "k"}, httpx.AsyncClient(), ignore))
    t0 = int((NOW - timedelta(hours=2)).timestamp())
    with respx.mock() as router:
        route = router.get(
            host="my.aeza.net", path__regex=r"^/api/v2/services/5/stats/\w+$"
        ).respond(200, json={"data": [[t0, 12.5], [t0 + 3600, None]]})
        points = await plugin.usage_history("5", NOW - timedelta(days=30))
    assert route.call_count == 4
    params = route.calls[0].request.url.params
    assert params["resolution"] == "3600"
    assert [(p.measured_at, p.cpu_percent, p.ram_percent, p.net_out_mbits) for p in points] == [
        (NOW - timedelta(hours=2), 12.5, 12.5, 12.5)
    ]


async def test_timeweb_usage_history_asks_hours_back_from_now() -> None:
    plugin = TimewebPlugin(PluginContext({"api_token": "t"}, httpx.AsyncClient(), ignore))
    since = datetime.now(UTC) - timedelta(days=30)
    old, new = since - timedelta(hours=1), since + timedelta(hours=1)
    answer = {
        "statistics": [
            {
                "name": "system.cpu.util",
                "list": [
                    {"value": 7.0, "time": new.isoformat()},
                    {"value": 3.0, "time": old.isoformat()},
                ],
            }
        ]
    }
    with respx.mock() as router:
        route = router.get(
            host="api.timeweb.cloud", path__regex=r"/statistics/[^/]+/720/system\.cpu\.util$"
        ).respond(200, json=answer)
        points = await plugin.usage_history("12", since)
        assert await plugin.usage_history("dedicated-3", since) == []
    assert route.call_count == 1
    assert [(p.cpu_percent, p.ram_percent) for p in points] == [(7.0, None)]
