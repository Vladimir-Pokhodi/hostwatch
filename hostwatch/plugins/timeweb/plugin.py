"""Timeweb Cloud through its public API (https://timeweb.cloud/api-docs).

Billing is hourly: the money is taken from the balance every hour, and the hoster
computes itself how many hours the balance lasts.

A top-up invoice cannot be made with the API token: only the panel makes it, with its own
session (the `refresh_token` and `device_token` cookies of a browser login). Every use of
the session swaps the refresh token for a new one and kills the old, so the new one is
saved before anything else happens. The bot and the owner's browser therefore cannot
share one session.

Power goes through the public API too: `POST servers/{id}/start|shutdown|reboot`, answered
with 204 (the API reference, 2026-11-08; not sent to a live server yet). Dedicated
servers have no power control in the API.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import fmean
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from hostwatch.core.catalog import Catalog, OrderRequest, OrderResult, OrderTemplate
from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import (
    ActionResult,
    Autorenew,
    Balance,
    BillingModel,
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
from hostwatch.core.plugin import ApiKeyAuth, BrowserAuth, Plugin, PluginMeta
from hostwatch.plugins.timeweb import ordering
from hostwatch.plugins.timeweb.schema import (
    CloudServer,
    CloudServerAnswer,
    CloudServersAnswer,
    DedicatedServer,
    DedicatedServersAnswer,
    FinancesAnswer,
    InvoiceAnswer,
    PanelTokensAnswer,
    PayersAnswer,
    ServiceCost,
    ServiceCostsAnswer,
    StatisticsAnswer,
)

API = "https://api.timeweb.cloud/api/v1/"
PAGE_SIZE = 100
# Timeweb shows monthly prices; an average month has 730 hours.
HOURS_PER_MONTH = Decimal(730)
HOURLY_PRECISION = Decimal("0.0001")
USAGE_WINDOW = timedelta(minutes=15)
CPU_KEY = "system.cpu.util"
DEDICATED_PREFIX = "dedicated-"
PANEL = "https://timeweb.cloud/"
UPDATE_TOKEN = PANEL + "api/v1/auth/update-token"
FINANCES_PAGE = PANEL + "my/finances"
SESSION_COOKIES = ("refresh_token", "device_token")
SBP = "sbp"
# The panel refuses 101 and 200 ₽ with `amount_less_minimal` and takes 500 (2026-11-08).
MIN_TOPUP = Decimal(500)

CLOUD_STATUSES = {
    "on": ServerStatus.RUNNING,
    "off": ServerStatus.STOPPED,
    "blocked": ServerStatus.SUSPENDED,
    "permanent_blocked": ServerStatus.SUSPENDED,
    "no_paid": ServerStatus.UNPAID,
}
POWER_PATHS = {
    PowerAction.START: "start",
    PowerAction.STOP: "shutdown",
    PowerAction.RESTART: "reboot",
}
CLOUD_ACTIONS = frozenset({ServerAction.START, ServerAction.STOP, ServerAction.RESTART})
DEDICATED_STATUSES = {
    "on": ServerStatus.RUNNING,
    "off": ServerStatus.STOPPED,
}


class TimewebPlugin(Plugin):
    meta = PluginMeta(
        id="timeweb",
        name="Timeweb Cloud",
        site="https://timeweb.cloud",
        auth=(
            ApiKeyAuth(fields=("api_token",)),
            # The panel session is needed for top-up invoices only.
            BrowserAuth(
                start_url="https://timeweb.cloud/my/login",
                allowed_domains=("timeweb.cloud",),
                cookies=("refresh_token", "device_token"),
            ),
        ),
        currency="RUB",
    )
    topup_page = FINANCES_PAGE
    record_names = (
        "servers.*.name",
        "server.name",
        "dedicated_servers.*.name",
        "services_costs.*.info.name",
        "comment",
        "display_name",
        "node_name",
        "ptr",
        # Floating IP ids are UUIDs, which the id scrubber leaves alone.
        "ips.*.id",
        "services.*.info.id",
        # Request ids are harmless, but they point Timeweb support at this account.
        "response_id",
    )

    async def servers(self) -> list[Server]:
        currency = (await self._get("account/finances", FinancesAnswer)).finances.currency
        costs = await self._get("account/services/cost", ServiceCostsAnswer)
        cloud = await self._cloud_servers()
        dedicated = await self._get("dedicated-servers", DedicatedServersAnswer)
        by_service = {(cost.type, str(cost.service_id)): cost for cost in costs.services_costs}
        return [
            _cloud_server(server, by_service.get(("server", str(server.id))), currency)
            for server in cloud
        ] + [
            _dedicated_server(
                server, by_service.get(("dedicated_server", str(server.id))), currency
            )
            for server in dedicated.dedicated_servers
        ]

    async def balance(self) -> Balance:
        finances = (await self._get("account/finances", FinancesAnswer)).finances
        hours_left = None if finances.hours_left is None else float(finances.hours_left)
        return Balance(
            money=Money(amount=finances.balance, currency=finances.currency),
            hours_left=hours_left,
        )

    async def usage(self, server_id: str) -> Usage:
        now = datetime.now(UTC)
        if server_id.startswith(DEDICATED_PREFIX):
            # The API has no statistics for dedicated servers.
            return Usage(server_id=server_id, measured_at=now)
        server = (await self._get(f"servers/{server_id}", CloudServerAnswer)).server
        # The path takes the end of the period and its length in hours.
        end = now.strftime("%Y-%m-%dT%H:%M:%S")
        stats = await self._get(
            f"servers/{server_id}/statistics/{end}/1/{CPU_KEY}", StatisticsAnswer
        )
        points = stats.series(CPU_KEY)
        if not points:
            return Usage(server_id=server_id, measured_at=now, disk_percent=_disk_percent(server))
        # Timeweb writes a point a minute, sometimes late; average the newest ones.
        latest = max(point.time for point in points)
        recent = [point.value for point in points if point.time > latest - USAGE_WINDOW]
        return Usage(
            server_id=server_id,
            measured_at=latest,
            cpu_percent=fmean(recent),
            disk_percent=_disk_percent(server),
        )

    async def power(self, server_id: str, action: PowerAction) -> ActionResult:
        if server_id.startswith(DEDICATED_PREFIX) or not server_id.isdigit():
            raise HosterError("the API has no power control for this server")
        token = self.ctx.secrets.get("api_token")
        if not token:
            raise AuthExpired
        try:
            response = await self.ctx.http.post(
                f"{API}servers/{server_id}/{POWER_PATHS[action]}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("Timeweb is unreachable") from None
        _raise_for_status(response)
        return ActionResult(ok=True, detail=f"HTTP {response.status_code}")

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

    async def topup_methods(self) -> list[TopupMethod]:
        minimum = Money(amount=MIN_TOPUP, currency="RUB")
        return [TopupMethod(id=SBP, name="СБП", kind=TopupKind.SBP, minimum=minimum)]

    async def topup(self, amount: Money, method_id: str) -> Payment:
        if method_id != SBP:
            raise HosterError(f"unknown method {method_id}")
        if amount.currency != "RUB" or amount.amount != amount.amount.to_integral_value():
            raise HosterError("the amount must be in whole rubles")
        access = await self._panel_token()
        login = _jwt_claim(access, "user") or _jwt_claim(self.ctx.secrets["api_token"], "user")
        if not login:
            raise LayoutChanged
        payers = await self._panel(
            "GET",
            f"portal/v1/accounts/{login}/payers",
            PayersAnswer,
            access,
            params={"type": "person"},
        )
        payer = next((payer for payer in payers.root if not payer.hidden), None)
        if payer is None:
            raise HosterError("the account has no payer")
        body = {
            "total_amount": int(amount.amount),
            "payer": {"id": payer.id},
            "payment_method": {
                "type": SBP,
                "params": {"bind": False, "return_url": FINANCES_PAGE, "fail_url": FINANCES_PAGE},
            },
            "items": [],
        }
        invoice = await self._panel("POST", "portal/v4/invoices", InvoiceAnswer, access, json=body)
        page = invoice.payment_info.confirmation.confirmation_url
        url, qr = await yookassa.link_or_page(self.ctx.http, page)
        return Payment(
            id=str(invoice.invoice_id),
            amount=amount,
            method=SBP,
            created_at=datetime.now(UTC),
            url=url,
            qr_data=qr,
        )

    async def _panel_token(self) -> str:
        """A 15-minute access token of the panel. The rotated refresh token is saved first."""
        secrets = self.ctx.secrets
        if not all(secrets.get(name) for name in SESSION_COOKIES):
            raise AuthExpired
        cookie = "; ".join(f"{name}={secrets[name]}" for name in SESSION_COOKIES)
        try:
            response = await self.ctx.http.post(
                UPDATE_TOKEN,
                headers={"Cookie": cookie, "Accept": "application/json", "Origin": PANEL[:-1]},
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("Timeweb is unreachable") from None
        # Whatever comes next, a reissued token is saved first: the old one is dead now.
        fresh = {name: value for name in SESSION_COOKIES if (value := response.cookies.get(name))}
        if fresh:
            await self.ctx.rotate_secrets({**secrets, **fresh})
        if response.status_code in (401, 403) and _is_json(response):
            raise AuthExpired  # the session is dead; QRATOR's 403 is an HTML page
        _raise_for_status(response)
        try:
            tokens = PanelTokensAnswer.model_validate_json(response.content).tokens
        except ValidationError:
            raise LayoutChanged from None
        if "refresh_token" not in fresh:
            if not tokens.refresh_token:
                raise LayoutChanged  # the old token is dead already, and there is no new one
            await self.ctx.rotate_secrets({**secrets, "refresh_token": tokens.refresh_token})
        return tokens.access_token

    async def _panel[M: BaseModel](
        self,
        method: str,
        path: str,
        model: type[M],
        access: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> M:
        try:
            response = await self.ctx.http.request(
                method,
                PANEL + path,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("Timeweb is unreachable") from None
        _raise_for_status(response)
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise LayoutChanged from None

    async def _cloud_servers(self) -> list[CloudServer]:
        servers: list[CloudServer] = []
        while True:
            page = await self._get(
                "servers",
                CloudServersAnswer,
                params={"limit": PAGE_SIZE, "offset": len(servers)},
            )
            servers.extend(page.servers)
            if not page.servers or len(servers) >= page.meta.total:
                return servers

    async def _get[M: BaseModel](
        self, path: str, model: type[M], params: dict[str, Any] | None = None
    ) -> M:
        token = self.ctx.secrets.get("api_token")
        if not token:
            raise AuthExpired
        try:
            response = await self.ctx.http.get(
                API + path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        _raise_for_status(response)
        try:
            # Decimal straight from the text, so 404.67 does not pass through float.
            return model.model_validate(json.loads(response.text, parse_float=Decimal))
        except (ValueError, ValidationError):
            raise LayoutChanged from None


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    if status == 401:
        raise AuthExpired
    if status == 403:
        raise Blocked
    if status == 429:
        raise RateLimited
    raise HosterError(_error_code(response))


def _is_json(response: httpx.Response) -> bool:
    return "json" in response.headers.get("content-type", "")


def _jwt_claim(token: str, name: str) -> str | None:
    """A claim of a JWT, signature unchecked: the token came from Timeweb itself."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (binascii.Error, ValueError):
        return None
    value = claims.get(name) if isinstance(claims, dict) else None
    return value if isinstance(value, str) and value else None


def _error_code(response: httpx.Response) -> str:
    """Timeweb's `error_code` is a fixed word such as `not_found`; its `message` may echo input."""
    try:
        code = response.json().get("error_code")
    except (ValueError, AttributeError):
        code = None
    return code if isinstance(code, str) and code.isidentifier() else f"HTTP {response.status_code}"


def _cloud_server(server: CloudServer, cost: ServiceCost | None, currency: str) -> Server:
    status = CLOUD_STATUSES.get(server.status, ServerStatus.UNKNOWN)
    if server.is_blocked:
        status = ServerStatus.SUSPENDED
    return Server(
        external_id=str(server.id),
        name=server.name,
        kind=ServerKind.VPS,
        ip=_main_ip(server),
        plan=_plan(server, cost),
        status=status,
        raw_status=server.status,
        price=_hourly_price(cost, currency),
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.HOURLY,
        actions=CLOUD_ACTIONS,
    )


def _dedicated_server(server: DedicatedServer, cost: ServiceCost | None, currency: str) -> Server:
    monthly = cost.total_cost if cost is not None else server.price
    return Server(
        external_id=f"{DEDICATED_PREFIX}{server.id}",
        name=server.name,
        kind=ServerKind.DEDICATED,
        ip=server.ip,
        plan=server.cpu_description or None,
        status=DEDICATED_STATUSES.get(server.status, ServerStatus.UNKNOWN),
        raw_status=server.status,
        price=Money(amount=_per_hour(monthly), currency=currency),
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.HOURLY,
    )


def _main_ip(server: CloudServer) -> str | None:
    ips = [ip for net in server.networks if net.type == "public" for ip in net.ips or []]
    for wanted in ("ipv4", "ipv6"):
        found = [ip for ip in ips if ip.type == wanted]
        if found:
            return next((ip.ip for ip in found if ip.is_main), found[0].ip)
    return None


def _plan(server: CloudServer, cost: ServiceCost | None) -> str | None:
    if cost is None:
        return None
    if server.preset_id is not None:
        return cost.info.description or None if cost.info is not None else None
    # A server built in the configurator has no tariff name, only the word "Конфигуратор".
    config = cost.configuration
    if config is None or not (config.cpu and config.ram and config.disk):
        return None
    return f"{config.cpu} CPU, {config.ram} GB RAM, {config.disk} GB"


def _hourly_price(cost: ServiceCost | None, currency: str) -> Money | None:
    if cost is None:
        return None
    return Money(amount=_per_hour(cost.total_cost), currency=currency)


def _per_hour(monthly: Decimal) -> Decimal:
    return (monthly / HOURS_PER_MONTH).quantize(HOURLY_PRECISION)


def _disk_percent(server: CloudServer) -> float | None:
    system = next((disk for disk in server.disks if disk.is_system), None)
    if system is None or system.size <= 0:
        return None
    return system.used / system.size * 100
