"""Ordering a Regxa Cloud VPS the way the cabinet's order page does (docs/research-orders.md).

NOT TESTED LIVE: no order was sent yet; the catalog says so and the bot warns the owner.

- catalog: `GET /api/cloudvps/order-plans` → `{data: {locations, plans, operatingSystems,
  billingTerms, planOSMap}}`. A plan row repeats for each of its locations, so a plan here is
  a row: `<id>-<location_id>`;
- price: `base_price × location multiplier × (1 − discount %) × months`, rounded to cents,
  as the page computes it; read again before the order;
- order: `POST /api/orders/cloud/create-order` with the catalog objects as they came and
  `paymentMethod: "stripe"`; with enough credit the site pays from it and answers
  `{success, useCredit, productIds}`, otherwise `{success, invoiceId}`;
- no SSH key at order time; the root password is `GET /api/services/vps/password/{vpsid}`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hostwatch.core.catalog import (
    Catalog,
    Location,
    OperatingSystem,
    OrderRequest,
    OrderResult,
    OrderTemplate,
    Plan,
    Price,
)
from hostwatch.core.errors import HosterError, LayoutChanged
from hostwatch.core.models import Money

if TYPE_CHECKING:
    from hostwatch.plugins.regxa import RegxaPlugin

AVAILABLE = "available"
ACTIVE = "active"
PAYMENT_METHOD = "stripe"
DETAIL_LIMIT = 200
CENT = Decimal("0.01")
# Sums come as JSON numbers or numeric strings.
Sum = Decimal


class Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class RawLocation(Answer):
    id: int
    country: str = ""
    city: str = ""
    status: str
    base_price_multiplier: Sum = Decimal(1)


class RawPlan(Answer):
    id: int
    name: str
    status: str
    location_id: int
    base_price: Sum
    cpu_cores: str = ""
    ram_gb: str = ""
    storage_gb: str = ""


class RawSystem(Answer):
    id: int
    name: str
    version: str = ""
    status: str
    price_adjustment: Sum = Decimal(0)


class RawTerm(Answer):
    id: int
    duration_months: int
    discount_percentage: Sum = Decimal(0)
    status: str


class OrderPlans(Answer):
    locations: list[RawLocation]
    plans: list[RawPlan]
    operatingSystems: list[RawSystem]
    billingTerms: list[RawTerm]
    planOSMap: dict[str, list[int]] = {}


class Created(Answer):
    success: bool
    useCredit: bool = False
    productIds: int | str | list[int | str] | None = None
    invoiceId: int | str | None = None
    error: str | None = None


class Password(Answer):
    password: str | None = Field(default=None, repr=False)


class Raw:
    """The answer of `order-plans`: parsed for the bot, and as it came for the order body."""

    def __init__(self, answer: Any) -> None:
        data = answer.get("data") if isinstance(answer, dict) else None
        if not isinstance(data, dict):
            raise LayoutChanged
        try:
            self.parsed = OrderPlans.model_validate(data)
        except ValidationError:
            raise LayoutChanged from None
        self.data = data

    def raw(self, key: str, match: dict[str, int]) -> dict[str, Any]:
        """The first object of list `key` with these field values, as the site sent it."""
        for item in self.data.get(key) or []:
            if isinstance(item, dict) and all(item.get(k) == v for k, v in match.items()):
                return item
        raise HosterError(f"{key}: not in the catalog any more")


async def catalog(plugin: RegxaPlugin) -> Catalog:
    raw = await _order_plans(plugin)
    data = raw.parsed
    currency = plugin.meta.currency
    locations = {loc.id: loc for loc in data.locations if loc.status == AVAILABLE}
    systems = _systems(data)
    plans = []
    for row in data.plans:
        location = locations.get(row.location_id)
        if row.status != AVAILABLE or location is None:
            continue
        prices = tuple(
            Price(period_months=term.duration_months, amount=_price(row, location, term, currency))
            for term in _terms(data)
        )
        allowed = set(data.planOSMap.get(str(row.id), []))
        plans.append(
            Plan(
                id=_plan_id(row),
                name=row.name.strip(),
                location_id=str(row.location_id),
                specs=" · ".join(x for x in (row.cpu_cores, row.ram_gb, row.storage_gb) if x),
                prices=prices,
                os_ids=tuple(str(s.id) for s in systems if not allowed or s.id in allowed),
            )
        )
    used = {p.location_id for p in plans}
    return Catalog(
        locations=tuple(
            Location(id=str(loc.id), name=", ".join(x for x in (loc.city, loc.country) if x))
            for loc in locations.values()
            if str(loc.id) in used
        ),
        plans=tuple(plans),
        systems=tuple(
            OperatingSystem(id=str(s.id), name=f"{s.name} {s.version}".strip()) for s in systems
        ),
        ssh_keys=False,
        tested=False,
    )


async def quote(plugin: RegxaPlugin, request: OrderRequest) -> Money:
    raw = await _order_plans(plugin)
    row, location, term = _pick(raw.parsed, request)
    return _price(row, location, term, plugin.meta.currency)


async def order(plugin: RegxaPlugin, request: OrderRequest) -> OrderResult:
    raw = await _order_plans(plugin)
    data = raw.parsed
    row, location, term = _pick(data, request)
    system = next((s for s in _systems(data) if str(s.id) == request.os_id), None)
    if system is None:
        raise HosterError(f"no OS {request.os_id}")
    allowed = set(data.planOSMap.get(str(row.id), []))
    body = {
        "selectedLocation": raw.raw("locations", {"id": location.id}),
        "selectedPlan": raw.raw("plans", {"id": row.id, "location_id": row.location_id}),
        "selectedOS": system.name,
        "selectedOSVersion": system.version,
        "selectedDuration": raw.raw("billingTerms", {"id": term.id}),
        "quantity": 1,
        "ipv6Enabled": False,
        "operatingSystems": [
            raw.raw("operatingSystems", {"id": s.id})
            for s in _systems(data)
            if not allowed or s.id in allowed
        ],
        "paymentMethod": PAYMENT_METHOD,
    }
    from hostwatch.plugins import regxa

    response = await plugin._request(
        "/api/orders/cloud/create-order",
        {},
        body,
        referer=f"{regxa.SITE}/dashboard/order/cloud",
    )
    regxa._raise_for_status(response)
    try:
        created = Created.model_validate_json(response.content)
    except ValidationError:
        raise LayoutChanged from None
    if not created.success:
        raise HosterError((created.error or "the order was refused")[:DETAIL_LIMIT])
    if created.useCredit and created.productIds is not None:
        ids = created.productIds if isinstance(created.productIds, list) else [created.productIds]
        server = str(ids[0]) if ids else None
        return OrderResult(ok=True, detail=f"paid with credit, service {server}", server_id=server)
    if created.invoiceId is not None:
        invoice = str(created.invoiceId)
        return OrderResult(ok=True, detail=f"invoice {invoice} issued", invoice_id=invoice)
    return OrderResult(ok=False, detail="neither a service nor an invoice in the answer")


async def template(plugin: RegxaPlugin, server_id: str) -> OrderTemplate:
    """The plan by its name when it is sold in one location only; the period of the service."""
    from hostwatch.plugins import regxa

    service = next((s for s in await plugin._list_services() if str(s.id) == server_id), None)
    if service is None:
        return OrderTemplate()
    period = regxa.PERIOD_MONTHS.get(service.billingCycle.lower())
    name = service.name.strip()
    rows = [r for r in (await _order_plans(plugin)).parsed.plans if r.name.strip() == name]
    if len(rows) != 1:
        return OrderTemplate(period_months=period)
    row = rows[0]
    return OrderTemplate(
        location_id=str(row.location_id), plan_id=_plan_id(row), period_months=period
    )


async def root_password(plugin: RegxaPlugin, server_id: str) -> str:
    from hostwatch.plugins import regxa

    service = next((s for s in await plugin._list_services() if str(s.id) == server_id), None)
    vps_id = service.field(regxa.VPS_FIELD) if service else None
    if vps_id is None or not vps_id.isdigit():
        raise HosterError(f"no VPS with id {server_id}")
    answer = await plugin._get(Password, f"/api/services/vps/password/{vps_id}")
    if not answer.password:
        raise HosterError("Regxa tells no password for this VPS")
    return answer.password


async def _order_plans(plugin: RegxaPlugin) -> Raw:
    await plugin._session()
    return Raw(await plugin._json("/api/cloudvps/order-plans"))


def _systems(data: OrderPlans) -> list[RawSystem]:
    """Available systems that cost nothing extra: the bot has no price for the extra."""
    return [s for s in data.operatingSystems if s.status == AVAILABLE and s.price_adjustment == 0]


def _terms(data: OrderPlans) -> list[RawTerm]:
    return sorted(
        (t for t in data.billingTerms if t.status == ACTIVE and t.duration_months > 0),
        key=lambda t: t.duration_months,
    )


def _plan_id(row: RawPlan) -> str:
    return f"{row.id}-{row.location_id}"


def _pick(data: OrderPlans, request: OrderRequest) -> tuple[RawPlan, RawLocation, RawTerm]:
    row = next(
        (r for r in data.plans if _plan_id(r) == request.plan_id and r.status == AVAILABLE), None
    )
    if row is None:
        raise HosterError("the plan is not sold any more")
    location = next(
        (x for x in data.locations if x.id == row.location_id and x.status == AVAILABLE), None
    )
    if location is None:
        raise HosterError("the location is closed")
    term = next((t for t in _terms(data) if t.duration_months == request.period_months), None)
    if term is None:
        raise HosterError(f"no term of {request.period_months} months")
    return row, location, term


def _price(row: RawPlan, location: RawLocation, term: RawTerm, currency: str) -> Money:
    discount = 1 - term.discount_percentage / 100
    amount = row.base_price * location.base_price_multiplier * discount * term.duration_months
    return Money(amount=amount.quantize(CENT, rounding=ROUND_HALF_UP), currency=currency)
