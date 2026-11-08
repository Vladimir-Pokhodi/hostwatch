"""AdminVPS (my.adminvps.ru): servers, balance and traffic from the cabinet's HTML. Read-only.

The cabinet is WHMCS behind DDoS-Guard. The WHMCS session cookie is `WHMCS<random>`;
DDoS-Guard sets `__ddg*` cookies tied to the visitor's IP. A session made by the
browser login on the bot's server passes DDoS-Guard with plain httpx (checked
2026-11-08), so no TLS fingerprinting is needed.

One poll reads the services list, then for every live service its page (SolusVM server
id, IP, autorenewal buttons) and the SolusVM module's JSON (state, traffic): 1 + 2N
requests, one after another, like a person clicking through.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from fnmatch import fnmatchcase

import httpx

from hostwatch.core.errors import AuthExpired, Blocked, HosterError, RateLimited, describe
from hostwatch.core.models import (
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Server,
    ServerKind,
    ServerStatus,
    Usage,
)
from hostwatch.core.plugin import BrowserAuth, Plugin, PluginContext, PluginMeta, cookie_header
from hostwatch.core.record import Scrubber
from hostwatch.plugins.adminvps import pages
from hostwatch.plugins.adminvps.scrub import scrub_page

SITE = "https://my.adminvps.ru"
CURRENCY = "RUB"
AUTH = BrowserAuth(
    start_url=f"{SITE}/login",
    allowed_domains=("adminvps.ru",),
    cookies=("WHMCS*",),
    extra_cookies=("__ddg*",),
)
CLIENTAREA = f"{SITE}/clientarea.php"
CLIENT_DATA = f"{SITE}/modules/servers/solusvmpro/get_client_data.php"
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
# Services that no longer exist at the hoster; WHMCS keeps them in the list.
GONE = {"terminated", "cancelled", "fraud"}
# Services that have a server and a service page worth reading.
LIVE = {"active", "suspended"}
# Session cookies saved when the site renews them. DDoS-Guard also renews its
# short-lived `__ddg8_`/`__ddg9_`/`__ddg10_` on every answer; those are kept for the
# current call only, so a poll does not write the database every time.
LASTING_COOKIES = ("WHMCS*", "__ddg1_")
SOLUSVM_STATES = {
    "online": ServerStatus.RUNNING,
    "offline": ServerStatus.STOPPED,
    "disabled": ServerStatus.SUSPENDED,
}
DEDICATED = re.compile(r"(?i)выделенн|dedicated")

log = logging.getLogger(__name__)


class AdminVpsPlugin(Plugin):
    meta = PluginMeta(id="adminvps", name="AdminVPS", site=SITE, auth=(AUTH,), currency=CURRENCY)
    record_names = (
        "vserverData.hostname",
        "vserverData.mac",
        "vserverData.clientid",
        "vserverData.ctid-xid",
        "node",
    )

    def __init__(self, ctx: PluginContext) -> None:
        super().__init__(ctx)
        self._cookies = {k: v for k, v in ctx.secrets.items() if AUTH.wants(k)}
        # One poll calls servers(), usage() and balance() on the same instance:
        # what servers() has read is not asked for again.
        self._services_page: pages.LexborHTMLParser | None = None
        self._client_data: dict[str, pages.ClientData] = {}
        self._vserver_ids: dict[str, str | None] = {}

    async def check_login(self) -> None:
        await self._services()

    async def servers(self) -> list[Server]:
        page = await self._services()
        return [await self._server(row) for row in pages.services(page) if row.status not in GONE]

    async def balance(self) -> Balance:
        page = self._services_page or await self._services()
        return Balance(money=Money(amount=pages.balance(page), currency=CURRENCY))

    async def usage(self, server_id: str) -> Usage:
        """Only traffic: CPU and RAM are pictures of graphs in the cabinet."""
        data = self._client_data.get(server_id)
        if data is None:
            if server_id not in self._vserver_ids:
                await self._details(server_id)
            vserver_id = self._vserver_ids[server_id]
            data = await self._solusvm(server_id, vserver_id) if vserver_id else None
        return Usage(
            server_id=server_id,
            measured_at=datetime.now(UTC),
            traffic_gb=pages.traffic_gb(data.bandwidthused) if data else None,
        )

    async def _server(self, row: pages.ServiceRow) -> Server:
        details = pages.ServiceDetails(None, None, Autorenew.NOT_AVAILABLE)
        data = None
        if row.status in LIVE:
            details = await self._details(row.id)
            if details.vserver_id is not None:
                try:
                    data = await self._solusvm(row.id, details.vserver_id)
                except (TimeoutError, HosterError) as exc:
                    # SolusVM sometimes hangs for one server; the rest of the poll is
                    # still right, and this server shows as "unknown" until the next one.
                    log.warning("adminvps: no SolusVM data for a server: %s", describe(exc))
        status, raw_status = _status(row.status, data)
        return Server(
            external_id=row.id,
            name=row.domain or row.product,
            kind=ServerKind.DEDICATED if DEDICATED.search(row.product) else ServerKind.VPS,
            ip=(data.mainip if data else None) or details.ip,
            plan=row.product.partition(": ")[2] or row.product,
            status=status,
            raw_status=raw_status,
            price=Money(amount=row.price, currency=CURRENCY) if row.price is not None else None,
            period_months=row.period_months,
            next_charge_at=row.next_due,
            autorenew=details.autorenew,
            billing=BillingModel.PERIODIC,
        )

    async def _services(self) -> pages.LexborHTMLParser:
        html = await self._page({"action": "services"})
        self._services_page = pages.parse(html)
        return self._services_page

    async def _details(self, service_id: str) -> pages.ServiceDetails:
        html = await self._page({"action": "productdetails", "id": service_id})
        details = pages.details(pages.parse(html), html)
        self._vserver_ids[service_id] = details.vserver_id
        return details

    async def _solusvm(self, service_id: str, vserver_id: str) -> pages.ClientData:
        response = await self._get(
            CLIENT_DATA,
            {"vserverid": vserver_id},
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{CLIENTAREA}?action=productdetails&id={service_id}",
            },
        )
        if not response.text.lstrip().startswith("{"):
            _check_page(response.text)  # a login or DDoS-Guard page instead of JSON
        data = pages.client_data(response.text)
        self._client_data[service_id] = data
        return data

    async def _page(self, params: dict[str, str]) -> str:
        html = (await self._get(CLIENTAREA, params, HTML_HEADERS)).text
        _check_page(html)
        return html

    async def _get(
        self, url: str, params: dict[str, str], headers: dict[str, str]
    ) -> httpx.Response:
        try:
            response = await self.ctx.http.get(
                url,
                params=params,
                headers={**headers, "Cookie": cookie_header(self._cookies, AUTH)},
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError as exc:
            raise HosterError(f"network: {type(exc).__name__}") from None
        await self._keep_cookies(response)
        status = response.status_code
        if status in (301, 302, 303, 307, 308):
            raise AuthExpired  # WHMCS sends a guest to the login page
        if status == 403:
            raise Blocked
        if status == 429:
            raise RateLimited
        if status >= 400:
            raise HosterError(f"HTTP {status}")
        return response

    async def _keep_cookies(self, response: httpx.Response) -> None:
        fresh = {k: v for k, v in response.cookies.items() if AUTH.wants(k) and v}
        lasting = {
            k: v
            for k, v in fresh.items()
            if self._cookies.get(k) != v and any(fnmatchcase(k, p) for p in LASTING_COOKIES)
        }
        self._cookies.update(fresh)
        if lasting:
            await self.ctx.rotate_secrets({**self.ctx.secrets, **lasting})

    @classmethod
    def record_text(cls, text: str, scrubber: Scrubber) -> str:
        return scrub_page(text, scrubber)


def _check_page(html: str) -> None:
    if pages.is_logged_in(html):
        return
    if "ddos-guard" in html.lower():
        raise Blocked
    raise AuthExpired


def _status(whmcs: str, data: pages.ClientData | None) -> tuple[ServerStatus, str]:
    """WHMCS says whether the service is paid for; SolusVM whether the server runs."""
    if whmcs == "suspended":
        return ServerStatus.SUSPENDED, whmcs
    if whmcs != "active":
        return ServerStatus.UNKNOWN, whmcs  # pending, completed and statuses we have not met
    state = (data.state or "") if data else ""
    return SOLUSVM_STATES.get(state, ServerStatus.UNKNOWN), f"{whmcs}/{state or '?'}"
