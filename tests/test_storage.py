import sqlite3
from datetime import UTC, datetime, timedelta

from hostwatch.core.models import (
    AccountStatus,
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
)
from hostwatch.storage.db import MIGRATIONS, migrate, schema_version
from hostwatch.storage.repositories import (
    AccountRepository,
    AlertRepository,
    BalanceHistoryRepository,
    JournalEntry,
    JournalRepository,
    ServerRepository,
    SettingsRepository,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def test_migrations_apply_once(conn: sqlite3.Connection) -> None:
    assert schema_version(conn) == len(MIGRATIONS)

    migrate(conn)

    assert schema_version(conn) == len(MIGRATIONS)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "accounts",
        "servers",
        "balance_history",
        "usage_cache",
        "alerts",
        "journal",
        "payments",
        "settings",
    } <= tables


def test_secrets_are_encrypted_at_rest(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    account = accounts.add("fake", "main", {"token": "plain-secret-value"})

    raw = conn.execute("SELECT secrets FROM accounts WHERE id = ?", (account,)).fetchone()[0]

    assert b"plain-secret-value" not in raw
    assert accounts.load_secrets(account) == {"token": "plain-secret-value"}


def test_account_status_flow(accounts: AccountRepository) -> None:
    account = accounts.add("fake", "main", {})

    accounts.set_status(account, AccountStatus.RELOGIN, "auth_expired")
    assert accounts.get(account).status is AccountStatus.RELOGIN

    accounts.mark_ok(account, NOW)
    loaded = accounts.get(account)
    assert loaded.status is AccountStatus.OK
    assert loaded.last_error is None
    assert loaded.last_ok_at == NOW
    assert [a.id for a in accounts.find("main")] == [account]


def test_servers_round_trip(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    account = accounts.add("fake", "main", {})
    repo = ServerRepository(conn)
    server = Server(
        external_id="42",
        name="beaver",
        kind=ServerKind.VPS,
        ip="192.0.2.1",
        plan="small",
        status=ServerStatus.RUNNING,
        raw_status="active",
        price=Money.of("4.50", "EUR"),
        period_months=1,
        next_charge_at=NOW + timedelta(days=3),
        autorenew=Autorenew.ON,
        billing=BillingModel.PERIODIC,
        actions=frozenset({ServerAction.RESTART, ServerAction.RENEW}),
    )

    repo.replace(account, [server], NOW)
    assert repo.for_account(account) == [server]

    repo.replace(account, [], NOW)
    assert repo.for_account(account) == []


def test_balance_history_and_prune(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    account = accounts.add("fake", "main", {})
    repo = BalanceHistoryRepository(conn)
    repo.add(account, Money.of("10", "EUR"), NOW - timedelta(days=200))
    repo.add(account, Money.of("8.25", "EUR"), NOW)

    assert repo.prune(NOW - timedelta(days=180)) == 1
    assert repo.since(account, NOW - timedelta(days=1)) == [(NOW, Money.of("8.25", "EUR"))]


def test_latest_balance_keeps_hoster_hours(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    account = accounts.add("fake", "main", {})
    repo = BalanceHistoryRepository(conn)
    assert repo.latest(account) is None

    repo.add(account, Money.of("10", "RUB"), NOW - timedelta(hours=1))
    repo.add(account, Money.of("9", "RUB"), NOW, hours_left=42.5)

    assert repo.latest(account) == (NOW, Balance(money=Money.of("9", "RUB"), hours_left=42.5))


def test_servers_updated_at(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    account = accounts.add("fake", "main", {})
    repo = ServerRepository(conn)
    assert repo.updated_at(account) is None

    server = Server(
        external_id="1",
        name="owl",
        kind=ServerKind.DEDICATED,
        status=ServerStatus.UNKNOWN,
        raw_status="?",
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.PERIODIC,
    )

    repo.replace(account, [server], NOW)

    assert repo.updated_at(account) == NOW


def test_alerts(conn: sqlite3.Connection) -> None:
    repo = AlertRepository(conn)
    repo.put("balance_low:1", "5d", NOW)
    repo.put("balance_low:1", "2d", NOW)

    alert = repo.get("balance_low:1")
    assert alert is not None and alert.value == "2d"

    repo.delete("balance_low:1")
    assert repo.get("balance_low:1") is None


def test_journal(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    account = accounts.add("fake", "main", {})
    repo = JournalRepository(conn)
    for minutes in range(3):
        repo.add(
            JournalEntry(
                at=NOW + timedelta(minutes=minutes),
                actor="owner",
                account_id=account,
                action="power",
                params={"server": "1", "action": "restart"},
                response="ok",
                ok=True,
            )
        )

    recent = repo.recent(limit=2)
    assert [e.at for e in recent] == [NOW + timedelta(minutes=2), NOW + timedelta(minutes=1)]
    assert recent[0].params == {"server": "1", "action": "restart"}
    assert repo.prune(NOW + timedelta(minutes=1)) == 1


def test_settings(conn: sqlite3.Connection) -> None:
    repo = SettingsRepository(conn)

    assert repo.get("lang", "ru") == "ru"
    repo.set("lang", "en")
    assert repo.get("lang") == "en"
