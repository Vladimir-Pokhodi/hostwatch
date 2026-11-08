"""The Cryptomus payment page: choose a coin and a network, read the address and the amount.

Without a browser the address cannot be had: the page asks for it with
`POST api-pay.cryptomus.com/v1/payment/method`, and that request carries an encrypted
browser fingerprint. So a headless tab clicks through the page the way a person does and
reads the page's own answer (checked 2026-11-08):

1. on load the page asks `payment/info`; if a network was chosen before, the address is
   already there;
2. «Select currency» → the coin, «Select network» → the network, «Proceed to the payment»;
3. the answer to `payment/method` holds `address`, `payer_amount` and `payer_currency`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from hostwatch.core.errors import LayoutChanged

PAY_PAGE = "https://pay.cryptomus.com/pay/"
DOMAINS = ("cryptomus.com",)
API = "https://api-pay.cryptomus.com/v1/payment/"
LOAD_TIMEOUT = 60_000
STEP_TIMEOUT = 15_000


@dataclass(frozen=True)
class Network:
    """A coin on a network as the page names them: «USDT», «TRON (TRC-20)», `tron`."""

    currency: str
    label: str
    code: str


@dataclass(frozen=True)
class Address:
    address: str
    amount: Decimal
    currency: str
    expires_at: datetime | None


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _Payment(_Answer):
    address: str | None = None
    payer_amount: Decimal | None = None
    payer_currency: str | None = None
    network: str | None = None
    token_standard: str | None = None
    expired_at: AwareDatetime | None = None


class _Result(_Answer):
    payment: _Payment


class _Envelope(_Answer):
    state: int
    result: _Result


async def address(page: Any, url: str, network: Network) -> Address:
    """Address and amount for `network` on the Cryptomus page `url` (a Playwright page)."""
    if not url.startswith(PAY_PAGE):
        raise LayoutChanged
    async with page.expect_response(lambda r: r.url == API + "info") as loaded:
        await page.goto(url, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT)
    known = _payment(await (await loaded.value).text())
    if known.address:
        # A network was chosen before: by the bot for this invoice, or by the owner.
        if (known.payer_currency, known.network) != (network.currency, network.code):
            raise LayoutChanged
        return _address(known, network)
    await _click(page, "Select currency")
    await _click(page, network.currency)
    await _click(page, "Select network")
    await _click(page, network.label)
    async with page.expect_response(lambda r: r.url == API + "method") as chosen:
        await _click(page, "Proceed to the payment")
    return _address(_payment(await (await chosen.value).text()), network)


async def _click(page: Any, text: str) -> None:
    # Case-insensitive: the page writes «Ton» and shows it as «TON» through CSS.
    whole = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
    await page.get_by_text(whole).first.click(timeout=STEP_TIMEOUT)


def _payment(body: str) -> _Payment:
    try:
        envelope = _Envelope.model_validate_json(body)
    except ValidationError:
        raise LayoutChanged from None
    if envelope.state != 0:
        raise LayoutChanged
    return envelope.result.payment


def _address(payment: _Payment, network: Network) -> Address:
    if not payment.address or payment.payer_amount is None or not payment.payer_currency:
        raise LayoutChanged
    if (payment.payer_currency, payment.network) != (network.currency, network.code):
        raise LayoutChanged
    standard = payment.token_standard or network.label
    return Address(
        address=payment.address,
        amount=payment.payer_amount,
        currency=f"{payment.payer_currency} ({standard})",
        expires_at=payment.expired_at,
    )
