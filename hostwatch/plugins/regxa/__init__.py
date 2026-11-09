"""Regxa (regxa.com) through the site's own API with the owner's browser session.

The site is Next.js with NextAuth in front of WHMCS (billing) and Virtualizor (VPS). The
session lives in the `rgx.session-token` cookie (split into `.0`, `.1` when long).
`GET /api/auth/session` returns `{}` without a session; with one it returns the client id
and a fresh cookie with a new expiry, which the plugin saves: that is what keeps the
session alive between polls.

Server ids are WHMCS service ids (invoices refer to them); the Virtualizor VPS id needed for
details, status and usage sits in the service's custom field `vpsid`.

Top-up is crypto through Cryptomus only: `addfunds` makes a WHMCS invoice, and
`cryptomus/create-payment` gives its Cryptomus page. The page lists its coins and networks
for each invoice, so the owner chooses the coin after the invoice is made
(`SupportsPayOptions`). The wallet address is on that page only, so a headless tab reads it
(see `cryptomus.py`). Without the list or the tab the owner gets the page link and chooses
the coin there.

Control is what the site's pages send (their scripts, read 2026-11-08; not sent to a live
server yet): `POST /api/services/vps/power/{start|stop|restart}/{vpsid}` without a body, and
paying an invoice from the balance with `POST /api/billing/invoices/apply-credit
{"invoiceid", "amount"}` → `{"success", "message"}`. The plugin pays only renewal invoices
(with a "Hosting" line), only the confirmed sum, and reads the invoice again afterwards.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from hostwatch.core.catalog import Catalog, OrderRequest, OrderResult, OrderTemplate
from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import (
    ActionResult,
    Autorenew,
    Balance,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Payment,
    PayOption,
    PowerAction,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    TopupKind,
    TopupMethod,
    Usage,
)
from hostwatch.core.plugin import BrowserAuth, Plugin, PluginContext, PluginMeta, cookie_header
from hostwatch.plugins.regxa import api, cryptomus, ordering

if TYPE_CHECKING:
    from hostwatch.core.record import Scrubber

SITE = "https://regxa.com"
AUTH = BrowserAuth(
    start_url=f"{SITE}/login",
    # Google and GitHub: the site offers them as login options.
    allowed_domains=("regxa.com", "accounts.google.com", "github.com"),
    cookies=("rgx.session-token*",),
)
VPS_FIELD = "vpsid"
# WHMCS service states that are no longer a server.
GONE = {"Terminated", "Cancelled", "Fraud"}
PERIOD_MONTHS = {
    "monthly": 1,
    "quarterly": 3,
    "semi-annually": 6,
    "annually": 12,
    "biennially": 24,
    "triennially": 36,
}
POWER = {1: ServerStatus.RUNNING, 0: ServerStatus.STOPPED, 2: ServerStatus.SUSPENDED}
INVOICE_STATUSES = {
    "Unpaid": InvoiceStatus.UNPAID,
    "Paid": InvoiceStatus.PAID,
    "Cancelled": InvoiceStatus.CANCELLED,
}
POWER_PATHS = {
    PowerAction.START: "start",
    PowerAction.STOP: "stop",
    PowerAction.RESTART: "restart",
}
POWER_ACTIONS = frozenset({ServerAction.START, ServerAction.STOP, ServerAction.RESTART})
DETAIL_LIMIT = 200
# An invoice item that renews a service; its `relid` is the service id.
RENEWAL_ITEM = "Hosting"
NO_DATE = "0000-00-00"
# For `record_text`: what the core scrubber does not recognise in WHMCS answers.
RECORD_IDS = {"userid", "invoiceid", "relid", "vpsid", "billingcid", "paymethodid", "stid"}
RECORD_MONEY = {
    "credit",
    "total",
    "balance",
    "subtotal",
    "tax",
    "tax2",
    "income",
    "grossRevenue",
    "expenses",
}

# The one top-up method: the coin and the network are chosen once the invoice is made.
PAGE_METHOD = "cryptomus"
MIN_TOPUP = Decimal(5)
# Opening the page and three clicks take ~15 s on the bot's one CPU.
BROWSER_TIMEOUT = 90.0

A = TypeVar("A", bound=BaseModel)
log = logging.getLogger(__name__)


class RegxaPlugin(Plugin):
    meta = PluginMeta(id="regxa", name="Regxa", site=SITE, auth=(AUTH,), currency="USD")
    topup_page = f"{SITE}/billing/add-funds"
    record_names = (
        "uuid",
        "hostname",
        "vps_name",
        "firstname",
        "lastname",
        "fullname",
        "companyname",
        "phonenumber",
        "address1",
        "address2",
        "city",
        "state",
        "postcode",
        "country",
        "countrycode",
        "countryname",
        "lastlogin",
        "datecreated",
        "notes",
        "users.user.*.name",
        "items.item.*.description",
    )

    def __init__(self, ctx: PluginContext) -> None:
        super().__init__(ctx)
        self._client_id: str | None = None
        self._services: list[api.Service] | None = None

    async def check_login(self) -> None:
        self._client_id = None  # always ask the site, even if this plugin asked before
        await self._session()

    async def servers(self) -> list[Server]:
        vps = [
            (service, vps_id)
            for service in await self._list_services()
            if service.status not in GONE and (vps_id := service.field(VPS_FIELD))
        ]
        return list(await asyncio.gather(*(self._server(s, vps_id) for s, vps_id in vps)))

    async def balance(self) -> Balance:
        client = await self._client()
        profile = await self._get(api.Profile, f"/api/profile/client/{client}")
        return Balance(money=_money(profile.data.credit, profile.data.currency_code))

    async def usage(self, server_id: str) -> Usage:
        service = next((s for s in await self._list_services() if str(s.id) == server_id), None)
        vps_id = service.field(VPS_FIELD) if service else None
        if vps_id is None:
            raise HosterError(f"no VPS with id {server_id}")
        state = await self._status(vps_id)
        return Usage(
            server_id=server_id,
            measured_at=datetime.now(UTC),
            cpu_percent=state.used_cpu,
            ram_percent=_percent(state.used_ram, state.ram),
            disk_percent=_percent(state.used_disk, state.disk),
            traffic_gb=state.used_bandwidth,
        )

    async def invoices(self) -> list[Invoice]:
        """The latest WHMCS invoices, newest first (the site shows the same 25).

        Only unpaid ones are opened to find the service they renew: that link is what
        autopay needs, and opening every paid invoice would cost a request each.
        """
        await self._session()
        rows = (await self._get(api.Invoices, "/api/billing/invoices")).rows()
        unpaid = [row for row in rows if row.status == "Unpaid"]
        details = await asyncio.gather(
            *(self._get(api.InvoiceDetails, f"/api/billing/invoices/{row.id}") for row in unpaid)
        )
        opened = {row.id: detail for row, detail in zip(unpaid, details, strict=True)}
        return [_invoice(row, opened.get(row.id)) for row in rows]

    async def power(self, server_id: str, action: PowerAction) -> ActionResult:
        service = next((s for s in await self._list_services() if str(s.id) == server_id), None)
        vps_id = service.field(VPS_FIELD) if service else None
        if vps_id is None or not vps_id.isdigit():
            raise HosterError(f"no VPS with id {server_id}")
        response = await self._request(
            f"/api/services/vps/power/{POWER_PATHS[action]}/{vps_id}",
            {},
            post=True,
            referer=f"{SITE}/dashboard/services/cloud/{server_id}",
        )
        _raise_for_status(response)
        return ActionResult(ok=True, detail=_outcome(response).done_msg[:DETAIL_LIMIT] or "done")

    async def pay_invoice(self, invoice_id: str, amount: Money) -> ActionResult:
        """Pay a renewal invoice with the credit on the account: the sum the owner confirmed."""
        if not invoice_id.isdigit():
            raise HosterError(f"bad invoice id {invoice_id!r}")
        path = f"/api/billing/invoices/{invoice_id}"
        await self._session()
        details = await self._get(api.InvoiceDetails, path)
        if details.status != "Unpaid":
            raise HosterError(f"the invoice is {details.status or 'unknown'}, not unpaid")
        if not any(line.type == RENEWAL_ITEM for line in details.lines()):
            raise HosterError("not a service renewal invoice")
        if details.balance is None:
            raise LayoutChanged
        due = _money(details.balance, self.meta.currency)
        if amount != due:
            raise HosterError(f"the invoice is {due} now, not {amount}")
        credit = (await self.balance()).money
        if credit < due:
            raise HosterError(f"the balance is {credit}, less than {due}")
        response = await self._request(
            "/api/billing/invoices/apply-credit",
            {},
            # The page sends both as JSON numbers; cents survive the float.
            {"invoiceid": int(invoice_id), "amount": float(due.amount)},
            referer=f"{SITE}/dashboard/billing/invoices/{invoice_id}",
        )
        _raise_for_status(response)
        outcome = _outcome(response)
        if not outcome.success:
            raise HosterError(outcome.message[:DETAIL_LIMIT] or "apply-credit failed")
        after = await self._get(api.InvoiceDetails, path)
        detail = f"credit applied; the invoice is {after.status or 'unknown'} now"
        return ActionResult(ok=after.status == "Paid", detail=detail)

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
        minimum = Money(amount=MIN_TOPUP, currency=self.meta.currency)
        return [
            TopupMethod(id=PAGE_METHOD, name="Cryptomus", kind=TopupKind.CRYPTO, minimum=minimum)
        ]

    async def topup(self, amount: Money, method_id: str) -> Payment:
        if method_id != PAGE_METHOD:
            raise HosterError(f"unknown method {method_id}")
        if amount.currency != self.meta.currency or amount.amount < MIN_TOPUP:
            raise HosterError(f"the minimum is {MIN_TOPUP} {self.meta.currency}")
        await self._session()
        # WHMCS takes the sum as a JSON number; cents survive the float.
        added = await self._post(api.AddFunds, "/api/billing/addfunds", amount=float(amount.amount))
        if not added.success or added.data is None:
            raise HosterError(added.message[:200] or "addfunds failed")
        invoice = added.data.invoiceid
        link = await self._post(
            api.CryptomusLink,
            "/api/billing/cryptomus/create-payment",
            invoiceId=invoice,
            returnUrl=f"{SITE}/billing/invoices/{invoice}",
        )
        if not link.success or not link.url:
            raise HosterError(link.message[:200] or "Cryptomus gave no payment page")
        if not link.url.startswith(cryptomus.PAY_PAGE):
            raise LayoutChanged
        payment = Payment(
            id=str(invoice),
            amount=amount,
            method=method_id,
            created_at=datetime.now(UTC),
            url=link.url,
            qr_data=link.url,
        )
        try:
            options = await self.pay_options(payment)
        except Exception as exc:
            # The invoice is made; its page still pays it, so the owner gets the link.
            log.warning("regxa: no Cryptomus coin list (%s); giving the page", type(exc).__name__)
            return payment
        return payment.model_copy(update={"options": tuple(options)})

    async def pay_options(self, payment: Payment) -> list[PayOption]:
        return [network.option for network in await self._networks(payment)]

    async def pay_with(self, payment: Payment, option_id: str) -> Payment:
        """The address for one coin and network of the invoice's Cryptomus page."""
        network = next((n for n in await self._networks(payment) if n.option.id == option_id), None)
        if network is None:
            raise HosterError(f"the payment page does not take {option_id}")
        if self.ctx.pages is None or payment.url is None:
            raise HosterError("no browser to read the address; choose the coin on the page")
        async with self.ctx.pages.open(cryptomus.DOMAINS) as page:
            found = await asyncio.wait_for(
                cryptomus.address(page, payment.url, network), BROWSER_TIMEOUT
            )
        return payment.model_copy(
            update={
                "address": found.address,
                "qr_data": found.address,
                "pay_amount": found.amount,
                "pay_currency": found.currency,
                "expires_at": found.expires_at,
                "options": (),
            }
        )

    async def _networks(self, payment: Payment) -> list[cryptomus.Network]:
        if not payment.url or not payment.url.startswith(cryptomus.PAY_PAGE):
            raise HosterError("not a Cryptomus invoice")
        return await cryptomus.networks(self.ctx.http, payment.url)

    @classmethod
    def record_text(cls, text: str, scrubber: Scrubber) -> str:
        """WHMCS ids under lowercase keys, invoice sums, ids of IP records used as keys,
        and the VPS uuid field: the core scrubber does not know these."""
        try:
            answer = json.loads(text)
        except ValueError:
            return text
        return json.dumps(_scrub(answer, scrubber), ensure_ascii=False)

    async def _server(self, service: api.Service, vps_id: str) -> Server:
        details = await self._get(api.VpsDetails, f"/api/services/vps/details/{vps_id}")
        if service.status == "Suspended" or details.suspended:
            status = ServerStatus.SUSPENDED
        elif service.status == "Active":
            power = (await self._status(vps_id)).status
            status = POWER.get(power, ServerStatus.UNKNOWN)
        else:
            status = ServerStatus.UNKNOWN
        period = PERIOD_MONTHS.get(service.billingCycle.lower())
        return Server(
            external_id=str(service.id),
            name=details.hostname or service.name,
            kind=ServerKind.VPS,
            ip=_ipv4(details),
            plan=service.name,
            status=status,
            raw_status=service.status,
            price=_money(service.amount, self.meta.currency) if period else None,
            period_months=period,
            next_charge_at=_date(service.nextDueDate),
            # Regxa has no auto-renewal: it issues an invoice before the term ends.
            autorenew=Autorenew.NOT_AVAILABLE,
            billing=BillingModel.PERIODIC,
            # A suspended VPS is the hoster's lock: power buttons would only fail.
            actions=frozenset() if status is ServerStatus.SUSPENDED else POWER_ACTIONS,
        )

    async def _status(self, vps_id: str) -> api.VpsStatus:
        answer = await self._json(f"/api/services/vps/status/{vps_id}")
        if isinstance(answer, int) and not isinstance(answer, bool):
            return api.VpsStatus(status=answer)
        try:
            return api.VpsStatus.model_validate(answer)
        except ValidationError:
            raise LayoutChanged from None

    async def _list_services(self) -> list[api.Service]:
        if self._services is None:
            client = await self._client()
            answer = await self._json("/api/services/getservices", clientId=client)
            if not isinstance(answer, list):
                raise LayoutChanged
            try:
                self._services = [api.Service.model_validate_json(json.dumps(i)) for i in answer]
            except ValidationError:
                raise LayoutChanged from None
        return self._services

    async def _client(self) -> str:
        client = await self._session()
        if not client:
            raise LayoutChanged
        return client

    async def _session(self) -> str:
        """Checks the session once per plugin call and keeps its cookie fresh.

        Returns the client id, empty if the session does not name one.
        """
        if self._client_id is None:
            response = await self._request("/api/auth/session", {})
            _raise_for_status(response)
            try:
                session = api.Session.model_validate_json(response.content)
            except ValidationError:
                raise LayoutChanged from None
            if session.user is None:
                raise AuthExpired
            await self._keep_cookie(response)
            self._client_id = session.user.client_id or ""
        return self._client_id

    async def _keep_cookie(self, response: httpx.Response) -> None:
        """Save the session cookie NextAuth reissued with a new expiry.

        The cookie can be split into chunks (`.0`, `.1`), and their number can change, so
        the whole set is replaced. Expired chunks are not in `response.cookies`.
        """
        fresh = {name: value for name, value in response.cookies.items() if AUTH.wants(name)}
        if not fresh:
            return
        kept = {name: value for name, value in self.ctx.secrets.items() if not AUTH.wants(name)}
        secrets = kept | fresh
        if secrets != dict(self.ctx.secrets):
            await self.ctx.rotate_secrets(secrets)

    async def _get(self, model: type[A], path: str, **params: Any) -> A:
        response = await self._request(path, params)
        _raise_for_status(response)
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise LayoutChanged from None

    async def _json(self, path: str, **params: Any) -> Any:
        response = await self._request(path, params)
        _raise_for_status(response)
        try:
            return response.json()
        except ValueError:
            raise LayoutChanged from None

    async def _post(self, model: type[A], path: str, **body: Any) -> A:
        response = await self._request(path, {}, body)
        _raise_for_status(response)
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise LayoutChanged from None

    async def _request(
        self,
        path: str,
        params: dict[str, Any],
        body: dict[str, Any] | None = None,
        post: bool = False,
        referer: str = f"{SITE}/billing/add-funds",
    ) -> httpx.Response:
        """GET, or POST (of a JSON `body`, or empty) the way the site's pages send it."""
        headers = {"Cookie": cookie_header(self.ctx.secrets, AUTH), "Accept": "application/json"}
        try:
            if body is None and not post:
                return await self.ctx.http.get(SITE + path, params=params, headers=headers)
            headers |= {"Origin": SITE, "Referer": referer}
            if body is None:
                headers["Content-Type"] = "application/json"
                return await self.ctx.http.post(SITE + path, headers=headers)
            return await self.ctx.http.post(SITE + path, json=body, headers=headers)
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("Regxa is unreachable") from None


def _scrub(value: Any, scrubber: Scrubber, key: str = "") -> Any:
    if isinstance(value, list):
        return [_scrub(item, scrubber, key) for item in value]
    if isinstance(value, dict):
        if key == "ips":
            value = {str(scrubber.fake_id(k)) if k.isdigit() else k: v for k, v in value.items()}
        if value.get("name") == "vps_uuid" and isinstance(value.get("value"), str):
            value = {**value, "value": scrubber.label("vps_uuid", value["value"])}
        return {k: _scrub(v, scrubber, k) for k, v in value.items()}
    if isinstance(value, bool) or value is None:
        return value
    if key in RECORD_MONEY:
        return scrubber.fake_money(value)
    if key in RECORD_IDS and str(value).isdigit() and str(value).strip("0"):
        fake = scrubber.fake_id(str(value))
        return fake if isinstance(value, int) else str(fake)
    return value


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    if 300 <= code < 400:
        raise AuthExpired  # the site sends a visitor without a session to the login page
    if code < 400:
        return
    if code == 401:
        raise AuthExpired
    if code == 429:
        raise RateLimited
    message = _message(response)
    if code == 403 and not message:
        raise Blocked
    raise HosterError(f"HTTP {code}" + (f": {message[:200]}" if message else ""))


def _message(response: httpx.Response) -> str:
    """`{"error": "..."}` or `{"success": false, "message": "..."}`."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    text = body.get("error") or body.get("message") or ""
    return text if isinstance(text, str) else ""


def _invoice(row: api.InvoiceRow, details: api.InvoiceDetails | None) -> Invoice:
    lines = details.lines() if details else []
    renewed = {line.relid for line in lines if line.type == RENEWAL_ITEM and line.relid}
    # An opened unpaid invoice shows what is left to pay: that is the sum to confirm.
    left = details.balance if details is not None else None
    return Invoice(
        id=str(row.id),
        amount=_money(row.total if left is None else left, row.currencycode),
        status=INVOICE_STATUSES.get(row.status, InvoiceStatus.UNKNOWN),
        description="; ".join(
            line.description.splitlines()[0] for line in lines if line.description
        ),
        due_at=_date(row.duedate),
        # Several services on one invoice: none of them owns it.
        server_id=str(renewed.pop()) if len(renewed) == 1 else None,
    )


def _outcome(response: httpx.Response) -> api.Outcome:
    try:
        return api.Outcome.model_validate_json(response.content or b"{}")
    except ValidationError:
        raise LayoutChanged from None


def _money(amount: Decimal, currency: str) -> Money:
    return Money(amount=amount, currency=currency.upper())


def _date(value: str) -> datetime | None:
    """WHMCS dates ("2026-12-01") as midnight UTC; "0000-00-00" means none."""
    if not value or value.startswith(NO_DATE):
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        raise LayoutChanged from None


def _ipv4(details: api.VpsDetails) -> str | None:
    addresses = list(details.ips.values()) if isinstance(details.ips, dict) else []
    return next((a for a in addresses if ":" not in a), addresses[0] if addresses else None)


def _percent(used: float | None, total: float | None) -> float | None:
    if used is None or not total:
        return None
    return round(used / total * 100, 2)
