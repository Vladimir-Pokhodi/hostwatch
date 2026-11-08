"""Control requests of each plugin, as the hosters' cabinets send them (stage 10).

None of these requests was sent to a live server: there is no test server, and every
account runs the owner's production servers. The request shapes come from the cabinets'
own scripts (Aeza, Regxa, AdminVPS HostingRenewals), the API reference (Timeweb) and the
service page forms (AdminVPS power). Answers to the changing requests are built here;
the AdminVPS modals were recorded live with the read-only request that opens them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hostwatch.core.errors import AuthExpired, HosterError, LayoutChanged
from hostwatch.core.models import Money, PowerAction, ServerAction
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.plugins.adminvps import CLIENTAREA, AdminVpsPlugin, control, pages
from hostwatch.plugins.aeza import BASE_URL, AezaPlugin
from hostwatch.plugins.regxa import SITE as RGX
from hostwatch.plugins.regxa import RegxaPlugin
from hostwatch.plugins.timeweb import TimewebPlugin
from tests.replay import load, replay

FIXTURES = Path(__file__).parent / "fixtures"


async def ignore(_: Secrets) -> None:
    pass


def body(request: httpx.Request) -> Any:
    return json.loads(request.content) if request.content else None


# Aeza


def aeza() -> AezaPlugin:
    return AezaPlugin(PluginContext({"api_key": "aeza-key-for-tests"}, httpx.AsyncClient(), ignore))


@pytest.fixture
def aeza_api() -> Iterator[respx.MockRouter]:
    records = load("aeza")
    with respx.mock(assert_all_called=False) as router:
        # A renewal reads the services again: answer every time, not once.
        services = next(r for r in records if r["url"].endswith("/services"))
        router.get(f"{BASE_URL}services").respond(200, json=services["json"])
        replay(router, records)
        yield router


@pytest.mark.parametrize(
    ("action", "path", "sent"),
    [
        (PowerAction.START, "ctl/resume", None),
        (PowerAction.STOP, "ctl/suspend", {"force": False}),
        (PowerAction.RESTART, "ctl/restart", {"force": False}),
    ],
)
async def test_aeza_power(
    aeza_api: respx.MockRouter, action: PowerAction, path: str, sent: Any
) -> None:
    route = aeza_api.post(f"{BASE_URL}services/100006/{path}").respond(200, json={"data": {}})
    result = await aeza().power("100006", action)
    assert result.ok
    request = route.calls.last.request
    assert body(request) == sent
    assert request.headers["X-API-Key"] == "aeza-key-for-tests"


async def test_aeza_error_in_a_2xx_answer_is_a_failure(aeza_api: respx.MockRouter) -> None:
    aeza_api.post(f"{BASE_URL}services/100006/ctl/restart").respond(
        200, json={"error": {"slug": "task_running", "message": "Task is already running"}}
    )
    with pytest.raises(HosterError, match="Task is already running"):
        await aeza().power("100006", PowerAction.RESTART)


async def test_aeza_autorenew(aeza_api: respx.MockRouter) -> None:
    route = aeza_api.patch(f"{BASE_URL}services/100006").respond(200, json={"data": {}})
    assert (await aeza().set_autorenew("100006", False)).ok
    assert body(route.calls.last.request) == {"autoProlong": False}


async def test_aeza_renew_price_and_renewal(aeza_api: respx.MockRouter) -> None:
    aeza_api.get(f"{BASE_URL}services/100006/prices").respond(
        200, json={"prices": {"hour": 4, "month": 593, "year": 6263}}
    )
    prolong = aeza_api.post(f"{BASE_URL}services/100006/prolong").respond(
        200, json={"data": {"transactionId": 1}}
    )
    plugin = aeza()
    price = await plugin.renew_price("100006")
    assert price == Money.of("5.93", "EUR")
    assert not prolong.called

    result = await plugin.renew("100006", price)

    assert result.ok
    assert body(prolong.calls.last.request) == {
        "method": "balance",
        "paymentTerm": "month",
        "count": 1,
    }


async def test_aeza_renewal_at_another_price_is_not_sent(aeza_api: respx.MockRouter) -> None:
    aeza_api.get(f"{BASE_URL}services/100006/prices").respond(200, json={"prices": {"month": 650}})
    prolong = aeza_api.post(f"{BASE_URL}services/100006/prolong").respond(200, json={})
    with pytest.raises(HosterError, match="costs"):
        await aeza().renew("100006", Money.of("5.93", "EUR"))
    assert not prolong.called


async def test_aeza_bad_ids_never_reach_the_url(aeza_api: respx.MockRouter) -> None:
    with pytest.raises(HosterError):
        await aeza().power("../accounts/me", PowerAction.STOP)


# Timeweb


@pytest.mark.parametrize(
    ("action", "path"),
    [
        (PowerAction.START, "start"),
        (PowerAction.STOP, "shutdown"),
        (PowerAction.RESTART, "reboot"),
    ],
)
async def test_timeweb_power(action: PowerAction, path: str) -> None:
    plugin = TimewebPlugin(PluginContext({"api_token": "tw-token"}, httpx.AsyncClient(), ignore))
    with respx.mock:
        route = respx.post(f"https://api.timeweb.cloud/api/v1/servers/100001/{path}").respond(204)
        result = await plugin.power("100001", action)
    assert result.ok
    assert route.calls.last.request.headers["Authorization"] == "Bearer tw-token"


async def test_timeweb_power_errors() -> None:
    plugin = TimewebPlugin(PluginContext({"api_token": "tw-token"}, httpx.AsyncClient(), ignore))
    with pytest.raises(HosterError):
        await plugin.power("dedicated-5", PowerAction.STOP)
    with respx.mock:
        respx.post("https://api.timeweb.cloud/api/v1/servers/100001/shutdown").respond(
            409, json={"error_code": "conflict", "message": "busy"}
        )
        with pytest.raises(HosterError, match="conflict"):
            await plugin.power("100001", PowerAction.STOP)
        respx.post("https://api.timeweb.cloud/api/v1/servers/100001/reboot").respond(401)
        with pytest.raises(AuthExpired):
            await plugin.power("100001", PowerAction.RESTART)


# Regxa

SESSION = f"{RGX}/api/auth/session"


def regxa() -> RegxaPlugin:
    secrets = {"rgx.session-token": "rgx-session-token-for-tests"}
    return RegxaPlugin(PluginContext(secrets, httpx.AsyncClient(), ignore))


@pytest.fixture
def regxa_site() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        replay(router, load("regxa"))
        router.get(SESSION).respond(json={"user": {"client_id": "100002"}})
        yield router


@pytest.mark.parametrize(
    ("action", "word"),
    [(PowerAction.START, "start"), (PowerAction.STOP, "stop"), (PowerAction.RESTART, "restart")],
)
async def test_regxa_power_goes_to_the_vps(
    regxa_site: respx.MockRouter, action: PowerAction, word: str
) -> None:
    route = regxa_site.post(f"{RGX}/api/services/vps/power/{word}/100006").respond(
        200, json={"done_msg": "VPS action done"}
    )
    result = await regxa().power("100003", action)
    assert (result.ok, result.detail) == (True, "VPS action done")
    request = route.calls.last.request
    assert request.content == b""
    assert "rgx.session-token=rgx-session-token-for-tests" in request.headers["Cookie"]


async def test_regxa_power_error(regxa_site: respx.MockRouter) -> None:
    regxa_site.post(f"{RGX}/api/services/vps/power/stop/100006").respond(
        500, json={"error": "VPS is locked"}
    )
    with pytest.raises(HosterError, match="VPS is locked"):
        await regxa().power("100003", PowerAction.STOP)


def _invoice(status: str, balance: str, line: str = "Hosting") -> dict[str, Any]:
    return {
        "result": "success",
        "status": status,
        "total": balance,
        "balance": balance,
        "items": {"item": [{"type": line, "relid": 100003, "description": "VPS renewal"}]},
    }


async def test_regxa_pays_a_renewal_invoice(regxa_site: respx.MockRouter) -> None:
    invoice = regxa_site.get(f"{RGX}/api/billing/invoices/100099").mock(
        side_effect=[
            httpx.Response(200, json=_invoice("Unpaid", "12.50")),
            httpx.Response(200, json=_invoice("Paid", "0.00")),
        ]
    )
    apply = regxa_site.post(f"{RGX}/api/billing/invoices/apply-credit").respond(
        200, json={"success": True, "message": "ok"}
    )

    result = await regxa().pay_invoice("100099", Money.of("12.50", "USD"))

    assert result.ok and "Paid" in result.detail
    assert body(apply.calls.last.request) == {"invoiceid": 100099, "amount": 12.5}
    assert invoice.call_count == 2


@pytest.mark.parametrize(
    ("answer", "amount", "reason"),
    [
        (_invoice("Paid", "0.00"), "12.50", "not unpaid"),
        (_invoice("Unpaid", "13.00"), "12.50", "now"),
        (_invoice("Unpaid", "12.50", line=""), "12.50", "not a service renewal"),
        (_invoice("Unpaid", "5000.00"), "5000.00", "less than"),
    ],
)
async def test_regxa_refuses_to_pay(
    regxa_site: respx.MockRouter, answer: dict[str, Any], amount: str, reason: str
) -> None:
    regxa_site.get(f"{RGX}/api/billing/invoices/100099").respond(200, json=answer)
    apply = regxa_site.post(f"{RGX}/api/billing/invoices/apply-credit").respond(json={})
    with pytest.raises(HosterError, match=reason):
        await regxa().pay_invoice("100099", Money.of(amount, "USD"))
    assert not apply.called


async def test_regxa_apply_credit_refused(regxa_site: respx.MockRouter) -> None:
    regxa_site.get(f"{RGX}/api/billing/invoices/100099").respond(
        200, json=_invoice("Unpaid", "12.50")
    )
    regxa_site.post(f"{RGX}/api/billing/invoices/apply-credit").respond(
        200, json={"success": False, "message": "Amount exceeds invoice balance"}
    )
    with pytest.raises(HosterError, match="exceeds invoice balance"):
        await regxa().pay_invoice("100099", Money.of("12.50", "USD"))


async def test_regxa_unpaid_invoice_amount_is_what_is_left(regxa_site: respx.MockRouter) -> None:
    invoices = await regxa().invoices()
    unpaid = [i for i in invoices if i.status.value == "unpaid"]
    assert unpaid and all(i.amount.amount > 0 for i in unpaid)


# AdminVPS

PAGE = load("adminvps")[1]["text"]
SERVICE = "100001"
SUCCESS = (
    '<html><a href="logout.php">x</a><div class="alert alert-success text-center"'
    ' id="alertModuleCustomButtonSuccess">Действие выполнено успешно</div></html>'
)
FAILED = (
    '<html><a href="logout.php">x</a><div class="alert alert-danger text-center"'
    ' id="alertModuleCustomButtonFailed">Ошибка: Server is locked</div></html>'
)


def adminvps() -> AdminVpsPlugin:
    secrets = {"WHMCSaBcDeFgHiJkL": "whmcs-session-for-tests"}
    return AdminVpsPlugin(PluginContext(secrets, httpx.AsyncClient(), ignore))


def modal(target: str) -> str:
    return (FIXTURES / "control" / f"adminvps-modal-{target}.json").read_text(encoding="utf-8")


class Cabinet:
    """The service page, its modals and the answers to posts, by request."""

    def __init__(self, page: str = PAGE) -> None:
        self.page = page
        self.posts: list[httpx.Request] = []
        self.answers: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.method == "GET":
            if params.get("ajax") == "1":
                return httpx.Response(200, text=modal(params["loadData"]))
            if params.get("serveraction") == "custom":
                return httpx.Response(200, text=self.answers["after_power"].text)
            return httpx.Response(200, text=self.page)
        self.posts.append(request)
        key = params.get("mgformtype", "power")
        return self.answers[key]


@pytest.fixture
def cabinet() -> Iterator[Cabinet]:
    handler = Cabinet()
    with respx.mock(assert_all_called=False) as router:
        router.route(url__startswith=CLIENTAREA).mock(side_effect=handler)
        yield handler


def mg(status: str = "success", message: str = "", **data: Any) -> httpx.Response:
    return httpx.Response(200, json={"data": {"status": status, "message": message, **data}})


def test_adminvps_page_offers_every_action() -> None:
    page = pages.parse(PAGE)
    assert control.actions(page) == frozenset(ServerAction)
    form = control.power_form(page, ServerAction.STOP)
    assert form is not None
    assert form.fields == {"token": "***", "id": SERVICE, "modop": "custom", "a": "shutdown"}
    assert set(control.renewal_modals(page)) >= {control.TURN_ON, control.TURN_OFF, control.RENEW}


@pytest.mark.parametrize(
    ("action", "command"),
    [(PowerAction.START, "boot"), (PowerAction.STOP, "shutdown"), (PowerAction.RESTART, "reboot")],
)
async def test_adminvps_power_posts_the_page_form(
    cabinet: Cabinet, action: PowerAction, command: str
) -> None:
    location = f"clientarea.php?action=productdetails&id={SERVICE}&serveraction=custom&a={command}"
    cabinet.answers["power"] = httpx.Response(302, headers={"Location": location})
    cabinet.answers["after_power"] = httpx.Response(200, text=SUCCESS)

    result = await adminvps().power(SERVICE, action)

    assert result.ok and "успешно" in result.detail
    request = cabinet.posts[-1]
    assert str(request.url) == f"{CLIENTAREA}?action=productdetails&id={SERVICE}"
    sent = dict(httpx.QueryParams(request.content.decode()))
    assert sent == {"token": "***", "id": SERVICE, "modop": "custom", "a": command}


async def test_adminvps_power_failure_and_no_answer(cabinet: Cabinet) -> None:
    cabinet.answers["power"] = httpx.Response(200, text=FAILED)
    with pytest.raises(HosterError, match="Server is locked"):
        await adminvps().power(SERVICE, PowerAction.STOP)

    cabinet.answers["power"] = httpx.Response(200, text='<a href="logout.php">x</a>')
    result = await adminvps().power(SERVICE, PowerAction.STOP)
    assert not result.ok


@pytest.mark.parametrize(
    ("enabled", "form", "formtype"),
    [(True, "turnOnRenewalsForms", "turnOn"), (False, "turnOffRenewalsForms", "turnOff")],
)
async def test_adminvps_autorenew_submits_the_modal_form(
    cabinet: Cabinet, enabled: bool, form: str, formtype: str
) -> None:
    cabinet.answers[formtype] = mg(message="Autorenewal changed")

    result = await adminvps().set_autorenew(SERVICE, enabled)

    assert (result.ok, result.detail) == (True, "Autorenewal changed")
    request = cabinet.posts[-1]
    params = request.url.params
    assert params["loadData"] == form and params["index"] == form
    assert params["namespace"].endswith(f"_Forms_{form[0].upper()}{form[1:]}")
    assert (params["ajax"], params["mgformtype"], params["id"]) == ("1", formtype, SERVICE)
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    content = request.content.decode()
    assert 'name="hostingID"\r\n\r\n100001' in content
    assert 'name="token"\r\n\r\n***' in content


async def test_adminvps_autorenew_refused(cabinet: Cabinet) -> None:
    cabinet.answers["turnOff"] = mg("error", "Not allowed")
    with pytest.raises(HosterError, match="Not allowed"):
        await adminvps().set_autorenew(SERVICE, False)


def price_answer(total: str) -> httpx.Response:
    """`checkPrice` as the renewal form's script reads it (`data.htmlData`)."""
    return mg(
        htmlData={
            "newNextDueDate": "27.12.2026",
            "fullTotal": {"subtotal": total, "total": total, "tax": 0, "discount": 0},
            "displayFullTotal": {"subtotal": f"{total} руб.", "total": f"{total} руб."},
        }
    )


async def test_adminvps_renewal_price_then_renewal(cabinet: Cabinet) -> None:
    cabinet.answers["checkPrice"] = price_answer("390.00")
    cabinet.answers["renew"] = mg(message="Invoice created", redirectUrl="viewinvoice.php?id=777")
    plugin = adminvps()

    price = await plugin.renew_price(SERVICE)
    assert price == Money(amount=Decimal("390.00"), currency="RUB")
    assert [p.url.params["mgformtype"] for p in cabinet.posts] == ["checkPrice"]

    result = await plugin.renew(SERVICE, price)

    assert result.ok and "invoice 777" in result.detail
    renew = cabinet.posts[-1]
    assert renew.url.params["loadData"] == "renewServiceForm"
    content = renew.content.decode()
    for name, value in (("hostingID", SERVICE), ("months", "1"), ("cycle", "monthly")):
        assert f'name="{name}"\r\n\r\n{value}' in content


async def test_adminvps_renewal_at_another_price_is_not_sent(cabinet: Cabinet) -> None:
    cabinet.answers["checkPrice"] = price_answer("420.00")
    with pytest.raises(HosterError, match="costs"):
        await adminvps().renew(SERVICE, Money.of("390.00", "RUB"))
    assert [p.url.params["mgformtype"] for p in cabinet.posts] == ["checkPrice"]


async def test_adminvps_price_answer_without_a_total_is_layout_change(cabinet: Cabinet) -> None:
    cabinet.answers["checkPrice"] = mg(htmlData={"newNextDueDate": "x"})
    with pytest.raises(LayoutChanged):
        await adminvps().renew_price(SERVICE)


async def test_adminvps_modal_of_another_service_is_refused(cabinet: Cabinet) -> None:
    cabinet.page = PAGE.replace(f"id={SERVICE}", "id=100002")
    with pytest.raises(LayoutChanged):
        await adminvps().set_autorenew("100002", True)


def test_adminvps_recorded_modals_parse() -> None:
    for target, formtype in (
        ("turnOnRenewals", "turnOn"),
        ("turnOffRenewals", "turnOff"),
        ("renewService", "renew"),
    ):
        form = control.modal_form(control.mg_answer(modal(target)))
        assert form.formtype == formtype
        assert form.fields["hostingID"] == SERVICE
    renew = control.modal_form(control.mg_answer(modal("renewService")))
    assert renew.fields == {
        "hostingID": SERVICE,
        "months": "1",
        "renewPrice": "",
        "cycle": "monthly",
        "promoCode": "",
    }


def test_adminvps_server_actions_only_for_active_services() -> None:
    page = pages.parse(PAGE)
    assert pages.details(page, PAGE).actions == frozenset(ServerAction)
