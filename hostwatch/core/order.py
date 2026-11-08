"""Ordering a server: the catalog cache, the monthly limit and the price check before the
order goes out (spec, decisions 5 and 11).

The order itself never repeats: a timeout or an unclear answer is reported to the owner,
who looks in the cabinet; a second attempt could buy a second server.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from decimal import Decimal, InvalidOperation

from hostwatch.core.catalog import Catalog, OrderRequest, OrderResult
from hostwatch.core.errors import HosterError
from hostwatch.core.logs import register_secrets
from hostwatch.core.models import Money
from hostwatch.core.plugin import SupportsOrder

CATALOG_TTL = 3600.0
# How long the message with a root password stays in the chat.
PASSWORD_TTL = 300.0
SSH_KEY = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com)"
    r" [A-Za-z0-9+/]{40,}={0,3}( [^\r\n]{0,100})?$"
)


class PriceChanged(HosterError):
    """The hoster asks for another sum than the owner confirmed."""

    def __init__(self, now: Money, confirmed: Money) -> None:
        super().__init__(f"the price is {now} now, not {confirmed}")
        self.now = now


@dataclass
class _Cached:
    catalog: Catalog
    at: float


@dataclass
class CatalogCache:
    """Catalogs by account for an hour: the hosters' catalogs change rarely and some are
    slow to read. The price itself is asked again right before the confirmation."""

    ttl: float = CATALOG_TTL
    clock: Callable[[], float] = time.monotonic
    _items: dict[int, _Cached] = field(default_factory=dict)

    async def get(self, account_id: int, load: Callable[[], Awaitable[Catalog]]) -> Catalog:
        cached = self._items.get(account_id)
        if cached is not None and self.clock() - cached.at < self.ttl:
            return cached.catalog
        catalog = await load()
        self._items[account_id] = _Cached(catalog, self.clock())
        return catalog

    def forget(self, account_id: int) -> None:
        self._items.pop(account_id, None)


@dataclass(frozen=True)
class Draft:
    """An order waiting for the owner's «Yes». `what` names the plan, location and OS."""

    account_id: int
    request: OrderRequest
    what: str
    # The owner said «Yes» once more because the order goes above the monthly limit.
    above_limit_confirmed: bool = False


async def place(plugin: SupportsOrder, request: OrderRequest) -> OrderResult:
    """Ask the price once more and order only if it is still the confirmed one."""
    now = await plugin.quote(request)
    if now != request.price:
        raise PriceChanged(now, request.price)
    result = await plugin.order(request)
    if result.password:
        # Whatever logs the hoster's answer later masks the password.
        register_secrets([result.password])
    return result


def month_start(now: datetime, tz: tzinfo) -> datetime:
    local = now.astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class LimitCheck:
    """The month's orders against the limit, in the owner's currency.

    `spent` or `this_order` is None when a sum could not be converted (no exchange rate):
    then the order counts as above the limit, so it takes the second confirmation.
    """

    limit: Money
    spent: Money | None
    this_order: Money | None

    @property
    def over(self) -> bool:
        if self.spent is None or self.this_order is None:
            return True
        return self.spent + self.this_order > self.limit


def check_limit(
    limit: Money,
    spent: Iterable[Money],
    this_order: Money,
    convert: Callable[[Money, str], Money | None],
) -> LimitCheck:
    currency = limit.currency
    total: Money | None = Money(amount=Decimal(0), currency=currency)
    for item in spent:
        converted = item if item.currency == currency else convert(item, currency)
        total = None if converted is None or total is None else total + converted
    order = this_order if this_order.currency == currency else convert(this_order, currency)
    return LimitCheck(limit=limit, spent=total, this_order=order)


def shortfall(balance: Money, charge: Money) -> Money | None:
    """How much is missing on the balance for the order; None if enough is there."""
    if balance.currency != charge.currency or balance >= charge:
        return None
    return charge - balance


def server_name(now: datetime) -> str:
    """A name that is a valid hostname everywhere: «hw-2611081530»."""
    return f"hw-{now:%y%m%d%H%M}"


def parse_limit(text: str) -> Decimal | None:
    """«0», «50», «12,5» → a sum ≥ 0 with at most two decimals; else None."""
    cleaned = text.strip().replace(",", ".").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        return None
    return amount


def parse_ssh_key(text: str) -> str | None:
    """One OpenSSH public key line, or None. A private key is never accepted."""
    key = " ".join(text.split())
    return key if SSH_KEY.match(key) else None
