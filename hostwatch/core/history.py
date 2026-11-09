"""What changed between two polls: server events and money moves (1.1, part 4).

Pure functions; the poller stores what they return.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from hostwatch.core.models import Balance, Invoice, InvoiceStatus, Money, Server


class ServerEventKind(StrEnum):
    APPEARED = "appeared"
    REMOVED = "removed"
    PLAN = "plan"
    PRICE = "price"
    PERIOD = "period"
    IP = "ip"
    STATUS = "status"
    AUTORENEW = "autorenew"


class MoneyKind(StrEnum):
    TOPUP = "topup"
    CHARGE = "charge"


class MoneySource(StrEnum):
    # A change of the balance the bot saw between two reads.
    OBSERVED = "observed"
    # A record the hoster keeps (a charge from the balance, tied to a server).
    HOSTER = "hoster"


@dataclass(frozen=True)
class ServerChange:
    server_id: str
    kind: ServerEventKind
    old: str | None
    new: str | None


@dataclass(frozen=True)
class MoneyMove:
    at: datetime
    kind: MoneyKind
    # Always positive; `kind` gives the direction.
    amount: Money
    source: MoneySource
    server_id: str | None = None
    external_id: str | None = None
    description: str = ""


def _price(server: Server) -> str | None:
    return None if server.price is None else f"{server.price.amount} {server.price.currency}"


def _period(server: Server) -> str | None:
    return None if server.period_months is None else str(server.period_months)


FIELDS: tuple[tuple[ServerEventKind, Callable[[Server], str | None]], ...] = (
    (ServerEventKind.PLAN, lambda s: s.plan),
    (ServerEventKind.PRICE, _price),
    (ServerEventKind.PERIOD, _period),
    (ServerEventKind.IP, lambda s: s.ip),
    (ServerEventKind.STATUS, lambda s: s.status.value),
    (ServerEventKind.AUTORENEW, lambda s: s.autorenew.value),
)


def server_changes(old: Sequence[Server], new: Sequence[Server]) -> list[ServerChange]:
    """Servers that appeared or went away, and the fields that changed on the rest."""
    before = {s.external_id: s for s in old}
    after = {s.external_id: s for s in new}
    changes: list[ServerChange] = []
    for sid, server in after.items():
        previous = before.get(sid)
        if previous is None:
            changes.append(ServerChange(sid, ServerEventKind.APPEARED, None, server.name))
            continue
        for kind, read in FIELDS:
            was, now = read(previous), read(server)
            if was != now:
                changes.append(ServerChange(sid, kind, was, now))
    for sid, server in before.items():
        if sid not in after:
            changes.append(ServerChange(sid, ServerEventKind.REMOVED, server.name, None))
    return changes


def balance_move(previous: Balance | None, current: Balance, at: datetime) -> MoneyMove | None:
    """A rise is a top-up, a fall is a charge; nothing when the currency changed."""
    if previous is None or previous.money.currency != current.money.currency:
        return None
    delta = current.money.amount - previous.money.amount
    if delta == 0:
        return None
    kind = MoneyKind.TOPUP if delta > 0 else MoneyKind.CHARGE
    amount = Money(amount=abs(delta), currency=current.money.currency)
    return MoneyMove(at=at, kind=kind, amount=amount, source=MoneySource.OBSERVED)


def hoster_charges(invoices: Sequence[Invoice], fallback_at: datetime) -> list[MoneyMove]:
    """Paid charges the hoster ties to a server (Aeza's balance transactions).

    Paid invoices without a server are left out: at WHMCS hosters they may be top-ups.
    """
    moves = []
    for invoice in invoices:
        if invoice.status is not InvoiceStatus.PAID or invoice.server_id is None:
            continue
        if invoice.amount.amount <= Decimal(0):
            continue
        moves.append(
            MoneyMove(
                at=invoice.due_at or fallback_at,
                kind=MoneyKind.CHARGE,
                amount=invoice.amount,
                source=MoneySource.HOSTER,
                server_id=invoice.server_id,
                external_id=invoice.id,
                description=invoice.description[:100],
            )
        )
    return moves
