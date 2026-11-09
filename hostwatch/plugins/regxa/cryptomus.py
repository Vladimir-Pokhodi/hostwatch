"""The Cryptomus payment page: which coins it takes, then the address for one of them.

The coins and networks of an invoice are the page's own answers, and they need no browser
(checked 2026-11-09): `POST api-pay.cryptomus.com/v1/payment/currencies {"uuid", "search"}`
lists the coins in the page's order, and `payment/methods` with the same body lists every
coin on every network (that day 83 coins and 97 pairs: USDT on 8 networks, USDC on 5, DAI
on 3, POL on 2, the rest on one, among them BTC, LTC, TRX, ETH, XMR and GRAM, ex-TON). The
page names a network by `network` in capitals and the `token_standard`: «TRON (TRC-20)»,
«TON».

The address cannot be had without a browser: the page asks for it with
`POST payment/method`, and that request carries an encrypted browser fingerprint. So a
headless tab clicks through the page the way a person does and reads the page's own
answer (checked 2026-11-08):

1. on load the page asks `payment/info`; if a network was chosen before, the address is
   already there;
2. «Select currency» → the coin, «Select network» → the network, «Proceed to the payment»;
   a coin with one network gets it at once, without «Select network»;
3. the answer to `payment/method` holds `address`, `payer_amount` and `payer_currency`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from hostwatch.core.errors import HosterError, LayoutChanged
from hostwatch.core.models import PayOption

PAY_PAGE = "https://pay.cryptomus.com/pay/"
PAGE_ORIGIN = "https://pay.cryptomus.com"
DOMAINS = ("cryptomus.com",)
API = "https://api-pay.cryptomus.com/v1/payment/"
LOAD_TIMEOUT = 60_000
STEP_TIMEOUT = 15_000
# An option id is `COIN.network` («USDT.tron»); it goes into callback data.
ID_SEP = "."
SAFE = re.compile(r"^[A-Za-z0-9_-]{1,12}$")


@dataclass(frozen=True)
class Network:
    """A coin on a network as the page names them: «USDT», «TRON (TRC-20)», `tron`.

    `single`: the coin has no other network, and the page chooses it by itself.
    """

    currency: str
    label: str
    code: str
    single: bool = False

    @property
    def option(self) -> PayOption:
        return PayOption(
            id=f"{self.currency}{ID_SEP}{self.code}", coin=self.currency, network=self.label
        )


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


class _Coin(_Answer):
    currency: str
    status: bool = True


class _Coins(_Answer):
    currencies: list[_Coin]


class _CoinsEnvelope(_Answer):
    state: int
    result: _Coins


class _Method(_Answer):
    currency: str
    network: str
    token_standard: str | None = None
    status: bool = True


class _Methods(_Answer):
    methods: list[_Method]


class _MethodsEnvelope(_Answer):
    state: int
    result: _Methods


async def networks(http: httpx.AsyncClient, url: str) -> list[Network]:
    """Every coin on every network the page `url` takes; coins in the page's order."""
    body = {"uuid": _uuid(url), "search": ""}
    coins = _coins(await _post(http, "currencies", body, url))
    usable = [
        m
        for m in _methods(await _post(http, "methods", body, url))
        if m.status and SAFE.match(m.currency) and SAFE.match(m.network)
    ]
    order = {coin: n for n, coin in enumerate(coins)}
    # Stable: a coin's networks keep the page's order, the popular one first.
    usable.sort(key=lambda m: order.get(m.currency, len(order)))
    count: dict[str, int] = {}
    for method in usable:
        count[method.currency] = count.get(method.currency, 0) + 1
    return [
        Network(m.currency, _label(m), m.network, single=count[m.currency] == 1) for m in usable
    ]


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
            chosen = f"{known.payer_currency or '?'} ({known.token_standard or known.network})"
            raise HosterError(f"the page already has {chosen}; change it on the page")
        return _address(known, network)
    await _click(page, "Select currency")
    await _click(page, network.currency)
    if not network.single:
        await _click(page, "Select network")
        await _click(page, network.label)
    async with page.expect_response(lambda r: r.url == API + "method") as chosen_answer:
        await _click(page, "Proceed to the payment")
    return _address(_payment(await (await chosen_answer.value).text()), network)


async def _click(page: Any, text: str) -> None:
    # Case-insensitive: the page writes «Ton» and shows it as «TON» through CSS.
    whole = re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)
    await page.get_by_text(whole).first.click(timeout=STEP_TIMEOUT)


async def _post(http: httpx.AsyncClient, endpoint: str, body: dict[str, str], url: str) -> bytes:
    headers = {"Accept": "application/json", "Origin": PAGE_ORIGIN, "Referer": url}
    try:
        response = await http.post(API + endpoint, json=body, headers=headers)
    except httpx.TimeoutException:
        raise TimeoutError from None
    except httpx.TransportError:
        raise HosterError("Cryptomus is unreachable") from None
    if response.status_code != 200:
        raise HosterError(f"Cryptomus {endpoint}: HTTP {response.status_code}")
    return response.content


def _label(method: _Method) -> str:
    name = method.network.upper()
    return f"{name} ({method.token_standard})" if method.token_standard else name


def _uuid(url: str) -> str:
    if not url.startswith(PAY_PAGE):
        raise LayoutChanged
    uuid = url[len(PAY_PAGE) :].split("?")[0].strip("/")
    if not uuid or "/" in uuid:
        raise LayoutChanged
    return uuid


def _coins(body: bytes) -> list[str]:
    try:
        envelope = _CoinsEnvelope.model_validate_json(body)
    except ValidationError:
        raise LayoutChanged from None
    if envelope.state != 0:
        raise LayoutChanged
    return [coin.currency for coin in envelope.result.currencies if coin.status]


def _methods(body: bytes) -> list[_Method]:
    try:
        envelope = _MethodsEnvelope.model_validate_json(body)
    except ValidationError:
        raise LayoutChanged from None
    if envelope.state != 0 or not envelope.result.methods:
        raise LayoutChanged
    return envelope.result.methods


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
