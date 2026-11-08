"""`hostwatch autopay <account>`: a dry run of the autopay rule on live data (stage 11).

It reads the account's invoices and balance from the hoster and prints what the rule would
do with each invoice right now. It never pays. It works on an in-memory copy of the
database, so the live file is only read: not migrated, not written (a session cookie the
hoster reissues during the run is not kept; the running bot keeps its own).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from hostwatch.bot.prefs import Prefs
from hostwatch.core.accounts import AccountRunner
from hostwatch.core.autopay import Decision, Rule, Verdict, eligible
from hostwatch.core.config import ConfigError, load_settings
from hostwatch.core.errors import describe
from hostwatch.core.http import make_client
from hostwatch.core.models import Money, Server
from hostwatch.core.plugin import Capability, capabilities
from hostwatch.core.registry import Registry
from hostwatch.scheduler.autopay import AutopayJob, Event, Plan
from hostwatch.storage.autopay import AutopayStore
from hostwatch.storage.db import migrate
from hostwatch.storage.repositories import (
    AccountRepository,
    NotFound,
    ServerRepository,
    SettingsRepository,
)

DB_NAME = "hostwatch.sqlite3"

EXPLAIN = {
    Verdict.PAY: "would pay now",
    Verdict.ASK_GROWN: "held: the sum grew by more than 10 %, the owner would be asked",
    Verdict.ASK_NEW: "held: no previous period to compare with, the owner would be asked",
    Verdict.OVER_LIMIT: "held: over the monthly limit",
    Verdict.LOW_BALANCE: "held: the balance is not enough",
    Verdict.NOT_UNPAID: "skipped: not unpaid",
    Verdict.NOT_SERVICE: "skipped: not a renewal of one server (top-up, upgrade, several)",
    Verdict.NOT_MARKED: "skipped: the server is not marked for autopay",
    Verdict.NOT_ELIGIBLE: "skipped: the server has the hoster's own autorenewal",
    Verdict.OTHER_CURRENCY: "skipped: another currency",
    Verdict.ALREADY: "skipped: paid or tried before (journal)",
    Verdict.DECLINED: "skipped: the owner said not to pay",
    Verdict.NO_DUE_DATE: "skipped: no due date",
    Verdict.NOT_DUE: "skipped: the due date is further than the rule's days",
}


class DryRunFailed(Exception):
    pass


@dataclass(frozen=True)
class Assume:
    """What the dry run assumes instead of the stored settings."""

    all_eligible: bool = False
    limit: str | None = None
    days: int | None = None


def memory_copy(path: Path) -> sqlite3.Connection:
    """The database copied into memory and migrated there; the file is only read."""
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        conn = sqlite3.connect(":memory:")
        source.backup(conn)
    finally:
        source.close()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


async def dry_run(registry: Registry, target: str, assume: Assume, redact: bool) -> str:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise DryRunFailed(f"the database is unavailable:\n{exc}") from None
    path = settings.data_dir / DB_NAME
    if not path.is_file():
        raise DryRunFailed(f"there is no database at {path}")
    conn = memory_copy(path)
    try:
        accounts = AccountRepository(conn, settings.encryption_key)
        runner = AccountRunner(accounts, registry, lambda pid: make_client(pid, settings.tg_proxy))
        return await describe_plan(
            conn, accounts, runner, registry, _find(accounts, target), assume, redact
        )
    finally:
        conn.close()


async def describe_plan(
    conn: sqlite3.Connection,
    accounts: AccountRepository,
    runner: AccountRunner,
    registry: Registry,
    account_id: int,
    assume: Assume,
    redact: bool,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Plan with the stored or the assumed rule and describe it. Pays nothing."""
    tz = Prefs(SettingsRepository(conn)).tz
    store = AutopayStore(conn)
    servers = ServerRepository(conn)
    job = AutopayJob(runner, accounts, servers, store, registry, _no_report, lambda: tz, now)
    plugin = registry.get(accounts.get(account_id).plugin_id)
    caps = capabilities(plugin)
    if not {Capability.INVOICES, Capability.PAY_INVOICE} <= caps:
        raise DryRunFailed(f"{plugin.meta.name} cannot read or pay invoices: no autopay there")
    known = servers.for_account(account_id)
    can = [s for s in known if eligible(caps, s)]
    marked = frozenset(s.external_id for s in can) if assume.all_eligible else None
    rule = None
    if assume.limit is not None or assume.days is not None:
        stored = store.rule(account_id, plugin.meta.currency)
        rule = Rule(
            days=assume.days if assume.days is not None else stored.days,
            limit=_limit(assume.limit, plugin.meta.currency)
            if assume.limit is not None
            else stored.limit,
        )
    try:
        plan = await job.plan(account_id, marked, rule)
    except Exception as exc:
        raise DryRunFailed(describe(exc)) from None
    return render(plan, plugin.meta.name, known, len(can), assume.all_eligible, redact)


def render(
    plan: Plan,
    hoster: str,
    known: list[Server],
    can: int,
    assumed: bool,
    redact: bool,
) -> str:
    hide = _Hider(redact)
    marked = f"{len(plan.marked)}" + (" (assumed: every eligible one)" if assumed else "")
    lines = [
        f"Autopay dry run: {hoster}, account {plan.account_id}. Nothing is paid.",
        f"Rule: {plan.rule.days} days before the due date,"
        f" monthly limit {hide.money(plan.rule.limit)}",
        f"Balance: {hide.money(plan.balance)}",
        f"Servers: {len(known)}, eligible for autopay {can}, marked {marked}",
        f"Invoices read: {len(plan.decisions)}",
    ]
    lines += ["  " + _line(decision, hide) for decision in plan.decisions]
    pay = sum(d.verdict is Verdict.PAY for d in plan.decisions)
    held = sum(d.verdict.held for d in plan.decisions)
    skipped = len(plan.decisions) - pay - held
    lines.append(f"Total: would pay {pay}, held {held}, skipped {skipped}")
    return "\n".join(lines)


def _line(decision: Decision, hide: _Hider) -> str:
    invoice = decision.invoice
    server = hide.name(decision.server.name) if decision.server is not None else "-"
    due = decision.due_at or invoice.due_at
    when = due.strftime("%Y-%m-%d") if due is not None else "-"
    return (
        f"invoice {hide.id(invoice.id)} {hide.money(invoice.amount)} {invoice.status.value},"
        f" server {server}, due {when}: {EXPLAIN[decision.verdict]}"
    )


class _Hider:
    """With `on`, sums, ids and server names become placeholders: output to share."""

    def __init__(self, on: bool) -> None:
        self._on = on
        self._ids: dict[str, str] = {}
        self._names: dict[str, str] = {}

    def money(self, value: Money) -> str:
        return f"*** {value.currency}" if self._on else str(value)

    def id(self, value: str) -> str:
        if not self._on:
            return f"#{value}"
        return self._ids.setdefault(value, f"#{len(self._ids) + 1}")

    def name(self, value: str) -> str:
        if not self._on:
            return value
        return self._names.setdefault(value, f"server-{len(self._names) + 1}")


async def _no_report(event: Event) -> None:
    raise AssertionError("a dry run reports nothing")


def _limit(text: str, currency: str) -> Money:
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise DryRunFailed(f"bad limit {text!r}") from None
    if not amount.is_finite() or amount < 0:
        raise DryRunFailed(f"bad limit {text!r}")
    return Money(amount=amount, currency=currency)


def _find(accounts: AccountRepository, target: str) -> int:
    if target.isdigit():
        try:
            return accounts.get(int(target)).id
        except NotFound:
            pass
    found = accounts.find(target)
    if len(found) != 1:
        raise DryRunFailed(
            f"no account named {target!r}" if not found else f"several accounts {target!r}"
        )
    return found[0].id
