"""Aeza (aeza.net) through its API key. Read-only for now: servers, balance, load, charges."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import (
    Autorenew,
    Balance,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    Usage,
)
from hostwatch.core.plugin import (
    ACTION_CAPABILITY,
    ApiKeyAuth,
    Plugin,
    PluginContext,
    PluginMeta,
    capabilities,
)
from hostwatch.plugins.aeza import api

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
ALWAYS_ACTIONS = {ServerAction.AUTORENEW, ServerAction.RENEW}
TRANSACTION_STATUSES = {"performed": InvoiceStatus.PAID, "cancelled": InvoiceStatus.CANCELLED}
REPLENISHMENT = "replenishment"

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
        try:
            return await self.ctx.http.get(
                BASE_URL + path,
                params=params,
                headers={"X-API-Key": self.ctx.secrets["api_key"], "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("Aeza is unreachable") from None

    @staticmethod
    def _server(service: api.Service, currency: str, allowed: set[ServerAction]) -> Server:
        hourly = service.paymentTerm == "hour"
        actions = set(ALWAYS_ACTIONS)
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
