"""ЮKassa: from a payment page link to the SBP link `https://qr.nspk.ru/...` (Timeweb, Aeza).

The hoster gives a `confirmation_url` of the ЮKassa page. The page itself starts an SBP
payment in three requests, which work without a browser (checked 2026-11-08):

1. `GET contract/sbp?orderId=...`: the HTML holds a CSRF token `<40 hex>:<13 digits>`;
2. `POST payment/sbp/start {"orderId"}` with the `x-csrf-token` header;
3. `POST payment/status {"orderId"}` until `payload.qrCodeData` appears.

The SBP payment lives about ten minutes, so the link is asked for when the owner presses
the button, never in advance.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from hostwatch.core.errors import HosterError, LayoutChanged, PluginError

log = logging.getLogger(__name__)

BASE = "https://yoomoney.ru/checkout/payments/v2/"
HOSTS = ("yoomoney.ru", "yookassa.ru")
NSPK = "https://qr.nspk.ru/"
CSRF = re.compile(r"\b[0-9a-f]{40}:\d{13}\b")
# The QR appears within a second or two; give up after about half a minute.
ATTEMPTS = 20
INTERVAL = 1.5
FAILED = {"error", "fail", "failed", "canceled", "cancelled"}


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _StatusPayload(_Answer):
    qrCodeData: str | None = None


class _Status(_Answer):
    status: str = ""
    payload: _StatusPayload | None = None


def order_id(confirmation_url: str) -> str:
    """`orderId` of a ЮKassa page link; LayoutChanged if the link is not one."""
    parts = urlsplit(confirmation_url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in HOSTS):
        raise LayoutChanged
    ids = parse_qs(parts.query).get("orderId")
    if not ids or not ids[0]:
        raise LayoutChanged
    return ids[0]


async def sbp_link(
    http: httpx.AsyncClient,
    confirmation_url: str,
    *,
    attempts: int = ATTEMPTS,
    interval: float = INTERVAL,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """The `qr.nspk.ru` link that pays the ЮKassa payment by SBP."""
    order = order_id(confirmation_url)
    page = await _send(http, "GET", "contract/sbp", params={"orderId": order})
    found = CSRF.search(page.text)
    if found is None:
        raise LayoutChanged
    headers = {
        "x-csrf-token": found.group(0),
        "Accept": "application/json",
        "Origin": "https://yoomoney.ru",
        "Referer": str(page.url),
    }
    await _send(http, "POST", "payment/sbp/start", json={"orderId": order}, headers=headers)
    for attempt in range(attempts):
        answer = await _send(
            http, "POST", "payment/status", json={"orderId": order}, headers=headers
        )
        try:
            status = _Status.model_validate_json(answer.content)
        except ValidationError:
            raise LayoutChanged from None
        link = status.payload.qrCodeData if status.payload else None
        if link:
            if not link.startswith(NSPK):
                raise LayoutChanged
            return link
        if status.status.lower() in FAILED:
            raise HosterError(f"ЮKassa: {status.status}")
        if attempt + 1 < attempts:
            await sleep(interval)
    raise HosterError("ЮKassa did not give an SBP link")


async def link_or_page(http: httpx.AsyncClient, confirmation_url: str) -> tuple[str, str | None]:
    """The SBP link and its QR text, or the ЮKassa page and no QR if the link does not come.

    The hoster's invoice is made by then, and the page pays it too: a failure here must
    not lose the invoice.
    """
    try:
        link = await sbp_link(http, confirmation_url)
    except (PluginError, TimeoutError) as exc:
        log.warning("no SBP link from ЮKassa (%s); giving its page", type(exc).__name__)
        return confirmation_url, None
    return link, link


async def _send(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    params: dict[str, str] | None = None,
    json: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = await http.request(
            method, BASE + path, params=params, json=json, headers=headers
        )
    except httpx.TimeoutException:
        raise TimeoutError from None
    except httpx.TransportError:
        raise HosterError("ЮKassa is unreachable") from None
    if response.status_code >= 400:
        raise HosterError(f"ЮKassa: HTTP {response.status_code}")
    return response
