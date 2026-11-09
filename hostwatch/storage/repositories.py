"""Small repositories over the SQLite tables. Times are stored as ISO 8601 in UTC."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatchcase
from typing import Literal

from hostwatch.core.models import (
    Account,
    AccountStatus,
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Payment,
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


Actor = Literal["owner", "autopay", "system"]


def write_journal(
    conn: sqlite3.Connection,
    actor: Actor,
    action: str,
    account_id: int | None = None,
    params: Mapping[str, str] | None = None,
    response: str = "",
    ok: bool = True,
    at: datetime | None = None,
) -> int:
    """One journal row, inside the caller's transaction. No secrets in `params`."""
    cursor = conn.execute(
        "INSERT INTO journal (at, actor, account_id, action, params, response, ok)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _ts(at or datetime.now(UTC)),
            actor,
            account_id,
            action,
            json.dumps(dict(params or {}), ensure_ascii=False),
            response,
            int(ok),
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


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
            params = {"plugin": plugin_id, "label": label}
            write_journal(self._conn, "owner", "connect", cursor.lastrowid, params)
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

    def set_status(
        self,
        account_id: int,
        status: AccountStatus,
        error: str | None = None,
        actor: Actor = "system",
    ) -> None:
        """A change to or from «needs login» or «disabled» goes to the journal; the
        ok/error flapping of polling does not."""
        with self._conn:
            row = self._conn.execute(
                "SELECT status FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            self._conn.execute(
                "UPDATE accounts SET status = ?, last_error = ? WHERE id = ?",
                (status.value, error, account_id),
            )
            old = None if row is None else AccountStatus(row["status"])
            watched = {AccountStatus.RELOGIN, AccountStatus.DISABLED}
            if old is not None and old is not status and {old, status} & watched:
                params = {"old": old.value, "new": status.value}
                write_journal(self._conn, actor, "account_status", account_id, params)

    def mark_ok(self, account_id: int, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE accounts SET status = 'ok', last_error = NULL, last_ok_at = ? WHERE id = ?",
                (_ts(at), account_id),
            )

    def delete(self, account_id: int) -> None:
        """The account and its history go; the journal keeps a row naming it."""
        with self._conn:
            row = self._conn.execute(
                "SELECT plugin_id, label FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if row is not None:
                params = {"plugin": row["plugin_id"], "label": row["label"]}
                write_journal(self._conn, "owner", "disconnect", account_id, params)
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

    def updated_at(self, account_id: int) -> datetime | None:
        row = self._conn.execute(
            "SELECT MAX(updated_at) AS at FROM servers WHERE account_id = ?", (account_id,)
        ).fetchone()
        return _dt(row["at"])

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
    """The balance as a step line: a row only when it (or `hours_left`) changed, kept
    forever. `checked_at` of the newest row is when that same balance was last read."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self, account_id: int, money: Money, at: datetime, hours_left: float | None = None
    ) -> bool:
        """Keep a read balance. True if it made a new row, False if nothing changed."""
        with self._conn:
            row = self._conn.execute(
                "SELECT id, at, amount, currency, hours_left FROM balance_history"
                " WHERE account_id = ? ORDER BY at DESC, id DESC LIMIT 1",
                (account_id,),
            ).fetchone()
            if (
                row is not None
                and row["currency"] == money.currency
                and Decimal(row["amount"]) == money.amount
                and row["hours_left"] == hours_left
            ):
                if _ts(at) > row["at"]:
                    self._conn.execute(
                        "UPDATE balance_history SET checked_at = ? WHERE id = ?",
                        (_ts(at), row["id"]),
                    )
                return False
            self._conn.execute(
                "INSERT INTO balance_history (account_id, at, amount, currency, hours_left)"
                " VALUES (?, ?, ?, ?, ?)",
                (account_id, _ts(at), str(money.amount), money.currency, hours_left),
            )
        return True

    def latest(self, account_id: int) -> tuple[datetime, Balance] | None:
        """The balance last read, and when it was read."""
        row = self._conn.execute(
            "SELECT at, checked_at, amount, currency, hours_left FROM balance_history"
            " WHERE account_id = ? ORDER BY at DESC, id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        money = Money(amount=Decimal(row["amount"]), currency=row["currency"])
        at = datetime.fromisoformat(row["checked_at"] or row["at"])
        return at, Balance(money=money, hours_left=row["hours_left"])

    def since(self, account_id: int, since: datetime) -> list[tuple[datetime, Money]]:
        """Changes since `since`, led by the last row before it: the balance at `since`."""
        before = self._conn.execute(
            "SELECT at, amount, currency FROM balance_history"
            " WHERE account_id = ? AND at < ? ORDER BY at DESC, id DESC LIMIT 1",
            (account_id, _ts(since)),
        ).fetchall()
        rows = self._conn.execute(
            "SELECT at, amount, currency FROM balance_history"
            " WHERE account_id = ? AND at >= ? ORDER BY at, id",
            (account_id, _ts(since)),
        ).fetchall()
        return [
            (
                datetime.fromisoformat(r["at"]),
                Money(amount=Decimal(r["amount"]), currency=r["currency"]),
            )
            for r in [*before, *rows]
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


# Journal records added for the history (1.1): connecting and disconnecting a hoster,
# account status, settings, «Paid» and its result, autopay marks, holds and answers.
HISTORY_ACTIONS = frozenset(
    {
        "connect",
        "disconnect",
        "account_status",
        "settings",
        "topup_paid",
        "topup_result",
        "autopay_mark",
        "autopay_hold",
        "autopay_answer",
    }
)


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
            return write_journal(
                self._conn,
                entry.actor,
                entry.action,
                entry.account_id,
                entry.params,
                entry.response,
                entry.ok,
                entry.at,
            )

    def note(
        self,
        actor: Actor,
        action: str,
        account_id: int | None = None,
        params: Mapping[str, str] | None = None,
        ok: bool = True,
        response: str = "",
    ) -> int:
        """A one-line record for the flows: `hub.journal.note("owner", "login", acc)`."""
        with self._conn:
            return write_journal(self._conn, actor, action, account_id, params, response, ok)

    def recent(self, limit: int = 20, offset: int = 0) -> list[JournalEntry]:
        """What the chat journal lists: actions with servers and money. The history
        records (`HISTORY_ACTIONS`) are read with `page`."""
        return self.page(limit, offset, exclude=HISTORY_ACTIONS)

    def page(
        self,
        limit: int = 20,
        offset: int = 0,
        *,
        account_id: int | None = None,
        actor: Actor | None = None,
        actions: Collection[str] = (),
        exclude: Collection[str] = (),
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[JournalEntry]:
        """Newest first, filtered by account, actor, action names and time (`since`
        included, `until` not)."""
        where: list[str] = ["1"]
        args: list[object] = []
        if account_id is not None:
            where.append("account_id = ?")
            args.append(account_id)
        if actor is not None:
            where.append("actor = ?")
            args.append(actor)
        if actions:
            where.append(f"action IN ({', '.join('?' for _ in actions)})")
            args.extend(sorted(actions))
        if exclude:
            where.append(f"action NOT IN ({', '.join('?' for _ in exclude)})")
            args.extend(sorted(exclude))
        if since is not None:
            where.append("at >= ?")
            args.append(_ts(since))
        if until is not None:
            where.append("at < ?")
            args.append(_ts(until))
        rows = self._conn.execute(
            f"SELECT * FROM journal WHERE {' AND '.join(where)}"
            " ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
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


class OwnerStopRepository:
    """Servers the owner stopped through the bot, and when (spec, decision 12)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def mark(self, account_id: int, server_id: str, at: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO owner_stops (account_id, server_id, at) VALUES (?, ?, ?)",
                (account_id, server_id, _ts(at)),
            )

    def clear(self, account_id: int, server_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM owner_stops WHERE account_id = ? AND server_id = ?",
                (account_id, server_id),
            )

    def for_account(self, account_id: int) -> dict[str, datetime]:
        rows = self._conn.execute(
            "SELECT server_id, at FROM owner_stops WHERE account_id = ?", (account_id,)
        )
        return {r["server_id"]: datetime.fromisoformat(r["at"]) for r in rows}


PaymentStatus = Literal["created", "waiting", "arrived", "not_seen"]


@dataclass(frozen=True)
class PaymentRecord:
    id: int
    account_id: int
    external_id: str
    amount: Money
    method: str
    url: str
    created_at: datetime
    status: PaymentStatus
    balance_before: Money | None


class PaymentRepository:
    """Top-up invoices the bot made (spec, decision 4). `url` is the link or the address."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, account_id: int, payment: Payment) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO payments (account_id, external_id, amount, currency, method, url,"
                " created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'created')",
                (
                    account_id,
                    payment.id,
                    str(payment.amount.amount),
                    payment.amount.currency,
                    payment.method,
                    payment.url or payment.address or "",
                    _ts(payment.created_at),
                ),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get(self, payment_id: int) -> PaymentRecord:
        row = self._conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if row is None:
            raise NotFound(payment_id)
        before = row["balance_before"]
        return PaymentRecord(
            id=row["id"],
            account_id=row["account_id"],
            external_id=row["external_id"],
            amount=Money(amount=Decimal(row["amount"]), currency=row["currency"]),
            method=row["method"],
            url=row["url"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=row["status"],
            balance_before=None
            if before is None
            else Money(amount=Decimal(before), currency=row["currency"]),
        )

    def set_status(
        self, payment_id: int, status: PaymentStatus, balance_before: Money | None = None
    ) -> None:
        """«Paid» pressed (`waiting`) and what the balance showed then (`arrived`,
        `not_seen`) go to the journal too."""
        with self._conn:
            if balance_before is None:
                self._conn.execute(
                    "UPDATE payments SET status = ? WHERE id = ?", (status, payment_id)
                )
            else:
                self._conn.execute(
                    "UPDATE payments SET status = ?, balance_before = ? WHERE id = ?",
                    (status, str(balance_before.amount), payment_id),
                )
            row = self._conn.execute(
                "SELECT * FROM payments WHERE id = ?", (payment_id,)
            ).fetchone()
            if row is None or status == "created":
                return
            params = {
                "payment": str(payment_id),
                "invoice": row["external_id"],
                "amount": row["amount"],
                "currency": row["currency"],
                "method": row["method"],
            }
            acc = row["account_id"]
            if status == "waiting":
                write_journal(self._conn, "owner", "topup_paid", acc, params)
            else:
                ok = status == "arrived"
                write_journal(
                    self._conn, "system", "topup_result", acc, params, response=status, ok=ok
                )


# Settings the owner changes: each change goes to the journal. The other keys (menu
# message, backup date) are the bot's own bookkeeping.
OWNER_SETTINGS = (
    "lang",
    "timezone",
    "currency",
    "threshold.*",
    "order.limit",
    "order.ssh_key",
    "autopay.days",
    "autopay.limit.*",
)
# Journaled as set or cleared, not by value.
HIDDEN_SETTINGS = frozenset({"order.ssh_key"})
AUTOPAY_LIMIT_PREFIX = "autopay.limit."


class SettingsRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def set(self, key: str, value: str) -> None:
        with self._conn:
            old = self.get(key)
            self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
            if old != value and any(fnmatchcase(key, p) for p in OWNER_SETTINGS):
                self._journal(key, old or "", value)

    def _journal(self, key: str, old: str, new: str) -> None:
        if key in HIDDEN_SETTINGS:
            old, new = ("set" if old else ""), ("set" if new else "cleared")
        account_id = None
        rest = key.removeprefix(AUTOPAY_LIMIT_PREFIX)
        if rest != key and rest.isdigit():
            account_id = int(rest)
        params = {"key": key, "old": old, "new": new}
        write_journal(self._conn, "owner", "settings", account_id, params)
