"""Autopay rule: which invoices the bot pays from the balance by itself (spec, decision 10).

The rule pays an invoice the hoster already issued, only for servers the owner marked, and
only when the hoster has no autorenewal of its own. Every guard is here, as pure functions
over what the hoster and the journal say, so the scheduler, the dry run and the tests share
them. Nothing is marked and the monthly limit is zero until the owner sets them.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum

from hostwatch.core.models import (
    Autorenew,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Server,
)
from hostwatch.core.plugin import Capability

DEFAULT_DAYS = 3
MAX_DAYS = 30
# A sum that grew by more than this against the last period is not paid without the owner.
GROWTH = Decimal("0.10")


class Verdict(StrEnum):
    PAY = "pay"
    # Held: the owner hears about these once per invoice and sum.
    ASK_GROWN = "ask_grown"
    ASK_NEW = "ask_new"
    OVER_LIMIT = "over_limit"
    LOW_BALANCE = "low_balance"
    # Skipped quietly.
    NOT_UNPAID = "not_unpaid"
    NOT_SERVICE = "not_service"
    NOT_MARKED = "not_marked"
    NOT_ELIGIBLE = "not_eligible"
    OTHER_CURRENCY = "other_currency"
    ALREADY = "already"
    DECLINED = "declined"
    NO_DUE_DATE = "no_due_date"
    NOT_DUE = "not_due"

    @property
    def asks(self) -> bool:
        return self in (Verdict.ASK_GROWN, Verdict.ASK_NEW)

    @property
    def held(self) -> bool:
        """The rule would pay, but a guard stops it and the owner should know."""
        return self.asks or self in (Verdict.OVER_LIMIT, Verdict.LOW_BALANCE)


@dataclass(frozen=True)
class Rule:
    days: int
    # Per account and calendar month, in the account currency. Zero pays nothing.
    limit: Money


@dataclass(frozen=True)
class Ledger:
    """What the journal says about paying invoices of one account."""

    # Invoices paid by anyone, and every invoice autopay tried, whatever came of it:
    # after an error in the middle the bot cannot know whether money left.
    done: frozenset[str] = frozenset()
    # Invoices the owner said not to pay.
    declined: frozenset[str] = frozenset()
    # The last paid sum per server: the previous period.
    previous: Mapping[str, Money] = field(default_factory=dict)
    # What autopay took (or may have taken) this month.
    spent: Money | None = None


@dataclass(frozen=True)
class Decision:
    invoice: Invoice
    verdict: Verdict
    server: Server | None = None
    # The sum of the previous period the invoice was compared with.
    reference: Money | None = None
    due_at: datetime | None = None


def eligible(caps: Collection[Capability], server: Server) -> bool:
    """Can the owner mark this server for autopay: the hoster has no autorenewal of its
    own for it, and the plugin can read and pay invoices."""
    return (
        Capability.PAY_INVOICE in caps
        and Capability.INVOICES in caps
        and server.billing is BillingModel.PERIODIC
        and server.autorenew is Autorenew.NOT_AVAILABLE
    )


def month_start(now: datetime, tz: tzinfo) -> datetime:
    """Midnight of the first day of the current month in the owner's time zone."""
    local = now.astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def grew(amount: Money, reference: Money) -> bool:
    return amount.amount > reference.amount * (1 + GROWTH)


def decide(
    invoices: Iterable[Invoice],
    servers: Iterable[Server],
    marked: Collection[str],
    caps: Collection[Capability],
    rule: Rule,
    ledger: Ledger,
    balance: Money,
    now: datetime,
) -> list[Decision]:
    """What the rule does with each invoice, soonest first. Paying one lowers the balance
    and the rest of the limit for the next ones."""
    currency = balance.currency
    by_id = {s.external_id: s for s in servers}
    spent = ledger.spent or Money(amount=Decimal(0), currency=currency)
    left = balance
    decisions = []
    for invoice in sorted(invoices, key=lambda i: (_due(i, by_id) or now, i.id)):
        decision = _decide(invoice, by_id, marked, caps, rule, ledger, now)
        if decision.verdict is Verdict.PAY:
            amount = invoice.amount
            if spent.currency != currency or spent + amount > rule.limit:
                decision = _with(decision, Verdict.OVER_LIMIT)
            elif amount > left:
                decision = _with(decision, Verdict.LOW_BALANCE)
            else:
                spent += amount
                left -= amount
        decisions.append(decision)
    return decisions


def _decide(
    invoice: Invoice,
    servers: Mapping[str, Server],
    marked: Collection[str],
    caps: Collection[Capability],
    rule: Rule,
    ledger: Ledger,
    now: datetime,
) -> Decision:
    if invoice.status is not InvoiceStatus.UNPAID:
        return Decision(invoice, Verdict.NOT_UNPAID)
    # Top-ups, upgrades and invoices for several services have no single server.
    server = servers.get(invoice.server_id or "")
    if server is None:
        return Decision(invoice, Verdict.NOT_SERVICE)
    if server.external_id not in marked:
        return Decision(invoice, Verdict.NOT_MARKED, server)
    if not eligible(caps, server):
        return Decision(invoice, Verdict.NOT_ELIGIBLE, server)
    if invoice.amount.currency != rule.limit.currency:
        return Decision(invoice, Verdict.OTHER_CURRENCY, server)
    if invoice.id in ledger.done:
        return Decision(invoice, Verdict.ALREADY, server)
    if invoice.id in ledger.declined:
        return Decision(invoice, Verdict.DECLINED, server)
    due = _due(invoice, servers)
    if due is None:
        return Decision(invoice, Verdict.NO_DUE_DATE, server)
    if due - now > timedelta(days=rule.days):
        return Decision(invoice, Verdict.NOT_DUE, server, due_at=due)
    reference = ledger.previous.get(server.external_id) or server.price
    if reference is None or reference.currency != invoice.amount.currency:
        return Decision(invoice, Verdict.ASK_NEW, server, due_at=due)
    verdict = Verdict.ASK_GROWN if grew(invoice.amount, reference) else Verdict.PAY
    return Decision(invoice, verdict, server, reference, due)


def _due(invoice: Invoice, servers: Mapping[str, Server]) -> datetime | None:
    if invoice.due_at is not None:
        return invoice.due_at
    server = servers.get(invoice.server_id or "")
    return server.next_charge_at if server is not None else None


def _with(decision: Decision, verdict: Verdict) -> Decision:
    return Decision(decision.invoice, verdict, decision.server, decision.reference, decision.due_at)
