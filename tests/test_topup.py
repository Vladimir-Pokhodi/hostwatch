"""Top-up invoices of every plugin and the payment pages they lead to, on hand-made fixtures."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from hostwatch.core.errors import AuthExpired, BrowserBusy, HosterError, LayoutChanged
from hostwatch.core.forecast import end_of_next_month, needed_until
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
)
from hostwatch.core.payments import yookassa
from hostwatch.core.plugin import BrowserAuth, PluginContext, Secrets
from hostwatch.plugins.adminvps import AdminVpsPlugin
from hostwatch.plugins.aeza import BASE_URL, AezaPlugin
from hostwatch.plugins.regxa import SITE as REGXA
from hostwatch.plugins.regxa import RegxaPlugin, cryptomus
from hostwatch.plugins.timeweb import TimewebPlugin
from hostwatch.plugins.timeweb.plugin import UPDATE_TOKEN
from hostwatch.storage.repositories import PaymentRepository
from hostwatch.web.headless import HeadlessPages
from hostwatch.web.login import LoginService, LoginTarget

FIXTURES = Path(__file__).parent / "fixtures" / "topup"
NSPK = "https://qr.nspk.ru/AD10000000000000000000000000TEST"
YK = "https://yoomoney.ru/checkout/payments/v2/"
YK_PAGE = f"{YK}contract/sbp?orderId=30000000-000f-5000-8000-000000000001"
ADDRESS = "TTestAddressForFixtures00000000000"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def web() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def mock_yookassa(web: respx.MockRouter, waits: int = 1) -> respx.Route:
    web.get(f"{YK}contract/sbp").respond(200, text=page("yookassa-contract.html"))
    web.post(f"{YK}payment/sbp/start").respond(201, json=fixture("yookassa-start.json"))
    answers = [httpx.Response(201, json=fixture("yookassa-status-wait.json"))] * waits
    answers.append(httpx.Response(201, json=fixture("yookassa-status-qr.json")))
    return web.post(f"{YK}payment/status").mock(side_effect=answers)


class Saved:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def __call__(self, secrets: Secrets) -> None:
        self.calls.append(dict(secrets))


def context(
    secrets: dict[str, str], saved: Saved | None = None, pages: Any = None
) -> PluginContext:
    return PluginContext(secrets, httpx.AsyncClient(), saved or Saved(), pages)


async def no_sleep(_: float) -> None:
    pass


# ЮKassa


async def test_yookassa_gives_the_sbp_link(web: respx.MockRouter) -> None:
    status = mock_yookassa(web, waits=2)

    link = await yookassa.sbp_link(httpx.AsyncClient(), YK_PAGE, sleep=no_sleep)

    assert link == NSPK
    assert status.call_count == 3
    start = web.calls[1].request
    assert start.headers["x-csrf-token"].endswith(":1700000000000")
    assert json.loads(start.content) == {"orderId": "30000000-000f-5000-8000-000000000001"}


async def test_yookassa_refuses_foreign_links_and_missing_token(web: respx.MockRouter) -> None:
    with pytest.raises(LayoutChanged):
        await yookassa.sbp_link(httpx.AsyncClient(), "https://evil.example/?orderId=1")
    web.get(f"{YK}contract/sbp").respond(200, text="<html>no token</html>")
    with pytest.raises(LayoutChanged):
        await yookassa.sbp_link(httpx.AsyncClient(), YK_PAGE)


async def test_yookassa_gives_up_and_the_page_is_kept(web: respx.MockRouter) -> None:
    web.get(f"{YK}contract/sbp").respond(200, text=page("yookassa-contract.html"))
    web.post(f"{YK}payment/sbp/start").respond(201, json={"status": "accepted"})
    web.post(f"{YK}payment/status").respond(201, json=fixture("yookassa-status-wait.json"))
    with pytest.raises(HosterError):
        await yookassa.sbp_link(httpx.AsyncClient(), YK_PAGE, attempts=3, sleep=no_sleep)

    web.get(f"{YK}contract/sbp").respond(502)
    assert await yookassa.link_or_page(httpx.AsyncClient(), YK_PAGE) == (YK_PAGE, None)


async def test_yookassa_failed_payment(web: respx.MockRouter) -> None:
    web.get(f"{YK}contract/sbp").respond(200, text=page("yookassa-contract.html"))
    web.post(f"{YK}payment/sbp/start").respond(201, json={"status": "accepted"})
    web.post(f"{YK}payment/status").respond(201, json={"status": "error"})
    with pytest.raises(HosterError):
        await yookassa.sbp_link(httpx.AsyncClient(), YK_PAGE, sleep=no_sleep)


# Aeza


def aeza(web: respx.MockRouter) -> AezaPlugin:
    web.get(BASE_URL + "accounts/me").respond(json={"balance": 100, "currency": "eur"})
    return AezaPlugin(context({"api_key": "aeza-key-for-tests"}))


async def test_aeza_methods_are_sbp_and_coins(web: respx.MockRouter) -> None:
    plugin = aeza(web)
    web.get(BASE_URL + "billing/payment-methods").respond(json=fixture("aeza-payment-methods.json"))

    methods = await plugin.topup_methods()

    # The live list of 2026-11-09: SBP and all 13 coin-network pairs, not the cards.
    assert len(methods) == 14
    assert [(m.id, m.kind, m.minimum) for m in methods[:3]] == [
        ("yookassa:sbp", TopupKind.SBP, Money.of("0.50", "EUR")),
        ("pawnow:usdt_trc20", TopupKind.CRYPTO, Money.of("5", "EUR")),
        ("pawnow:bnb_bsc", TopupKind.CRYPTO, Money.of("5", "EUR")),
    ]
    assert "yookassa:bank_card" not in {m.id for m in methods}
    usdt = [(m.id, m.network) for m in methods if m.coin == "USDT"]
    assert usdt == [
        ("pawnow:usdt_trc20", "TRC20"),
        ("pawnow:usdt_bsc", "BEP20"),
        ("pawnow:usdt_eth", "ERC20"),
        ("pawnow:usdt_ton", "TON"),
    ]
    coins = {m.coin for m in methods if m.kind is TopupKind.CRYPTO}
    assert coins == {"USDT", "USDC", "BNB", "BTC", "ETH", "SOL", "LTC", "TRX", "TON"}
    btc = next(m for m in methods if m.id == "pawnow:btc_btc")
    assert (btc.coin, btc.network, btc.name) == ("BTC", None, "Bitcoin")
    assert methods[0].coin is None


async def test_aeza_sbp_invoice_becomes_an_nspk_link(web: respx.MockRouter) -> None:
    plugin = aeza(web)
    invoices = web.post(BASE_URL + "billing/invoices").respond(
        201, json=fixture("aeza-invoice-sbp.json")
    )
    mock_yookassa(web, waits=0)

    payment = await plugin.topup(Money.of("0.50", "EUR"), "yookassa:sbp")

    assert json.loads(invoices.calls[0].request.content) == {"method": "yookassa:sbp", "amount": 50}
    assert invoices.calls[0].request.headers["X-API-Key"] == "aeza-key-for-tests"
    assert payment.id == "100001"
    assert payment.amount == Money.of("0.50", "EUR")
    assert payment.url == payment.qr_data == NSPK


async def test_aeza_crypto_invoice_has_the_address(web: respx.MockRouter) -> None:
    plugin = aeza(web)
    web.post(BASE_URL + "billing/invoices").respond(201, json=fixture("aeza-invoice-crypto.json"))

    payment = await plugin.topup(Money.of("5", "EUR"), "pawnow:usdt_trc20")

    assert payment.address == ADDRESS
    assert payment.pay_amount == Decimal("6.52")
    assert payment.pay_currency == "USDT (TRC20)"
    assert payment.qr_data == f"{ADDRESS}?amount=6.52"
    assert payment.url is None


async def test_aeza_refusal_and_bad_amounts(web: respx.MockRouter) -> None:
    plugin = aeza(web)
    web.post(BASE_URL + "billing/invoices").respond(400, json={"message": "Amount too small"})
    with pytest.raises(HosterError, match="Amount too small"):
        await plugin.topup(Money.of("0.10", "EUR"), "yookassa:sbp")
    with pytest.raises(HosterError):
        await plugin.topup(Money.of("1.005", "EUR"), "yookassa:sbp")
    with pytest.raises(HosterError):
        await plugin.topup(Money.of("5", "USD"), "yookassa:sbp")


async def test_aeza_keeps_the_yookassa_page_when_sbp_fails(web: respx.MockRouter) -> None:
    plugin = aeza(web)
    web.post(BASE_URL + "billing/invoices").respond(201, json=fixture("aeza-invoice-sbp.json"))
    web.get(f"{YK}contract/sbp").respond(500)

    payment = await plugin.topup(Money.of("0.50", "EUR"), "yookassa:sbp")

    assert payment.url == YK_PAGE
    assert payment.qr_data is None


# Timeweb


SESSION = {"api_token": "api-token-for-tests", "refresh_token": "old-r", "device_token": "dev-d"}


def mock_timeweb(web: respx.MockRouter, refresh_cookie: bool = True) -> respx.Route:
    headers = {"Set-Cookie": "refresh_token=new-r; Path=/; HttpOnly"} if refresh_cookie else {}
    route = web.post(UPDATE_TOKEN).respond(
        201, json=fixture("timeweb-update-token.json"), headers=headers
    )
    web.get("https://timeweb.cloud/portal/v1/accounts/cl000001/payers").respond(
        json=fixture("timeweb-payers.json")
    )
    return route


async def test_timeweb_without_panel_session_asks_for_login(web: respx.MockRouter) -> None:
    plugin = TimewebPlugin(context({"api_token": "api-token-for-tests"}))
    with pytest.raises(AuthExpired):
        await plugin.topup(Money.of("500", "RUB"), "sbp")
    assert not web.calls


async def test_timeweb_invoice_rotates_the_session_first(web: respx.MockRouter) -> None:
    saved = Saved()
    plugin = TimewebPlugin(context(dict(SESSION), saved))
    update = mock_timeweb(web)
    invoices = web.post("https://timeweb.cloud/portal/v4/invoices").respond(
        201, json=fixture("timeweb-invoice.json")
    )
    mock_yookassa(web, waits=0)

    payment = await plugin.topup(Money.of("500", "RUB"), "sbp")

    assert update.calls[0].request.headers["Cookie"] == "refresh_token=old-r; device_token=dev-d"
    assert saved.calls[0] == {**SESSION, "refresh_token": "new-r"}
    # The new token was saved before the access token went anywhere.
    assert [c.request.url.path for c in web.calls][:2] == [
        "/api/v1/auth/update-token",
        "/portal/v1/accounts/cl000001/payers",
    ]
    body = json.loads(invoices.calls[0].request.content)
    assert body["total_amount"] == 500
    assert body["payer"] == {"id": 100003}
    assert body["payment_method"]["type"] == "sbp"
    assert invoices.calls[0].request.headers["Authorization"].startswith("Bearer eyJ")
    assert payment.id == "100004"
    assert payment.url == payment.qr_data == NSPK


async def test_timeweb_token_in_the_body_is_saved_too(web: respx.MockRouter) -> None:
    saved = Saved()
    plugin = TimewebPlugin(context(dict(SESSION), saved))
    answer = fixture("timeweb-update-token.json")
    answer["tokens"]["refresh_token"] = "body-r"
    web.post(UPDATE_TOKEN).respond(201, json=answer)
    web.get("https://timeweb.cloud/portal/v1/accounts/cl000001/payers").respond(json=[])

    with pytest.raises(HosterError):
        await plugin.topup(Money.of("500", "RUB"), "sbp")
    assert saved.calls[-1]["refresh_token"] == "body-r"


async def test_timeweb_saves_the_cookie_even_if_the_body_changed(web: respx.MockRouter) -> None:
    saved = Saved()
    plugin = TimewebPlugin(context(dict(SESSION), saved))
    web.post(UPDATE_TOKEN).respond(
        201, json={"unexpected": True}, headers={"Set-Cookie": "refresh_token=new-r; Path=/"}
    )
    with pytest.raises(LayoutChanged):
        await plugin.topup(Money.of("500", "RUB"), "sbp")
    assert saved.calls == [{**SESSION, "refresh_token": "new-r"}]


async def test_timeweb_dead_session_and_small_amount(web: respx.MockRouter) -> None:
    web.post(UPDATE_TOKEN).respond(403, json={"status_code": 403})
    with pytest.raises(AuthExpired):
        await TimewebPlugin(context(dict(SESSION))).topup(Money.of("500", "RUB"), "sbp")

    web.post(UPDATE_TOKEN).respond(
        201, json=fixture("timeweb-update-token.json"), headers={"Set-Cookie": "refresh_token=n"}
    )
    web.get("https://timeweb.cloud/portal/v1/accounts/cl000001/payers").respond(
        json=fixture("timeweb-payers.json")
    )
    web.post("https://timeweb.cloud/portal/v4/invoices").respond(
        400, json={"error_code": "amount_less_minimal", "error_msg": "Amount is less"}
    )
    with pytest.raises(HosterError, match="amount_less_minimal"):
        await TimewebPlugin(context(dict(SESSION))).topup(Money.of("500", "RUB"), "sbp")
    with pytest.raises(HosterError):
        await TimewebPlugin(context(dict(SESSION))).topup(Money.of("500.50", "RUB"), "sbp")


async def test_timeweb_offers_sbp_from_500() -> None:
    methods = await TimewebPlugin(context({"api_token": "t"})).topup_methods()
    assert [(m.id, m.minimum) for m in methods] == [("sbp", Money.of("500", "RUB"))]


# Regxa


REGXA_SECRETS = {"rgx.session-token": "rgx-cookie-for-tests"}


CRYPTOMUS = "https://api-pay.cryptomus.com/v1/payment/"
PAY_URL = "https://pay.cryptomus.com/pay/00000000-0000-4000-8000-000000000006"


def mock_regxa(web: respx.MockRouter, coins: bool = True) -> respx.Route:
    web.get(f"{REGXA}/api/auth/session").respond(json={"user": {"client_id": "100002"}})
    add = web.post(f"{REGXA}/api/billing/addfunds").respond(json=fixture("regxa-addfunds.json"))
    web.post(f"{REGXA}/api/billing/cryptomus/create-payment").respond(
        json=fixture("regxa-create-payment.json")
    )
    if coins:
        web.post(f"{CRYPTOMUS}currencies").respond(json=fixture("cryptomus-currencies.json"))
        web.post(f"{CRYPTOMUS}methods").respond(json=fixture("cryptomus-methods.json"))
    else:
        web.post(f"{CRYPTOMUS}currencies").respond(500)
    return add


class FakePages:
    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.opened: list[tuple[str, ...]] = []

    @asynccontextmanager
    async def open(self, allowed_domains: tuple[str, ...]) -> AsyncIterator[object]:
        if self.busy:
            raise BrowserBusy
        self.opened.append(allowed_domains)
        yield object()


async def test_regxa_offers_one_method_and_the_coins_come_with_the_invoice(
    web: respx.MockRouter,
) -> None:
    add = mock_regxa(web)
    plugin = RegxaPlugin(context(dict(REGXA_SECRETS)))
    assert [m.id for m in await plugin.topup_methods()] == ["cryptomus"]

    payment = await plugin.topup(Money.of("5", "USD"), "cryptomus")

    assert json.loads(add.calls[0].request.content) == {"amount": 5.0}
    assert add.calls[0].request.headers["Cookie"] == "rgx.session-token=rgx-cookie-for-tests"
    assert payment.id == "100005"
    assert payment.url == PAY_URL
    assert payment.address is None
    # The page's coin order (USDT, BTC, LTC, ...), each coin's networks in the page's order.
    coins = list(dict.fromkeys(o.coin for o in payment.options))
    assert coins == [
        "USDT", "BTC", "LTC", "TRX", "ETH", "XMR", "POL", "BNB", "USDC", "SOL", "DOGE", "GRAM"
    ]  # fmt: skip
    usdt = [(o.id, o.network) for o in payment.options if o.coin == "USDT"]
    assert usdt == [
        ("USDT.bsc", "BSC (BEP-20)"),
        ("USDT.tron", "TRON (TRC-20)"),
        ("USDT.eth", "ETH (ERC-20)"),
        ("USDT.polygon", "POLYGON (ERC-20)"),
        ("USDT.arbitrum", "ARBITRUM"),
        ("USDT.avalanche", "AVALANCHE"),
        ("USDT.sol", "SOL"),
        ("USDT.ton", "TON"),
    ]
    assert [o.id for o in payment.options if o.coin in ("BTC", "GRAM")] == ["BTC.btc", "GRAM.ton"]
    sent = web.calls.last.request
    assert json.loads(sent.content) == {
        "uuid": "00000000-0000-4000-8000-000000000006",
        "search": "",
    }
    assert sent.headers["Origin"] == "https://pay.cryptomus.com"
    # The Regxa session cookie does not go to Cryptomus.
    assert "Cookie" not in sent.headers


async def test_regxa_without_the_coin_list_gives_the_page(web: respx.MockRouter) -> None:
    mock_regxa(web, coins=False)
    payment = await RegxaPlugin(context(dict(REGXA_SECRETS))).topup(
        Money.of("5", "USD"), "cryptomus"
    )
    assert payment.url == PAY_URL
    assert payment.options == ()


async def test_regxa_reads_the_address_in_the_browser(
    web: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_regxa(web)
    seen: list[cryptomus.Network] = []

    async def address(page: object, url: str, network: cryptomus.Network) -> cryptomus.Address:
        seen.append(network)
        payment = cryptomus._payment(json.dumps(fixture("cryptomus-method.json")))
        return cryptomus._address(payment, network)

    monkeypatch.setattr(cryptomus, "address", address)
    pages = FakePages()
    plugin = RegxaPlugin(context(dict(REGXA_SECRETS), pages=pages))
    invoice = await plugin.topup(Money.of("5", "USD"), "cryptomus")

    payment = await plugin.pay_with(invoice, "USDT.tron")

    assert pages.opened == [("cryptomus.com",)]
    assert seen == [cryptomus.Network("USDT", "TRON (TRC-20)", "tron", single=False)]
    assert payment.address == payment.qr_data == ADDRESS
    assert payment.pay_amount == Decimal("5.02")
    assert payment.pay_currency == "USDT (TRC-20)"
    assert payment.expires_at == datetime(2026, 11, 8, 13, 0, tzinfo=UTC)
    assert payment.options == ()

    with pytest.raises(LayoutChanged):  # the fixture answers USDT on TRON, not BTC
        await plugin.pay_with(invoice, "BTC.btc")
    assert seen[-1] == cryptomus.Network("BTC", "BTC", "btc", single=True)


async def test_regxa_choice_refusals(web: respx.MockRouter) -> None:
    mock_regxa(web)
    invoice = Payment(
        id="100005",
        amount=Money.of("5", "USD"),
        method="cryptomus",
        created_at=datetime(2026, 11, 9, tzinfo=UTC),
        url=PAY_URL,
    )
    with pytest.raises(HosterError, match="does not take"):
        await RegxaPlugin(context(dict(REGXA_SECRETS), pages=FakePages())).pay_with(
            invoice, "USDT.nowhere"
        )
    with pytest.raises(HosterError, match="no browser"):
        await RegxaPlugin(context(dict(REGXA_SECRETS))).pay_with(invoice, "USDT.tron")
    with pytest.raises(BrowserBusy):
        await RegxaPlugin(context(dict(REGXA_SECRETS), pages=FakePages(busy=True))).pay_with(
            invoice, "USDT.tron"
        )
    elsewhere = invoice.model_copy(update={"url": "https://example.com/pay/x"})
    with pytest.raises(HosterError, match="not a Cryptomus"):
        await RegxaPlugin(context(dict(REGXA_SECRETS))).pay_options(elsewhere)


async def test_regxa_refusals(web: respx.MockRouter) -> None:
    web.get(f"{REGXA}/api/auth/session").respond(json={"user": {"client_id": "100002"}})
    web.post(f"{REGXA}/api/billing/addfunds").respond(json={"success": False, "message": "No"})
    plugin = RegxaPlugin(context(dict(REGXA_SECRETS)))
    with pytest.raises(HosterError, match="No"):
        await plugin.topup(Money.of("5", "USD"), "cryptomus")
    with pytest.raises(HosterError):
        await plugin.topup(Money.of("4", "USD"), "cryptomus")
    with pytest.raises(HosterError, match="unknown method"):
        await plugin.topup(Money.of("5", "USD"), "usdt_trc20")
    web.get(f"{REGXA}/api/auth/session").respond(json={})
    with pytest.raises(AuthExpired):
        await RegxaPlugin(context(dict(REGXA_SECRETS))).topup(Money.of("5", "USD"), "cryptomus")


def test_cryptomus_answers() -> None:
    network = cryptomus.Network("USDT", "TRON (TRC-20)", "tron")
    fresh = cryptomus._payment(json.dumps(fixture("cryptomus-info-new.json")))
    assert fresh.address is None
    with pytest.raises(LayoutChanged):
        cryptomus._address(fresh, network)
    chosen = cryptomus._payment(json.dumps(fixture("cryptomus-method.json")))
    with pytest.raises(LayoutChanged):
        cryptomus._address(chosen, cryptomus.Network("USDT", "TON", "ton"))
    with pytest.raises(LayoutChanged):
        cryptomus._payment('{"state": 1, "result": {"payment": {}}}')
    with pytest.raises(LayoutChanged):
        cryptomus._methods(b'{"state": 0, "result": {"methods": []}}')
    with pytest.raises(LayoutChanged):
        cryptomus._coins(b'{"state": 0, "result": {}}')
    with pytest.raises(LayoutChanged):
        cryptomus._uuid("https://pay.cryptomus.com/pay/")


# AdminVPS


ADMINVPS_SECRETS = {"WHMCSabc": "whmcs-cookie-for-tests", "__ddg1_": "ddg"}
CLIENTAREA = "https://my.adminvps.ru/clientarea.php"


def mock_adminvps(web: respx.MockRouter) -> respx.Route:
    web.get(CLIENTAREA, params={"action": "addfunds"}).respond(
        text=page("adminvps-addfunds-form.html")
    )
    add = web.post(CLIENTAREA).respond(text=page("adminvps-addfunds-gateway.html"))
    web.post("https://paymaster.ru/Payment/Init").respond(
        302, headers={"Location": "https://paymaster.ru/cpay/0123456789abcdef0123456789abcdef"}
    )
    web.get("https://paymaster.ru/cpay/0123456789abcdef0123456789abcdef").respond(text="<html/>")
    return add


async def test_adminvps_sbp_through_paymaster(web: respx.MockRouter) -> None:
    add = mock_adminvps(web)
    auth = web.post("https://paymaster.ru/cpay/api/Auth/AuthPaymentExternal").respond(
        json=fixture("paymaster-auth.json")
    )

    payment = await AdminVpsPlugin(context(dict(ADMINVPS_SECRETS))).topup(
        Money.of("100", "RUB"), "paymaster_sbp"
    )

    form = httpx.QueryParams(add.calls[0].request.content.decode())
    assert dict(form) == {
        "token": "0000000000000000000000000000000000000001",
        "amount": "100",
        "paymentmethod": "paymaster_sbp",
    }
    assert "WHMCSabc=whmcs-cookie-for-tests" in add.calls[0].request.headers["Cookie"]
    init = web.calls[2].request
    assert "Cookie" not in init.headers  # the cabinet session stays with the cabinet
    assert httpx.QueryParams(init.content.decode())["LMI_PAYMENT_NO"] == "100007"
    request = auth.calls[0].request
    assert request.headers["x-public-id"] == "0123456789abcdef0123456789abcdef"
    assert json.loads(request.content) == {"paymentMethodAlias": "SBP"}
    assert payment.id == "100007"
    assert payment.url == payment.qr_data == NSPK


async def test_adminvps_keeps_the_invoice_when_paymaster_fails(web: respx.MockRouter) -> None:
    mock_adminvps(web)
    web.post("https://paymaster.ru/cpay/api/Auth/AuthPaymentExternal").respond(500)

    payment = await AdminVpsPlugin(context(dict(ADMINVPS_SECRETS))).topup(
        Money.of("100", "RUB"), "paymaster_sbp"
    )

    assert payment.url == "https://my.adminvps.ru/viewinvoice.php?id=100007"
    assert payment.qr_data is None


async def test_adminvps_limits_and_lost_session(web: respx.MockRouter) -> None:
    plugin = AdminVpsPlugin(context(dict(ADMINVPS_SECRETS)))
    with pytest.raises(HosterError):
        await plugin.topup(Money.of("99", "RUB"), "paymaster_sbp")
    with pytest.raises(HosterError):
        await plugin.topup(Money.of("100", "RUB"), "paymaster")
    web.get(CLIENTAREA).respond(302, headers={"Location": "/login"})
    with pytest.raises(AuthExpired):
        await plugin.topup(Money.of("100", "RUB"), "paymaster_sbp")


async def test_adminvps_methods() -> None:
    methods = await AdminVpsPlugin(context(dict(ADMINVPS_SECRETS))).topup_methods()
    assert [(m.id, m.kind, m.minimum) for m in methods] == [
        ("paymaster_sbp", TopupKind.SBP, Money.of("100", "RUB"))
    ]


# Core pieces


async def test_headless_pages_refuse_while_a_login_runs() -> None:
    launched: list[object] = []

    async def launcher(pw: object) -> Any:
        launched.append(pw)
        raise AssertionError("must not launch")

    pages = HeadlessPages(launcher, busy=lambda: True)
    with pytest.raises(BrowserBusy):
        async with pages.open(("cryptomus.com",)):
            pass
    assert not launched


def test_payment_needs_a_link_or_an_address() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        Payment(id="1", amount=Money.of("1", "EUR"), method="x", created_at=now)
    Payment(id="1", amount=Money.of("1", "EUR"), method="x", created_at=now, address="T")


def _monthly(name: str, price: str, next_at: datetime) -> Server:
    return Server(
        external_id=name,
        name=name,
        kind=ServerKind.VPS,
        status=ServerStatus.RUNNING,
        raw_status="active",
        price=Money.of(price, "EUR"),
        period_months=1,
        next_charge_at=next_at,
        autorenew=Autorenew.ON,
        billing=BillingModel.PERIODIC,
    )


def test_needed_until_counts_charges_up_to_the_date() -> None:
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    until = end_of_next_month(now, ZoneInfo("Europe/Moscow"))
    assert until == datetime(2027, 1, 1, tzinfo=ZoneInfo("Europe/Moscow"))
    servers = [_monthly("a", "4.50", now + timedelta(days=7))]  # 15.11 and 15.12
    balance = Balance(money=Money.of("2", "EUR"))

    assert needed_until(balance, servers, now, until) == Money.of("7.00", "EUR")
    rich = Balance(money=Money.of("100", "EUR"))
    assert needed_until(rich, servers, now, until) == Money.of("0", "EUR")


def test_needed_until_hourly() -> None:
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    hourly = Server(
        external_id="h",
        name="h",
        kind=ServerKind.VPS,
        status=ServerStatus.RUNNING,
        raw_status="on",
        price=Money.of("1", "RUB"),
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.HOURLY,
    )
    need = needed_until(
        Balance(money=Money.of("4", "RUB")), [hourly], now, now + timedelta(hours=10)
    )
    assert need == Money.of("6", "RUB")


def test_payments_repository(conn: Any) -> None:
    repo = PaymentRepository(conn)
    payment = Payment(
        id="100001",
        amount=Money.of("0.50", "EUR"),
        method="yookassa:sbp",
        url=NSPK,
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
    )
    row = repo.add(_account(conn), payment)
    record = repo.get(row)
    assert (record.external_id, record.status) == ("100001", "created")
    assert record.amount == payment.amount
    repo.set_status(row, "waiting", Money.of("1.00", "EUR"))
    assert repo.get(row).balance_before == Money.of("1.00", "EUR")
    repo.set_status(row, "arrived")
    assert repo.get(row).status == "arrived"


def _account(conn: Any) -> int:
    with conn:
        conn.execute(
            "INSERT INTO accounts (id, plugin_id, label, secrets) VALUES (1, 'aeza', '', x'00')"
        )
    return 1


async def test_login_service_is_busy_while_a_link_is_fresh() -> None:
    clock = [0.0]

    async def launcher(pw: object) -> Any:
        raise AssertionError("not launched")

    service = LoginService(launcher, clock=lambda: clock[0], link_ttl=600)
    assert not service.busy()
    auth = BrowserAuth("https://example.com/login", ("example.com",), ("sid",))
    await service.issue(LoginTarget("fake", "Fake", auth))
    assert service.busy()
    clock[0] = 601
    assert not service.busy()
