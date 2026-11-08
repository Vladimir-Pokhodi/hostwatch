"""Ordering a VPS at Aeza with the API key (docs/research-orders.md).

- catalog: `GET services/products` (VPS and Hi-CPU), `services/groups` (a product group's
  parent with `role: "location"` is its location), `services/operating-systems`;
- price right before the order: `GET services/orders/price` → `totalPrice` in cents and
  `availability.status`;
- order: `POST services/orders` with `method: "balance"`, the body the cabinet's order
  wizard sends; not sent to the live API yet;
- the owner's key: found in `GET services/ssh-keys` by its body, else `POST services/ssh-keys`;
- root password: `secureParameters.password` of `GET services/{id}`.

Products with the `custom` tag are built from resources and left out, and so are Windows
images, whose licence the catalog does not price.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from hostwatch.core.catalog import (
    HOURLY,
    HOURS_PER_MONTH,
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
    from hostwatch.plugins.aeza import AezaPlugin

KINDS = {"vps", "hicpu"}
# Payment terms and their months; `hour` is the hourly plan.
TERMS = {"hour": HOURLY, "month": 1, "quarter_year": 3, "half_year": 6, "year": 12}
MONTHS_TERM = {months: term for term, months in TERMS.items()}
LOCATION_ROLE = "location"
CUSTOM_TAG = "custom"
KEY_NAME = "hostwatch"
DETAIL_LIMIT = 200


class Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Kind(Answer):
    slug: str


class Parameters(Answer):
    cpu: Decimal = Decimal(0)
    ram: Decimal = Decimal(0)
    rom: Decimal = Decimal(0)


class Prices(Answer):
    base: dict[str, Decimal]


class Product(Answer):
    id: int
    name: str
    groupId: int
    type: Kind
    parameters: Parameters = Parameters()
    isAvailable: bool
    isPublic: bool
    tags: list[str] = []
    prices: Prices


class Products(Answer):
    """All products: domains and others have fields of their own (no group, other
    parameters), so only VPS items are read as `Product`."""

    items: list[dict[str, Any]]
    total: int


class Group(Answer):
    id: int
    role: str | None = None
    parentId: int | None = None
    name: str
    payload: dict[str, Any] = {}

    @property
    def disabled(self) -> bool:
        return bool(self.payload.get("isDisabled"))


class Groups(Answer):
    items: list[Group]


class System(Answer):
    slug: str
    name: str
    enabled: bool
    supportsSshKeys: bool = False


class Systems(Answer):
    items: list[System]


# The list comes bare or as `{items}`, depending on the endpoint's version.
SYSTEMS: TypeAdapter[list[System] | Systems] = TypeAdapter(list[System] | Systems)


class Availability(Answer):
    status: str


class OrderPrice(Answer):
    totalPrice: Decimal
    availability: Availability | None = None


class SshKey(Answer):
    id: int
    publicKey: str


class SshKeys(Answer):
    items: list[SshKey]


class Secure(Answer):
    password: str | None = Field(default=None, repr=False)


async def catalog(plugin: AezaPlugin) -> Catalog:
    currency = (await plugin._me()).currency.upper()
    products = await _products(plugin)
    groups = {g.id: g for g in (await plugin._get(Groups, "services/groups")).items}
    systems = await _systems(plugin)
    plans: list[Plan] = []
    locations: dict[int, Group] = {}
    for product in products:
        location = _location(product, groups)
        if location is None or not _sold(product):
            continue
        prices = _prices(product, currency)
        if not prices:
            continue
        locations[location.id] = location
        group = groups[product.groupId]
        params = product.parameters
        plans.append(
            Plan(
                id=str(product.id),
                name=f"{product.name} · {group.name}",
                location_id=str(location.id),
                specs=f"{params.cpu:g} CPU · {params.ram:g} GB RAM · {params.rom:g} GB",
                prices=prices,
            )
        )
    return Catalog(
        locations=tuple(Location(id=str(g.id), name=g.name) for g in locations.values()),
        plans=tuple(plans),
        systems=tuple(OperatingSystem(id=s.slug, name=s.name) for s in systems),
        ssh_keys=True,
    )


async def quote(plugin: AezaPlugin, request: OrderRequest) -> Money:
    currency = (await plugin._me()).currency.upper()
    params = {
        "productId": _number(request.plan_id),
        "paymentTerm": _term(request.period_months),
        "isBackupsEnabled": "false",
        "termCount": 1,
    }
    price = await plugin._get(OrderPrice, "services/orders/price", **params)
    if price.availability is not None and price.availability.status != "ok":
        raise HosterError(f"not available now: {price.availability.status}")
    return _money(price.totalPrice, currency, request.period_months)


async def order(plugin: AezaPlugin, request: OrderRequest) -> OrderResult:
    systems = {s.slug: s for s in await _systems(plugin)}
    system = systems.get(request.os_id)
    if system is None:
        raise HosterError(f"no OS {request.os_id}")
    parameters: dict[str, Any] = {"os": system.slug}
    if request.ssh_key and system.supportsSshKeys:
        parameters["sshKeyIds"] = [await _key_id(plugin, request.ssh_key)]
    body = {
        "orders": [
            {
                "count": 1,
                "term": _term(request.period_months),
                "termCount": 1,
                "name": request.name,
                "productId": _number(request.plan_id),
                "parameters": parameters,
                "autoProlong": True,
                "backups": False,
            }
        ],
        "method": "balance",
    }
    response = await plugin._send("POST", "services/orders", json=body)
    answer = _answer(response)
    server_id = _created(answer)
    transaction = _first(answer, "id")
    detail = f"HTTP {response.status_code}"
    if transaction is not None:
        detail += f": transaction {transaction}"
    if server_id is not None:
        detail += f", service {server_id}"
    return OrderResult(ok=True, detail=detail[:DETAIL_LIMIT], server_id=server_id)


async def template(plugin: AezaPlugin, server_id: str) -> OrderTemplate:
    service = await plugin._service(server_id)
    period = TERMS.get(service.paymentTerm)
    products = await _products(plugin)
    found = [p for p in products if p.name == service.productName]
    if len(found) != 1:
        return OrderTemplate(period_months=period)
    groups = {g.id: g for g in (await plugin._get(Groups, "services/groups")).items}
    location = _location(found[0], groups)
    return OrderTemplate(
        location_id=None if location is None else str(location.id),
        plan_id=str(found[0].id),
        period_months=period,
    )


async def root_password(plugin: AezaPlugin, server_id: str) -> str:
    if not server_id.isdigit():
        raise HosterError(f"bad service id {server_id!r}")
    response = await plugin._send("GET", f"services/{server_id}")
    answer = _answer(response)
    holder = answer.get("data") if isinstance(answer.get("data"), dict) else answer
    secure = holder.get("secureParameters") if isinstance(holder, dict) else None
    try:
        password = Secure.model_validate(secure).password if isinstance(secure, dict) else None
    except ValidationError:
        raise LayoutChanged from None
    if not password:
        raise HosterError("Aeza tells no password for this service")
    return password


async def _products(plugin: AezaPlugin) -> list[Product]:
    products = await plugin._get(Products, "services/products")
    if products.total > len(products.items):
        raise HosterError("the product list is paged; the bot reads one page")
    kinds = [
        item
        for item in products.items
        if isinstance(item.get("type"), dict) and item["type"].get("slug") in KINDS
    ]
    try:
        return [Product.model_validate(item) for item in kinds]
    except ValidationError:
        raise LayoutChanged from None


async def _systems(plugin: AezaPlugin) -> list[System]:
    from hostwatch.plugins import aeza

    response = await plugin._send("GET", "services/operating-systems")
    aeza._raise_for_status(response)
    try:
        answer = SYSTEMS.validate_json(response.content)
    except ValidationError:
        raise LayoutChanged from None
    found = answer.items if isinstance(answer, Systems) else answer
    return [s for s in found if s.enabled and not s.slug.startswith("windows")]


def _sold(product: Product) -> bool:
    return product.isAvailable and product.isPublic and CUSTOM_TAG not in product.tags


def _location(product: Product, groups: dict[int, Group]) -> Group | None:
    """The product's location, if neither its group nor the location is closed."""
    group = groups.get(product.groupId)
    if group is None or group.disabled or group.parentId is None:
        return None
    location = groups.get(group.parentId)
    if location is None or location.role != LOCATION_ROLE or location.disabled:
        return None
    return location


def _prices(product: Product, currency: str) -> tuple[Price, ...]:
    return tuple(
        Price(period_months=months, amount=_money(product.prices.base[term], currency, months))
        for term, months in TERMS.items()
        if product.prices.base.get(term, Decimal(0)) > 0
    )


def _money(cents: Decimal, currency: str, months: int) -> Money:
    """Cents of the term as Money; an hourly price becomes the price of 730 hours."""
    amount = cents / 100
    if months == HOURLY:
        amount *= HOURS_PER_MONTH
    return Money(amount=amount.quantize(Decimal("0.01")), currency=currency)


def _term(months: int) -> str:
    term = MONTHS_TERM.get(months)
    if term is None:
        raise HosterError(f"no payment term of {months} months")
    return term


def _number(value: str) -> int:
    if not value.isdigit():
        raise HosterError(f"bad id {value!r}")
    return int(value)


async def _key_id(plugin: AezaPlugin, key: str) -> int:
    """The id of the owner's key in the account; the key is added once if it is not there."""
    wanted = _key_body(key)
    keys = (await plugin._get(SshKeys, "services/ssh-keys")).items
    found = next((k for k in keys if _key_body(k.publicKey) == wanted), None)
    if found is not None:
        return found.id
    body = {"name": KEY_NAME, "publicKey": key, "autoAssign": False}
    answer = _answer(await plugin._send("POST", "services/ssh-keys", json=body))
    created = _first(answer, "id")
    if created is None or not created.isdigit():
        raise LayoutChanged
    return int(created)


def _key_body(key: str) -> str:
    return " ".join(key.split()[:2])


def _answer(response: httpx.Response) -> dict[str, Any]:
    """The JSON body of a write; an HTTP error or `{"error": ...}` raises, as in `_done`."""
    from hostwatch.plugins import aeza

    aeza._raise_for_status(response)
    message = aeza._error_in_body(response)
    if message is not None:
        raise HosterError(message[:DETAIL_LIMIT] or "Aeza refused")
    try:
        body = response.json()
    except ValueError:
        raise LayoutChanged from None
    if not isinstance(body, dict):
        raise LayoutChanged
    return body


def _first(answer: dict[str, Any], key: str) -> str | None:
    """`key` of the answer, of its `data`, or of its first transaction."""
    holders: list[Any] = [answer, answer.get("data")]
    transactions = answer.get("transactions")
    if isinstance(transactions, list) and transactions:
        holders.append(transactions[0])
    for holder in holders:
        if isinstance(holder, dict):
            value = holder.get(key)
            if isinstance(value, int | str) and not isinstance(value, bool) and str(value):
                return str(value)
    return None


def _created(answer: dict[str, Any]) -> str | None:
    """The new service id: `serviceId`, or the first of `orderInfo.createdServiceIds`."""
    found = _first(answer, "serviceId")
    if found is not None:
        return found
    holders: list[Any] = [answer, answer.get("data")]
    transactions = answer.get("transactions")
    if isinstance(transactions, list):
        holders.extend(transactions)
    for holder in holders:
        info = holder.get("orderInfo") if isinstance(holder, dict) else None
        ids = info.get("createdServiceIds") if isinstance(info, dict) else None
        if isinstance(ids, list) and ids and isinstance(ids[0], int | str):
            return str(ids[0])
    return None
