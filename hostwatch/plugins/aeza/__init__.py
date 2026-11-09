"""Aeza (aeza.net) through its API key: servers, balance, load, charges, top-up invoices,
power, autorenewal and renewal.

A top-up invoice is `POST /billing/invoices`. For SBP it carries a ЮKassa page, which
`core.payments.yookassa` turns into a `qr.nspk.ru` link; for crypto (`pawnow:*`) it carries
the address, the amount in the coin and the QR text right away.

Control requests are the ones the cabinet's own scripts send (my.aeza.net, read 2026-11-08);
none of them was sent to a live server yet:
- power: `POST services/{id}/ctl/resume`, `ctl/suspend {"force": false}`,
  `ctl/restart {"force": false}`;
- autorenewal: `PATCH services/{id} {"autoProlong": bool}`;
- renewal from the balance: `GET services/{id}/prices` → `{"prices": {term: cents}}`, then
  `POST services/{id}/prolong {"method": "balance", "paymentTerm": term, "count": 1}`.
Aeza may answer 2xx with `{"error": {...}}`; the cabinet treats that as a failure, and so
does the plugin.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from hostwatch.core.catalog import Catalog, OrderRequest, OrderResult, OrderTemplate
from hostwatch.core.errors import (
    AuthExpired,
    Blocked,
    HosterError,
    LayoutChanged,
    RateLimited,
)
from hostwatch.core.models import (
    ActionResult,
    Autorenew,
    Balance,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Payment,
    PowerAction,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    TopupKind,
    TopupMethod,
    Usage,
)
from hostwatch.core.payments import yookassa
from hostwatch.core.plugin import (
    ACTION_CAPABILITY,
    ApiKeyAuth,
    Plugin,
    PluginContext,
    PluginMeta,
    capabilities,
)
from hostwatch.plugins.aeza import api, ordering

BASE_URL = "https://my.aeza.net/api/v2/"
PAGE = 50
# How many latest balance transactions `invoices()` reads; about a year for a few servers.
TRANSACTIONS = 50
USAGE_WINDOW = timedelta(minutes=15)
USAGE_RESOLUTION = 60  # seconds per point
UNIX_EPOCH_YEAR = 1970
SUBCENT = Decimal("0.00001")
# Aeza limits requests per second; a short `Retry-After` is waited out this many times.
SHORT_LIMIT_RETRIES = 4
MAX_SHORT_WAIT = 5.0

KINDS = {
    "vps": ServerKind.VPS,
    "hicpu": ServerKind.VPS,
    "storage": ServerKind.VPS,
    "dedicated": ServerKind.DEDICATED,
}
STATUSES = {
    "active": ServerStatus.RUNNING,
    "stopped": ServerStatus.STOPPED,
    "suspended": ServerStatus.SUSPENDED,
    "unpaid": ServerStatus.UNPAID,
}
PERIOD_MONTHS = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}
# Service capability -> what it lets the owner do. Renewal is open to every service.
SERVICE_ACTIONS = {
    "ctl": {ServerAction.START, ServerAction.STOP},
    "ctl.restart": {ServerAction.RESTART},
}
# Periodic services; an hourly one is paid from the balance every hour anyway.
PERIODIC_ACTIONS = {ServerAction.AUTORENEW, ServerAction.RENEW}
POWER_CALLS: dict[PowerAction, tuple[str, dict[str, Any] | None]] = {
    PowerAction.START: ("ctl/resume", None),
    PowerAction.STOP: ("ctl/suspend", {"force": False}),
    PowerAction.RESTART: ("ctl/restart", {"force": False}),
}
DETAIL_LIMIT = 200
TRANSACTION_STATUSES = {"performed": InvoiceStatus.PAID, "cancelled": InvoiceStatus.CANCELLED}
REPLENISHMENT = "replenishment"
SBP_METHOD = "yookassa:sbp"
CRYPTO_PREFIX = "pawnow:"

A = TypeVar("A", bound=BaseModel)


class AezaPlugin(Plugin):
    meta = PluginMeta(
        id="aeza",
        name="Aeza",
        site="https://my.aeza.net",
        auth=(ApiKeyAuth(fields=("api_key",)),),
        currency="EUR",
    )
    record_names = ("items.*.name", "items.*.payload.name")
    topup_page = "https://my.aeza.net/billing"

    def __init__(self, ctx: PluginContext) -> None:
        super().__init__(ctx)
        self._account: api.Account | None = None

    async def servers(self) -> list[Server]:
        currency = (await self._me()).currency
        services = await self._get(api.Services, "services", offset=0, limit=PAGE)
        if services.total > len(services.items):
            raise HosterError(f"more than {PAGE} services; paging is not supported yet")
        allowed = {
            action
            for action, capability in ACTION_CAPABILITY.items()
            if capability in capabilities(type(self))
        }
        return [
            self._server(service, currency, allowed)
            for service in services.items
            if service.typeSlug in KINDS
        ]

    async def balance(self) -> Balance:
        account = await self._me()
        return Balance(money=_money(account.balance, account.currency))

    async def usage(self, server_id: str) -> Usage:
        now = datetime.now(UTC)
        params = {
            "resolution": USAGE_RESOLUTION,
            "fromDate": _iso(now - USAGE_WINDOW),
            "toDate": _iso(now),
        }
        metrics = ("cpu_usage", "memory_usage", "net_read_mbits", "net_write_mbits")
        cpu, ram, net_in, net_out = await asyncio.gather(
            *(
                self._get(api.Stats, f"services/{server_id}/stats/{metric}", **params)
                for metric in metrics
            )
        )
        times = [t for stats in (cpu, ram) for t, value in stats.data if value is not None]
        return Usage(
            server_id=server_id,
            measured_at=datetime.fromtimestamp(max(times), UTC) if times else now,
            cpu_percent=_average(cpu),
            ram_percent=_average(ram),
            net_in_mbits=_average(net_in),
            net_out_mbits=_average(net_out),
        )

    async def invoices(self) -> list[Invoice]:
        """Charges from the balance, newest first. Aeza sorts transactions oldest first."""
        currency = (await self._me()).currency
        total = (await self._get(api.Transactions, "billing/transactions", offset=0, limit=1)).total
        if total == 0:
            return []
        page = await self._get(
            api.Transactions,
            "billing/transactions",
            offset=max(0, total - TRANSACTIONS),
            limit=TRANSACTIONS,
        )
        charges = [
            _charge(item, currency)
            for item in page.items
            if item.type != REPLENISHMENT and item.amount < 0
        ]
        return sorted(charges, key=lambda c: c.due_at or datetime.min.replace(tzinfo=UTC))[::-1]

    async def topup_methods(self) -> list[TopupMethod]:
        """SBP and every coin of `pawnow` the API lists (13 on 2026-11-09: USDT on four
        networks, USDC on two, BTC, ETH, LTC, TRX, TON, SOL, BNB). Aeza lists cards too,
        the bot does not offer them."""
        currency = (await self._me()).currency
        methods = await self._get(api.PaymentMethods, "billing/payment-methods")
        return [
            TopupMethod(
                id=method.slug,
                name=method.name,
                kind=TopupKind.SBP if method.slug == SBP_METHOD else TopupKind.CRYPTO,
                minimum=_money(Decimal(method.min), currency),
                **_coin(method),
            )
            for method in methods.items
            if method.slug == SBP_METHOD or method.slug.startswith(CRYPTO_PREFIX)
        ]

    async def topup(self, amount: Money, method_id: str) -> Payment:
        currency = (await self._me()).currency.upper()
        if amount.currency != currency:
            raise HosterError(f"the account is in {currency}")
        cents = amount.amount * 100
        if cents != cents.to_integral_value() or cents <= 0:
            raise HosterError("the amount must be in whole cents")
        response = await self._send(
            "POST", "billing/invoices", json={"method": method_id, "amount": int(cents)}
        )
        _raise_for_status(response)
        try:
            invoice = api.CreatedInvoice.model_validate_json(response.content)
        except ValidationError:
            raise LayoutChanged from None
        payload = invoice.payload
        paid = _money(Decimal(invoice.amount), currency)
        if payload.address:
            return Payment(
                id=str(invoice.id),
                amount=paid,
                method=method_id,
                created_at=invoice.createdAt,
                address=payload.address,
                qr_data=payload.qr or payload.address,
                pay_amount=payload.amount,
                pay_currency=payload.currency,
            )
        if not payload.url:
            raise LayoutChanged
        url, qr = await yookassa.link_or_page(self.ctx.http, payload.url)
        return Payment(
            id=str(invoice.id),
            amount=paid,
            method=method_id,
            created_at=invoice.createdAt,
            url=url,
            qr_data=qr,
        )

    async def power(self, server_id: str, action: PowerAction) -> ActionResult:
        path, body = POWER_CALLS[action]
        response = await self._send("POST", f"services/{_id(server_id)}/{path}", json=body)
        return _done(response)

    async def set_autorenew(self, server_id: str, enabled: bool) -> ActionResult:
        response = await self._send(
            "PATCH", f"services/{_id(server_id)}", json={"autoProlong": enabled}
        )
        return _done(response)

    async def renew_price(self, server_id: str) -> Money:
        service = await self._service(server_id)
        return await self._renewal_price(service)

    async def renew(self, server_id: str, price: Money) -> ActionResult:
        service = await self._service(server_id)
        now = await self._renewal_price(service)
        if now != price:
            raise HosterError(f"the renewal costs {now} now, not {price}")
        body = {"method": "balance", "paymentTerm": service.paymentTerm, "count": 1}
        response = await self._send("POST", f"services/{service.id}/prolong", json=body)
        return _done(response)

    async def catalog(self) -> Catalog:
        return await ordering.catalog(self)

    async def quote(self, request: OrderRequest) -> Money:
        return await ordering.quote(self, request)

    async def order(self, request: OrderRequest) -> OrderResult:
        return await ordering.order(self, request)

    async def order_template(self, server_id: str) -> OrderTemplate:
        return await ordering.template(self, server_id)

    async def root_password(self, server_id: str) -> str:
        return await ordering.root_password(self, server_id)

    async def _service(self, server_id: str) -> api.Service:
        services = await self._get(api.Services, "services", offset=0, limit=PAGE)
        found = next((s for s in services.items if str(s.id) == server_id), None)
        if found is None:
            raise HosterError(f"no service {server_id}")
        return found

    async def _renewal_price(self, service: api.Service) -> Money:
        if service.paymentTerm == "hour":
            raise HosterError("an hourly service is not renewed")
        currency = (await self._me()).currency
        prices = await self._get(api.RenewalPrices, f"services/{service.id}/prices")
        cents = prices.prices.get(service.paymentTerm)
        if cents is None or cents <= 0:
            raise LayoutChanged
        return _money(cents, currency)

    async def _me(self) -> api.Account:
        if self._account is None:
            self._account = await self._get(api.Account, "accounts/me")
        return self._account

    async def _get(self, model: type[A], path: str, **params: Any) -> A:
        for _ in range(SHORT_LIMIT_RETRIES):
            response = await self._request(path, params)
            wait = _short_wait(response)
            if wait is None:
                break
            await asyncio.sleep(wait)
        _raise_for_status(response)
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise LayoutChanged from None

    async def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        return await self._send("GET", path, params=params)

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self.ctx.http.request(
                method,
                BASE_URL + path,
                params=params,
                json=json,
                headers={"X-API-Key": self.ctx.secrets["api_key"], "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("Aeza is unreachable") from None

    @staticmethod
    def _server(service: api.Service, currency: str, allowed: set[ServerAction]) -> Server:
        hourly = service.paymentTerm == "hour"
        actions = set() if hourly else set(PERIODIC_ACTIONS)
        for capability in service.capabilities:
            actions |= SERVICE_ACTIONS.get(capability, set())
        return Server(
            external_id=str(service.id),
            name=service.name,
            kind=KINDS[service.typeSlug],
            ip=service.ip,
            plan=service.productName,
            status=STATUSES.get(service.status, ServerStatus.UNKNOWN),
            raw_status=service.status,
            price=_money(service.price, currency),
            period_months=None if hourly else PERIOD_MONTHS[service.paymentTerm],
            next_charge_at=service.expiresAt,
            autorenew=Autorenew.ON if service.autoProlong else Autorenew.OFF,
            billing=BillingModel.HOURLY if hourly else BillingModel.PERIODIC,
            actions=frozenset(actions & allowed),
        )


def _coin(method: api.PaymentMethod) -> dict[str, str | None]:
    """«pawnow:usdt_trc20», «USDT (TRC20)» → coin USDT, network TRC20; «Bitcoin» has no
    network in its name."""
    if not method.slug.startswith(CRYPTO_PREFIX):
        return {}
    coin = method.slug.removeprefix(CRYPTO_PREFIX).split("_")[0].upper()
    found = re.search(r"\(([^()]+)\)\s*$", method.name)
    return {"coin": coin[:16] or None, "network": found.group(1) if found else None}


def _id(server_id: str) -> int:
    """Service ids are numbers; anything else must not reach a URL path."""
    if not server_id.isdigit():
        raise HosterError(f"bad service id {server_id!r}")
    return int(server_id)


def _done(response: httpx.Response) -> ActionResult:
    """A control answer: an HTTP error or an `{"error": ...}` body raise, anything else is
    success. The body goes to the journal, shortened."""
    _raise_for_status(response)
    message = _error_in_body(response)
    if message is not None:
        raise HosterError(message[:DETAIL_LIMIT] or "Aeza refused")
    text = response.text.strip()
    detail = f"HTTP {response.status_code}" + (f": {text[:DETAIL_LIMIT]}" if text else "")
    return ActionResult(ok=True, detail=detail)


def _error_in_body(response: httpx.Response) -> str | None:
    """`{"error": {"message", "slug"}}`, also under `data`, the way the cabinet reads it."""
    try:
        body = response.json()
    except ValueError:
        return None
    for holder in (body, body.get("data") if isinstance(body, dict) else None):
        if isinstance(holder, dict) and isinstance(holder.get("error"), dict):
            error = holder["error"]
            text = error.get("message") or error.get("slug") or ""
            return text if isinstance(text, str) else ""
    return None


def _short_wait(response: httpx.Response) -> float | None:
    """Seconds to wait before a retry, when Aeza asks for a short pause.

    A poll of four servers already makes more requests a second than Aeza allows, and it
    answers 429 with `Retry-After: 1`. Longer pauses stay errors and stop the polling.
    """
    if response.status_code != 429:
        return None
    try:
        wait = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return wait if 0 <= wait <= MAX_SHORT_WAIT else None


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    if code < 400:
        return
    if code == 401:
        raise AuthExpired
    if code == 403:
        raise Blocked
    if code == 429:
        raise RateLimited
    try:
        message = api.Problem.model_validate_json(response.content).message
    except ValidationError:
        message = ""
    raise HosterError(f"HTTP {code}" + (f": {message[:200]}" if message else ""))


def _money(cents: Decimal, currency: str) -> Money:
    # Transactions come as floats with fractions of a cent (-487.79999999999995).
    return Money(amount=(cents / 100).quantize(SUBCENT), currency=currency.upper())


def _charge(item: api.Transaction, currency: str) -> Invoice:
    # A cancelled transaction was never performed and has the Unix epoch as its date.
    performed = item.performedAt.year > UNIX_EPOCH_YEAR
    return Invoice(
        id=str(item.id),
        amount=_money(-item.amount, currency),
        status=TRANSACTION_STATUSES.get(item.status, InvoiceStatus.UNKNOWN),
        description=item.type,
        due_at=item.performedAt if performed else item.createdAt,
        server_id=None if item.serviceId is None else str(item.serviceId),
    )


def _average(stats: api.Stats) -> float | None:
    values = [value for _, value in stats.data if value is not None]
    return round(sum(values) / len(values), 2) if values else None


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
