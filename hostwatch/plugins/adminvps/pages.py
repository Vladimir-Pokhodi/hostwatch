"""Reading the AdminVPS cabinet: WHMCS pages and the SolusVM module's JSON.

Every parser raises `LayoutChanged` when the part it needs is not where it was:
an empty answer must never look like "no servers" or "zero balance".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError
from selectolax.lexbor import LexborHTMLParser, LexborNode

from hostwatch.core.errors import LayoutChanged
from hostwatch.core.models import Autorenew, ServerAction
from hostwatch.plugins.adminvps import control

# WHMCS shows dates without a time; AdminVPS charges by Moscow time.
TZ = ZoneInfo("Europe/Moscow")
NUMBER = re.compile(r"-?\d[\d\s]*(?:[.,]\d+)?")
SERVICE_ID = re.compile(r"[?&](?:amp;)?id=(\d+)")
INVOICE_LINK = re.compile(r"viewinvoice\.php\?id=(\d+)")
VSERVER_ID = re.compile(r"\bvar\s+vserverid\s*=\s*['\"]?(\d+)")
TRAFFIC = re.compile(r"^\s*([\d.,]+)\s*([KMGTP]?)i?B\s*$", re.IGNORECASE)
TRAFFIC_UNITS = {"": 1 / 1024**3, "K": 1 / 1024**2, "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024**2}
RUB = ("руб", "₽", "RUB")
# Billing cycle captions of WHMCS, Russian and English.
PERIODS = {
    "ежемесячно": 1,
    "monthly": 1,
    "ежеквартально": 3,
    "quarterly": 3,
    "раз в полгода": 6,
    "полугодично": 6,
    "semi-annually": 6,
    "ежегодно": 12,
    "annually": 12,
    "раз в два года": 24,
    "раз в 2 года": 24,
    "двухгодично": 24,
    "biennially": 24,
    "раз в три года": 36,
    "раз в 3 года": 36,
    "трехгодично": 36,
    "triennially": 36,
}


@dataclass(frozen=True)
class ServiceRow:
    """One row of `clientarea.php?action=services`."""

    id: str
    product: str
    domain: str | None
    price: Decimal | None
    period_months: int | None
    next_due: datetime | None
    # WHMCS status from the label class: active, suspended, pending, terminated, ...
    status: str
    status_text: str


@dataclass(frozen=True)
class ServiceDetails:
    """What the service page adds: the SolusVM server id, the IP, the autorenewal and the
    actions it has buttons for."""

    vserver_id: str | None
    ip: str | None
    autorenew: Autorenew
    actions: frozenset[ServerAction] = frozenset()


class ClientData(BaseModel):
    """`modules/servers/solusvmpro/get_client_data.php?vserverid=...`; only what we read."""

    model_config = ConfigDict(extra="ignore")

    state: str | None = None
    mainip: str | None = None
    bandwidthused: str | None = None


def parse(html: str) -> LexborHTMLParser:
    return LexborHTMLParser(html)


def balance(page: LexborHTMLParser) -> Decimal:
    """The balance in the page header: `<a class="balance-link">4321.09 руб.</a>`."""
    link = page.css_first("a.balance-link")
    if link is None:
        raise LayoutChanged
    text = link.text(strip=True)
    if not any(sign in text for sign in RUB):
        raise LayoutChanged
    amount = _decimal(text)
    if amount is None:
        raise LayoutChanged
    return amount


def services(page: LexborHTMLParser) -> list[ServiceRow]:
    table = page.css_first("table#tableServicesList")
    if table is None:
        raise LayoutChanged
    headers = table.css("thead th")
    if len(headers) < 4:
        raise LayoutChanged
    return [_row(tr) for tr in table.css("tbody tr") if tr.css("td")]


def _row(tr: LexborNode) -> ServiceRow:
    cells = tr.css("td")
    if len(cells) < 4:
        raise LayoutChanged
    name, price, due, status = cells[:4]
    service_id = _service_id(tr)
    product = name.css_first("strong")
    label = status.css_first("span.status")
    if product is None or label is None:
        raise LayoutChanged
    status_slug = next(
        (c.removeprefix("status-") for c in _classes(label) if c.startswith("status-")), None
    )
    if status_slug is None:
        raise LayoutChanged
    domain = name.css_first("a")
    amount, period = _price(price)
    return ServiceRow(
        id=service_id,
        product=product.text(strip=True),
        domain=(domain.text(strip=True) or None) if domain is not None else None,
        price=amount,
        period_months=period,
        next_due=_due(due),
        status=status_slug,
        status_text=label.text(strip=True),
    )


def _service_id(tr: LexborNode) -> str:
    for link in tr.css("a[href]"):
        found = SERVICE_ID.search(link.attributes.get("href") or "")
        if found and "productdetails" in (link.attributes.get("href") or ""):
            return found.group(1)
    found = SERVICE_ID.search(tr.attributes.get("onclick") or "")
    if found is None:
        raise LayoutChanged
    return found.group(1)


def _price(cell: LexborNode) -> tuple[Decimal | None, int | None]:
    text = cell.text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None, None
    if not any(sign in lines[0] for sign in RUB):
        raise LayoutChanged  # another currency: the plugin knows only rubles
    amount = _decimal(cell.attributes.get("data-order") or "") or _decimal(lines[0])
    if amount is None:
        raise LayoutChanged
    period = PERIODS.get(lines[1].lower()) if len(lines) > 1 else None
    return amount, period


def _due(cell: LexborNode) -> datetime | None:
    """`<span class="hidden">2026-11-27</span>27.11.2026`; free services show a dash."""
    hidden = cell.css_first("span.hidden")
    raw = hidden.text(strip=True) if hidden is not None else ""
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=TZ)


def details(page: LexborHTMLParser, html: str) -> ServiceDetails:
    vserver = VSERVER_ID.search(html)
    if vserver is None and "solusvmpro" in html:
        raise LayoutChanged  # a SolusVM service page that no longer names its server
    ip_input = page.css_first("input#dedicatedip-hidden")
    ip = (ip_input.attributes.get("value") or None) if ip_input is not None else None
    return ServiceDetails(
        vserver_id=vserver.group(1) if vserver else None,
        ip=ip,
        autorenew=autorenew(page),
        actions=control.actions(page),
    )


def autorenew(page: LexborHTMLParser) -> Autorenew:
    """ModulesGarden HostingRenewals: the page has both buttons, one of them `hidden`."""
    turn_on = page.css_first("a.mg-hosting-renewals-turn-on-button")
    turn_off = page.css_first("a.mg-hosting-renewals-turn-off-button")
    if turn_on is None and turn_off is None:
        return Autorenew.NOT_AVAILABLE
    on_shown = turn_on is not None and "hidden" not in turn_on.attributes
    off_shown = turn_off is not None and "hidden" not in turn_off.attributes
    if off_shown and not on_shown:
        return Autorenew.ON
    if on_shown and not off_shown:
        return Autorenew.OFF
    raise LayoutChanged


def client_data(body: str) -> ClientData:
    try:
        return ClientData.model_validate_json(body)
    except ValidationError:
        raise LayoutChanged from None


def traffic_gb(text: str | None) -> float | None:
    """Gigabytes from "417.7 MB". SolusVM counts in binary units."""
    if not text:
        return None
    found = TRAFFIC.match(text)
    if found is None:
        raise LayoutChanged
    number, unit = found.groups()
    return float(number.replace(",", ".")) * TRAFFIC_UNITS[unit.upper()]


def form_token(page: LexborHTMLParser) -> str:
    """CSRF `token` of the «Add funds» form."""
    field = page.css_first('form[action*="addfunds"] input[name="token"]')
    token = (field.attributes.get("value") or "") if field is not None else ""
    if not token:
        raise LayoutChanged
    return token


def is_logged_in(html: str) -> bool:
    return "logout.php" in html


def _decimal(text: str) -> Decimal | None:
    found = NUMBER.search(text)
    if found is None:
        return None
    try:
        return Decimal(re.sub(r"\s", "", found.group()).replace(",", "."))
    except InvalidOperation:
        return None


def _classes(node: LexborNode) -> list[str]:
    return (node.attributes.get("class") or "").split()
