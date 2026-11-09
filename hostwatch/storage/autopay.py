"""Autopay state in SQLite: marked servers, rule settings, notices and the journal rows
autopay writes (stage 11). Nothing is marked and every limit is zero until the owner
sets them: a fresh deploy pays nothing by itself."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from hostwatch.core.autopay import DEFAULT_DAYS, MAX_DAYS, Ledger, Rule
from hostwatch.core.models import Money
from hostwatch.storage.repositories import SettingsRepository, write_journal

PAY_ACTION = "pay_invoice"
DAYS_KEY = "autopay.days"
LIMIT_KEY = "autopay.limit.{account}"
# Notice reasons that are the owner's answer, not the rule's.
DECLINED = "declined"
APPROVED = "approved"


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class Notice:
    invoice_id: str
    # A `Verdict` value the owner was told about, or DECLINED / APPROVED.
    reason: str
    amount: Money
    at: datetime


class AutopayStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # Marked servers

    def marked(self, account_id: int) -> frozenset[str]:
        rows = self._conn.execute(
            "SELECT server_id FROM autopay_servers WHERE account_id = ?", (account_id,)
        )
        return frozenset(r["server_id"] for r in rows)

    def accounts(self) -> list[int]:
        """Accounts with at least one marked server."""
        rows = self._conn.execute(
            "SELECT DISTINCT account_id FROM autopay_servers ORDER BY account_id"
        )
        return [r["account_id"] for r in rows]

    def mark(self, account_id: int, server_id: str, on: bool, at: datetime) -> None:
        with self._conn:
            if on:
                self._conn.execute(
                    "INSERT OR REPLACE INTO autopay_servers (account_id, server_id, at)"
                    " VALUES (?, ?, ?)",
                    (account_id, server_id, _ts(at)),
                )
            else:
                self._conn.execute(
                    "DELETE FROM autopay_servers WHERE account_id = ? AND server_id = ?",
                    (account_id, server_id),
                )
            params = {"server": server_id, "on": "yes" if on else "no"}
            write_journal(self._conn, "owner", "autopay_mark", account_id, params, at=at)

    # Rule settings, kept in `settings`

    def days(self) -> int:
        value = self._setting(DAYS_KEY)
        if value is not None and value.isdigit() and 1 <= int(value) <= MAX_DAYS:
            return int(value)
        return DEFAULT_DAYS

    def set_days(self, days: int) -> None:
        if not 1 <= days <= MAX_DAYS:
            raise ValueError(f"days must be 1..{MAX_DAYS}")
        self._set_setting(DAYS_KEY, str(days))

    def limit(self, account_id: int, currency: str) -> Money:
        """The monthly limit; zero when the owner has not set one."""
        value = self._setting(LIMIT_KEY.format(account=account_id))
        try:
            amount = Decimal(value) if value is not None else Decimal(0)
        except InvalidOperation:
            amount = Decimal(0)
        if not amount.is_finite() or amount < 0:
            amount = Decimal(0)
        return Money(amount=amount, currency=currency)

    def set_limit(self, account_id: int, amount: Decimal) -> None:
        if not amount.is_finite() or amount < 0:
            raise ValueError("the limit must be zero or more")
        self._set_setting(LIMIT_KEY.format(account=account_id), f"{amount:f}")

    def rule(self, account_id: int, currency: str) -> Rule:
        return Rule(days=self.days(), limit=self.limit(account_id, currency))

    # The journal

    def ledger(self, account_id: int, currency: str, since: datetime) -> Ledger:
        """Invoices paid or tried, the last paid sum per server, and what autopay took
        since `since`. A row whose result is not known yet counts as money gone."""
        rows = self._conn.execute(
            "SELECT at, actor, params, ok FROM journal"
            " WHERE account_id = ? AND action = ? ORDER BY at, id",
            (account_id, PAY_ACTION),
        )
        done: set[str] = set()
        previous: dict[str, Money] = {}
        spent = Money(amount=Decimal(0), currency=currency)
        for row in rows:
            params = json.loads(row["params"])
            invoice = params.get("invoice", "")
            amount = _money(params)
            ok = bool(row["ok"])
            if ok or row["actor"] == "autopay":
                done.add(invoice)
            if ok and amount is not None and params.get("server"):
                previous[params["server"]] = amount
            recent = datetime.fromisoformat(row["at"]) >= since
            mine = row["actor"] == "autopay" and recent
            if mine and amount is not None and amount.currency == currency:
                spent += amount
        declined = frozenset(
            r["invoice_id"]
            for r in self._conn.execute(
                "SELECT invoice_id FROM autopay_notices WHERE account_id = ? AND reason = ?",
                (account_id, DECLINED),
            )
        )
        return Ledger(
            done=frozenset(done - {""}), declined=declined, previous=previous, spent=spent
        )

    def begin(self, account_id: int, params: dict[str, str], at: datetime) -> int:
        """Write the attempt before calling the hoster: if the bot dies in the middle,
        the invoice is still never paid twice."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO journal (at, actor, account_id, action, params, response, ok)"
                " VALUES (?, 'autopay', ?, ?, ?, 'started', 0)",
                (_ts(at), account_id, PAY_ACTION, json.dumps(params, ensure_ascii=False)),
            )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def finish(self, row_id: int, response: str, ok: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE journal SET response = ?, ok = ? WHERE id = ?",
                (response, int(ok), row_id),
            )

    def note_hold(
        self, account_id: int, invoice_id: str, server_id: str, reason: str, amount: Money
    ) -> None:
        """An invoice the rule held, once per reason and sum (the caller checks)."""
        params = {
            "invoice": invoice_id,
            "server": server_id,
            "reason": reason,
            "amount": str(amount.amount),
            "currency": amount.currency,
        }
        with self._conn:
            write_journal(self._conn, "autopay", "autopay_hold", account_id, params, ok=False)

    # Notices

    def notice(self, account_id: int, invoice_id: str) -> Notice | None:
        row = self._conn.execute(
            "SELECT * FROM autopay_notices WHERE account_id = ? AND invoice_id = ?",
            (account_id, invoice_id),
        ).fetchone()
        if row is None:
            return None
        return Notice(
            invoice_id=row["invoice_id"],
            reason=row["reason"],
            amount=Money(amount=Decimal(row["amount"]), currency=row["currency"]),
            at=datetime.fromisoformat(row["at"]),
        )

    def put_notice(
        self, account_id: int, invoice_id: str, reason: str, amount: Money, at: datetime
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO autopay_notices"
                " (account_id, invoice_id, reason, amount, currency, at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (account_id, invoice_id, reason, str(amount.amount), amount.currency, _ts(at)),
            )

    def take_notice(self, account_id: int, invoice_id: str, reasons: set[str], to: str) -> bool:
        """Move a notice from one of `reasons` to `to`, once: a second press finds nothing."""
        placeholders = ", ".join("?" for _ in reasons)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE autopay_notices SET reason = ? WHERE account_id = ? AND invoice_id = ?"
                f" AND reason IN ({placeholders})",
                (to, account_id, invoice_id, *sorted(reasons)),
            )
            if cursor.rowcount == 1:
                params = {"invoice": invoice_id, "answer": to}
                write_journal(self._conn, "owner", "autopay_answer", account_id, params)
        return cursor.rowcount == 1

    def _setting(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _set_setting(self, key: str, value: str) -> None:
        # Through the repository, so the change is in the journal.
        SettingsRepository(self._conn).set(key, value)


def _money(params: dict[str, str]) -> Money | None:
    try:
        return Money(amount=Decimal(params["amount"]), currency=params["currency"])
    except (KeyError, InvalidOperation, ValueError):
        return None
