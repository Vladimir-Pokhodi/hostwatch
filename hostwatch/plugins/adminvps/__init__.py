"""AdminVPS (my.adminvps.ru): servers, balance and traffic from the cabinet's HTML; SBP top-up.

The cabinet is WHMCS behind DDoS-Guard. The WHMCS session cookie is `WHMCS<random>`;
DDoS-Guard sets `__ddg*` cookies tied to the visitor's IP. A session made by the
browser login on the bot's server passes DDoS-Guard with plain httpx (checked
2026-11-08), so no TLS fingerprinting is needed.

One poll reads the services list, then for every live service its page (SolusVM server
id, IP, autorenewal buttons) and the SolusVM module's JSON (state, traffic): 1 + 2N
requests, one after another, like a person clicking through.

Top-up by SBP goes through WHMCS «Add funds» and the PayMaster gateway (`paymaster.py`).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatchcase

import httpx

from hostwatch.core.errors import (
    AuthExpired,
    Blocked,
    HosterError,
    LayoutChanged,
    PluginError,
    RateLimited,
    describe,
)
from hostwatch.core.models import (
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Payment,
    Server,
    ServerKind,
    ServerStatus,
    TopupKind,
    TopupMethod,
    Usage,
)
from hostwatch.core.plugin import BrowserAuth, Plugin, PluginContext, PluginMeta, cookie_header
from hostwatch.core.record import Scrubber
from hostwatch.plugins.adminvps import pages, paymaster
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
SBP_METHOD = "paymaster_sbp"
# The add-funds page says: from 100 to 200 000 ₽.
MIN_TOPUP = Decimal(100)
MAX_TOPUP = Decimal(200_000)
FORM_HEADERS = {**HTML_HEADERS, "Origin": SITE, "Referer": f"{CLIENTAREA}?action=addfunds"}

log = logging.getLogger(__name__)


class AdminVpsPlugin(Plugin):
    meta = PluginMeta(id="adminvps", name="AdminVPS", site=SITE, auth=(AUTH,), currency=CURRENCY)
    topup_page = f"{CLIENTAREA}?action=addfunds"
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

    async def topup_methods(self) -> list[TopupMethod]:
        minimum = Money(amount=MIN_TOPUP, currency=CURRENCY)
        return [TopupMethod(id=SBP_METHOD, name="СБП", kind=TopupKind.SBP, minimum=minimum)]

    async def topup(self, amount: Money, method_id: str) -> Payment:
        if method_id != SBP_METHOD:
            raise HosterError(f"unknown method {method_id}")
        value = amount.amount
        if amount.currency != CURRENCY or not MIN_TOPUP <= value <= MAX_TOPUP:
            raise HosterError(f"the amount must be from {MIN_TOPUP} to {MAX_TOPUP} ₽")
        if value != value.quantize(Decimal("0.01")):
            raise HosterError("the amount must be in whole kopecks")
        token = pages.form_token(pages.parse(await self._page({"action": "addfunds"})))
        fields = {"token": token, "amount": f"{value:f}", "paymentmethod": SBP_METHOD}
        form = paymaster.gateway_form(
            await self._post_form(f"{CLIENTAREA}?action=addfunds", fields)
        )
        if form.amount != value:
            raise LayoutChanged
        payment = Payment(
            id=form.invoice_id,
            amount=amount,
            method=method_id,
            created_at=datetime.now(UTC),
            url=f"{SITE}/viewinvoice.php?id={form.invoice_id}",
        )
        try:
            link = await self._paymaster(form)
        except (PluginError, TimeoutError) as exc:
            # The invoice is made; its page in the cabinet still pays it.
            log.warning("adminvps: no SBP link from PayMaster: %s", describe(exc))
            return payment
        return payment.model_copy(update={"url": link, "qr_data": link})

    async def _paymaster(self, form: paymaster.GatewayForm) -> str:
        """What the browser does after WHMCS: post the form, open the page, choose SBP."""
        accept = {"Accept": HTML_HEADERS["Accept"]}
        init = await self._outside(
            "POST", form.action, data=form.fields, headers={**accept, "Referer": f"{SITE}/"}
        )
        page = init.headers.get("location", "")
        public_id = paymaster.payment_id(page)
        page_url = page if page.startswith("https://") else paymaster.SITE + page
        await self._outside("GET", page_url, headers=accept)
        auth = await self._outside(
            "POST",
            paymaster.AUTH,
            json={"paymentMethodAlias": "SBP"},
            headers={
                "Accept": "application/json",
                "Origin": paymaster.SITE,
                "Referer": page_url,
                "x-public-id": public_id,
            },
        )
        return paymaster.sbp_link(auth.content)

    async def _outside(
        self,
        method: str,
        url: str,
        data: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """A request to the payment gateway: no cabinet cookies, no redirects followed."""
        try:
            response = await self.ctx.http.request(
                method, url, data=data, json=json, headers=headers, follow_redirects=False
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError as exc:
            raise HosterError(f"network: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise HosterError(f"PayMaster: HTTP {response.status_code}")
        return response

    async def _post_form(self, url: str, fields: dict[str, str]) -> str:
        """POST a cabinet form. WHMCS answers with the next page, or a redirect to it."""
        try:
            response = await self.ctx.http.post(
                url,
                data=fields,
                headers={**FORM_HEADERS, "Cookie": cookie_header(self._cookies, AUTH)},
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError as exc:
            raise HosterError(f"network: {type(exc).__name__}") from None
        await self._keep_cookies(response)
        location = response.headers.get("location", "")
        found = pages.INVOICE_LINK.search(location)
        if response.is_redirect and found:
            return await self._page_at(f"{SITE}/viewinvoice.php", {"id": found.group(1)})
        _check_status(response)
        _check_page(response.text)
        return response.text

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
        return await self._page_at(CLIENTAREA, params)

    async def _page_at(self, url: str, params: dict[str, str]) -> str:
        html = (await self._get(url, params, HTML_HEADERS)).text
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
        _check_status(response)
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


def _check_status(response: httpx.Response) -> None:
    status = response.status_code
    if status in (301, 302, 303, 307, 308):
        raise AuthExpired  # WHMCS sends a guest to the login page
    if status == 403:
        raise Blocked
    if status == 429:
        raise RateLimited
    if status >= 400:
        raise HosterError(f"HTTP {status}")


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
