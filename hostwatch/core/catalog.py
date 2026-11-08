"""What a hoster sells and how the bot orders it (spec, decision 11).

A plugin's `catalog()` returns locations, plans, operating systems and prices; the bot walks
the owner through them and sends an `OrderRequest`. Prices are for the whole period and are
charged when the order is placed. An hourly plan (`HOURLY`) is charged from the balance
every hour instead; its price is given for a month of 730 hours, the way hosters show it.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from pydantic import Field

from hostwatch.core.models import Model, Money

# `period_months` of an hourly plan: the price is for 730 hours, taken hour by hour.
HOURLY = 0
HOURS_PER_MONTH = Decimal(730)


class Location(Model):
    id: str
    name: str


class OperatingSystem(Model):
    id: str
    name: str


class Price(Model):
    """The price of one period: for the whole period, or for 730 hours when `HOURLY`."""

    period_months: int = Field(ge=HOURLY)
    amount: Money


class Plan(Model):
    """A tariff in one location. `id` is unique in the catalog and stays short: it goes
    into button data together with the location and the OS."""

    id: str = Field(max_length=16)
    name: str
    location_id: str = Field(max_length=16)
    # «1 CPU · 2 GB RAM · 30 GB NVMe»
    specs: str = ""
    prices: tuple[Price, ...]
    # The OS this plan takes; empty means every OS of the catalog.
    os_ids: tuple[str, ...] = ()

    def price(self, period_months: int) -> Money | None:
        return next((p.amount for p in self.prices if p.period_months == period_months), None)

    @property
    def cheapest_month(self) -> Money:
        """For sorting: the lowest price of a month."""
        return min((monthly(p) for p in self.prices), key=lambda m: m.amount)


class Catalog(Model):
    locations: tuple[Location, ...]
    plans: tuple[Plan, ...]
    systems: tuple[OperatingSystem, ...]
    # The hoster puts the owner's SSH key on the new server.
    ssh_keys: bool = False
    # False until an order went through at this hoster for real: the bot warns the owner.
    tested: bool = True

    def plan(self, plan_id: str) -> Plan | None:
        return next((p for p in self.plans if p.id == plan_id), None)

    def location(self, location_id: str) -> Location | None:
        return next((loc for loc in self.locations if loc.id == location_id), None)

    def system(self, os_id: str) -> OperatingSystem | None:
        return next((s for s in self.systems if s.id == os_id), None)

    def plans_in(self, location_id: str) -> list[Plan]:
        found = [p for p in self.plans if p.location_id == location_id]
        return sorted(found, key=lambda p: (p.cheapest_month.amount, p.name))

    def systems_for(self, plan: Plan) -> list[OperatingSystem]:
        if not plan.os_ids:
            return list(self.systems)
        return [s for s in self.systems if s.id in plan.os_ids]

    def used_locations(self) -> list[Location]:
        """Locations that have at least one plan, in the catalog's order."""
        used = {p.location_id for p in self.plans}
        return [loc for loc in self.locations if loc.id in used]


class OrderRequest(Model):
    """One server to order. `price` is what the owner confirmed for the period; the order
    is refused when the hoster asks for another sum."""

    plan_id: str
    os_id: str
    period_months: int = Field(ge=HOURLY)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,30}$")
    price: Money
    # The owner's public key, for hosters that install it with the order.
    ssh_key: str | None = None


class OrderResult(Model):
    """What the hoster said. `detail` goes to the journal and must hold no secret.

    `password` is the root password when the hoster tells it right away; the bot shows it
    once and never stores it.
    """

    ok: bool
    detail: str = ""
    server_id: str | None = None
    # The hoster made an invoice instead of paying from the balance (Regxa).
    invoice_id: str | None = None
    password: str | None = Field(default=None, repr=False, exclude=True)


class OrderTemplate(Model):
    """«Order the same»: what the plugin could tell about an existing server. Every field
    may be missing; the bot asks for the rest."""

    location_id: str | None = None
    plan_id: str | None = None
    os_id: str | None = None
    period_months: int | None = Field(default=None, ge=HOURLY)


def monthly(price: Price) -> Money:
    if price.period_months == HOURLY:
        return price.amount
    return Money(amount=price.amount.amount / price.period_months, currency=price.amount.currency)


def first_charge(price: Money, period_months: int) -> Money:
    """What the order takes from the balance at once: the whole period, or one hour."""
    if period_months != HOURLY:
        return price
    hour = (price.amount / HOURS_PER_MONTH).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return Money(amount=hour, currency=price.currency)
