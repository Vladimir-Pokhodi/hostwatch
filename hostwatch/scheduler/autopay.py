"""The autopay job: once an hour, pay due renewal invoices of marked servers (stage 11).

Each account is read (invoices, balance), the rule in `core.autopay` decides, and only
then the plugin is asked to pay. Every attempt is in the journal before the hoster is
called. Held invoices (grown sum, limit, balance) reach the owner once per invoice and sum.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from enum import StrEnum

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.autopay import Decision, Rule, Verdict, decide, month_start
from hostwatch.core.control import Control, Request, journal_text, perform
from hostwatch.core.errors import describe
from hostwatch.core.models import AccountStatus, ActionResult, Balance, Invoice, Money
from hostwatch.core.plugin import Capability, Plugin, SupportsInvoices, capabilities
from hostwatch.core.registry import Registry
from hostwatch.storage.autopay import AutopayStore
from hostwatch.storage.repositories import AccountRepository, ServerRepository

log = logging.getLogger(__name__)

HOUR = 3600.0
READ_TIMEOUT = 90.0
PAY_TIMEOUT = 90.0


class Outcome(StrEnum):
    PAID = "paid"
    # The hoster took the request but did not confirm the invoice is paid.
    UNCLEAR = "unclear"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class Plan:
    account_id: int
    caps: frozenset[Capability]
    rule: Rule
    balance: Money
    marked: frozenset[str]
    decisions: list[Decision]


@dataclass(frozen=True)
class Event:
    """Something the owner must hear about: a payment, or an invoice the rule held."""

    account_id: int
    decision: Decision
    rule: Rule
    balance: Money
    # None for a held invoice.
    outcome: Outcome | None = None
    detail: str = ""


Report = Callable[[Event], Awaitable[None]]


class AutopayJob:
    def __init__(
        self,
        runner: AccountRunner,
        accounts: AccountRepository,
        servers: ServerRepository,
        store: AutopayStore,
        registry: Registry,
        report: Report,
        tz: Callable[[], tzinfo],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        refresh: Callable[[int], object] | None = None,
        read_timeout: float = READ_TIMEOUT,
        pay_timeout: float = PAY_TIMEOUT,
    ) -> None:
        self._runner = runner
        self._accounts = accounts
        self._servers = servers
        self._store = store
        self._registry = registry
        self._report = report
        self._tz = tz
        self._now = now
        self._refresh = refresh
        self._read_timeout = read_timeout
        self._pay_timeout = pay_timeout
        # One run at a time: the hourly tick and a run by hand must not pay twice.
        self._lock = asyncio.Lock()

    async def tick(self) -> None:
        for account_id in self._store.accounts():
            try:
                account = self._accounts.get(account_id)
            except LookupError:
                continue
            if account.status in (AccountStatus.RELOGIN, AccountStatus.DISABLED):
                # Polling has told the owner already; nothing can be read now.
                continue
            if account.plugin_id not in self._registry:
                continue
            try:
                await self.run(account_id)
            except Exception as error:
                log.warning("autopay on account %s: %s", account_id, describe(error))

    async def plan(
        self,
        account_id: int,
        marked: Collection[str] | None = None,
        rule: Rule | None = None,
    ) -> Plan:
        """Read the account and decide, paying nothing. `marked` and `rule` replace the
        stored ones for a dry run («what if every server were marked»)."""
        plugin = self._registry.get(self._accounts.get(account_id).plugin_id)
        caps = frozenset(capabilities(plugin))
        if Capability.INVOICES not in caps or Capability.PAY_INVOICE not in caps:
            raise LookupError(f"{plugin.meta.id} cannot read or pay invoices")
        invoices, balance = await asyncio.wait_for(
            self._runner.call(account_id, _read, track_status=False), self._read_timeout
        )
        currency = balance.money.currency
        now = self._now()
        chosen = frozenset(self._store.marked(account_id) if marked is None else marked)
        chosen_rule = rule or self._store.rule(account_id, currency)
        ledger = self._store.ledger(account_id, currency, month_start(now, self._tz()))
        decisions = decide(
            invoices,
            self._servers.for_account(account_id),
            chosen,
            caps,
            chosen_rule,
            ledger,
            balance.money,
            now,
        )
        return Plan(account_id, caps, chosen_rule, balance.money, chosen, decisions)

    async def run(self, account_id: int) -> Plan | None:
        async with self._lock:
            if not self._store.marked(account_id):
                return None
            plan = await self.plan(account_id)
            stop = False
            paid = False
            for decision in plan.decisions:
                if decision.verdict.held:
                    await self._hold(plan, decision)
                elif decision.verdict is Verdict.PAY and not stop:
                    outcome = await self._pay(plan, decision)
                    paid = True
                    # An error in the middle: no more payments until the next run.
                    stop = outcome is not Outcome.PAID
            if paid and self._refresh is not None:
                self._refresh(account_id)
            return plan

    async def _hold(self, plan: Plan, decision: Decision) -> None:
        invoice = decision.invoice
        reason = decision.verdict.value
        old = self._store.notice(plan.account_id, invoice.id)
        if old is not None and old.reason == reason and old.amount == invoice.amount:
            return
        self._store.put_notice(plan.account_id, invoice.id, reason, invoice.amount, self._now())
        sid = invoice.server_id or ""
        self._store.note_hold(plan.account_id, invoice.id, sid, reason, invoice.amount)
        await self._report(Event(plan.account_id, decision, plan.rule, plan.balance))

    async def _pay(self, plan: Plan, decision: Decision) -> Outcome:
        invoice = decision.invoice
        request = Request(
            action=Control.PAY_INVOICE,
            account_id=plan.account_id,
            server_id=invoice.server_id or "",
            invoice_id=invoice.id,
            amount=invoice.amount,
        )
        row = self._store.begin(plan.account_id, request.params(), self._now())
        detail = ""
        try:
            result: ActionResult = await asyncio.wait_for(
                self._runner.call(
                    plan.account_id, lambda p: perform(p, request), track_status=False
                ),
                self._pay_timeout,
            )
        except TimeoutError:
            outcome, detail = Outcome.TIMEOUT, "timeout"
        except Exception as error:
            log.warning("autopay of an invoice on account %s: %s", plan.account_id, describe(error))
            outcome, detail = Outcome.FAILED, describe(error)
        else:
            outcome = Outcome.PAID if result.ok else Outcome.UNCLEAR
            detail = result.detail
        detail = journal_text(detail)
        self._store.finish(row, detail, outcome is Outcome.PAID)
        event = Event(plan.account_id, decision, plan.rule, plan.balance, outcome, detail)
        await self._report(event)
        return outcome


async def _read(plugin: Plugin) -> tuple[list[Invoice], Balance]:
    if not isinstance(plugin, SupportsInvoices):
        raise LookupError("the plugin cannot read invoices")
    invoices = await plugin.invoices()
    return invoices, await plugin.balance()
