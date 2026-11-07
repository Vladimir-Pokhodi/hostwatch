"""Small repositories over the SQLite tables. Times are stored as ISO 8601 in UTC."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from hostwatch.core.models import (
    Account,
    AccountStatus,
    Autorenew,
    BillingModel,
    Money,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    Usage,
)
from hostwatch.core.secrets import decrypt, encrypt


class NotFound(LookupError):
    pass


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class AccountRepository:
    def __init__(self, conn: sqlite3.Connection, key: bytes) -> None:
        self._conn = conn
        self._key = key

    def add(self, plugin_id: str, label: str, secrets: Mapping[str, str]) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO accounts (plugin_id, label, secrets) VALUES (?, ?, ?)",
                (plugin_id, label, self._encrypt(secrets)),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get(self, account_id: int) -> Account:
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise NotFound(f"account {account_id}")
        return self._account(row)

    def find(self, label: str) -> list[Account]:
        rows = self._conn.execute("SELECT * FROM accounts WHERE label = ? ORDER BY id", (label,))
        return [self._account(row) for row in rows]

    def all(self) -> list[Account]:
        return [
            self._account(row) for row in self._conn.execute("SELECT * FROM accounts ORDER BY id")
        ]

    def load_secrets(self, account_id: int) -> dict[str, str]:
        row = self._conn.execute(
            "SELECT secrets FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"account {account_id}")
        secrets: dict[str, str] = json.loads(decrypt(row["secrets"], self._key))
        return secrets

    def save_secrets(self, account_id: int, secrets: Mapping[str, str]) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE accounts SET secrets = ? WHERE id = ?",
                (self._encrypt(secrets), account_id),
            )

    def set_status(self, account_id: int, status: AccountStatus, error: str | None = None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE accounts SET status = ?, last_error = ? WHERE id = ?",
                (status.value, error, account_id),
            )

    def mark_ok(self, account_id: int, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE accounts SET status = 'ok', last_error = NULL, last_ok_at = ? WHERE id = ?",
                (_ts(at), account_id),
            )

    def delete(self, account_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def _encrypt(self, secrets: Mapping[str, str]) -> bytes:
        return encrypt(json.dumps(dict(secrets)).encode(), self._key)

    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            plugin_id=row["plugin_id"],
            label=row["label"],
            status=AccountStatus(row["status"]),
            last_error=row["last_error"],
            last_ok_at=_dt(row["last_ok_at"]),
        )


class ServerRepository:
    """Holds the latest snapshot of each account's servers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace(self, account_id: int, servers: list[Server], at: datetime) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM servers WHERE account_id = ?", (account_id,))
            self._conn.executemany(
                """
                INSERT INTO servers (
                    account_id, external_id, name, kind, ip, plan, status, raw_status,
                    price_amount, price_currency, period_months, next_charge_at,
                    autorenew, billing, actions, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._row(account_id, server, at) for server in servers],
            )

    def for_account(self, account_id: int) -> list[Server]:
        rows = self._conn.execute(
            "SELECT * FROM servers WHERE account_id = ? ORDER BY name, external_id", (account_id,)
        )
        return [self._server(row) for row in rows]

    @staticmethod
    def _row(account_id: int, s: Server, at: datetime) -> tuple[object, ...]:
        return (
            account_id,
            s.external_id,
            s.name,
            s.kind.value,
            s.ip,
            s.plan,
            s.status.value,
            s.raw_status,
            None if s.price is None else str(s.price.amount),
            None if s.price is None else s.price.currency,
            s.period_months,
            None if s.next_charge_at is None else _ts(s.next_charge_at),
            s.autorenew.value,
            s.billing.value,
            json.dumps(sorted(a.value for a in s.actions)),
            _ts(at),
        )

    @staticmethod
    def _server(row: sqlite3.Row) -> Server:
        price = None
        if row["price_amount"] is not None:
            price = Money.of(row["price_amount"], row["price_currency"])
        return Server(
            external_id=row["external_id"],
            name=row["name"],
            kind=ServerKind(row["kind"]),
            ip=row["ip"],
            plan=row["plan"],
            status=ServerStatus(row["status"]),
            raw_status=row["raw_status"],
            price=price,
            period_months=row["period_months"],
            next_charge_at=_dt(row["next_charge_at"]),
            autorenew=Autorenew(row["autorenew"]),
            billing=BillingModel(row["billing"]),
            actions=frozenset(ServerAction(a) for a in json.loads(row["actions"])),
        )


class UsageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def put(self, account_id: int, usage: Usage) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO usage_cache (
                    account_id, server_id, measured_at,
                    cpu_percent, ram_percent, disk_percent, traffic_gb
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    usage.server_id,
                    _ts(usage.measured_at),
                    usage.cpu_percent,
                    usage.ram_percent,
                    usage.disk_percent,
                    usage.traffic_gb,
                ),
            )

    def get(self, account_id: int, server_id: str) -> Usage | None:
        row = self._conn.execute(
            "SELECT * FROM usage_cache WHERE account_id = ? AND server_id = ?",
            (account_id, server_id),
        ).fetchone()
        if row is None:
            return None
        return Usage(
            server_id=row["server_id"],
            measured_at=datetime.fromisoformat(row["measured_at"]),
            cpu_percent=row["cpu_percent"],
            ram_percent=row["ram_percent"],
            disk_percent=row["disk_percent"],
            traffic_gb=row["traffic_gb"],
        )


class BalanceHistoryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, account_id: int, money: Money, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO balance_history (account_id, at, amount, currency)"
                " VALUES (?, ?, ?, ?)",
                (account_id, _ts(at), str(money.amount), money.currency),
            )

    def since(self, account_id: int, since: datetime) -> list[tuple[datetime, Money]]:
        rows = self._conn.execute(
            "SELECT at, amount, currency FROM balance_history"
            " WHERE account_id = ? AND at >= ? ORDER BY at",
            (account_id, _ts(since)),
        )
        return [
            (
                datetime.fromisoformat(r["at"]),
                Money(amount=Decimal(r["amount"]), currency=r["currency"]),
            )
            for r in rows
        ]

    def prune(self, before: datetime) -> int:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM balance_history WHERE at < ?", (_ts(before),))
        return cursor.rowcount


@dataclass(frozen=True)
class Alert:
    key: str
    value: str
    sent_at: datetime


class AlertRepository:
    """Last sent state of each alert, so the same event is not sent twice."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str) -> Alert | None:
        row = self._conn.execute("SELECT * FROM alerts WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return Alert(row["key"], row["value"], datetime.fromisoformat(row["sent_at"]))

    def put(self, key: str, value: str, sent_at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO alerts (key, value, sent_at) VALUES (?, ?, ?)",
                (key, value, _ts(sent_at)),
            )

    def delete(self, key: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM alerts WHERE key = ?", (key,))

    def all(self) -> list[Alert]:
        rows = self._conn.execute("SELECT * FROM alerts ORDER BY key")
        return [Alert(r["key"], r["value"], datetime.fromisoformat(r["sent_at"])) for r in rows]


Actor = Literal["owner", "autopay"]


@dataclass(frozen=True)
class JournalEntry:
    at: datetime
    actor: Actor
    account_id: int | None
    action: str
    params: dict[str, str]
    # The hoster's answer with secrets already removed by the caller.
    response: str
    ok: bool
    id: int | None = None


class JournalRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, entry: JournalEntry) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO journal (at, actor, account_id, action, params, response, ok)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _ts(entry.at),
                    entry.actor,
                    entry.account_id,
                    entry.action,
                    json.dumps(entry.params, ensure_ascii=False),
                    entry.response,
                    int(entry.ok),
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def recent(self, limit: int = 20, offset: int = 0) -> list[JournalEntry]:
        rows = self._conn.execute(
            "SELECT * FROM journal ORDER BY at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return [
            JournalEntry(
                at=datetime.fromisoformat(r["at"]),
                actor=r["actor"],
                account_id=r["account_id"],
                action=r["action"],
                params=json.loads(r["params"]),
                response=r["response"],
                ok=bool(r["ok"]),
                id=r["id"],
            )
            for r in rows
        ]

    def prune(self, before: datetime) -> int:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM journal WHERE at < ?", (_ts(before),))
        return cursor.rowcount


class SettingsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def set(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
