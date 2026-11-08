import copy
import re
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import Autorenew, BillingModel, ServerAction, ServerKind, ServerStatus
from hostwatch.core.plugin import Capability, PluginContext, Secrets, capabilities
from hostwatch.core.record import MASK, Scrubber
from hostwatch.plugins.adminvps import CLIENT_DATA, CLIENTAREA, AdminVpsPlugin, pages
from hostwatch.plugins.adminvps.scrub import scrub_page
from tests.contract import check_contract, check_error_mapped
from tests.replay import load, replay

SESSION = "WHMCSaBcDeFgHiJkL"
SECRETS = {SESSION: "whmcs-session-for-tests", "__ddg1_": "ddg-cookie-for-tests"}
LOGGED_IN = '<html><body><a href="/logout.php">Выход</a></body></html>'


async def ignore(_: Secrets) -> None:
    pass


def adminvps(secrets: Secrets = SECRETS, save: Any = ignore) -> AdminVpsPlugin:
    return AdminVpsPlugin(PluginContext(secrets, httpx.AsyncClient(), save))


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return load("adminvps")


@pytest.fixture
def hoster(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with serving(records) as router:
        yield router


@contextmanager
def serving(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        replay(router, records)
        yield router


def services_page(records: list[dict[str, Any]]) -> dict[str, Any]:
    return next(r for r in records if r["query"].get("action") == "services")


def with_page(records: list[dict[str, Any]], index: int, text: str) -> list[dict[str, Any]]:
    records = copy.deepcopy(records)
    records[index]["text"] = text
    return records


def html_only(page: str) -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.get(CLIENTAREA).mock(return_value=httpx.Response(200, text=page))
    return router


async def test_adminvps_passes_contract(hoster: respx.MockRouter) -> None:
    await check_contract(adminvps())


def test_capabilities() -> None:
    assert capabilities(AdminVpsPlugin) == {
        Capability.USAGE,
        Capability.TOPUP,
        Capability.POWER,
        Capability.AUTORENEW,
        Capability.RENEW,
    }
    assert not AdminVpsPlugin.login_only


async def test_servers_from_recorded_cabinet(hoster: respx.MockRouter) -> None:
    servers = await adminvps().servers()

    # Ten services in the list, four of them deleted or cancelled.
    assert [s.external_id for s in servers] == [str(i) for i in range(100001, 100007)]
    first = servers[0]
    assert first.name == "hostname-1"
    assert first.plan == "Promo (Испания)"
    assert first.kind is ServerKind.VPS
    assert first.ip == "192.0.2.10"
    assert first.status is ServerStatus.RUNNING
    assert first.raw_status == "active/online"
    assert first.price is not None and first.price.amount == Decimal("1250.00")
    assert first.price.currency == "RUB"
    assert first.period_months == 1
    assert first.billing is BillingModel.PERIODIC
    assert first.next_charge_at is not None
    assert first.next_charge_at.isoformat() == "2026-11-27T00:00:00+03:00"
    # The page has the power forms and the HostingRenewals buttons.
    assert first.actions == frozenset(ServerAction)
    assert [s.autorenew for s in servers] == [Autorenew.OFF] * 5 + [Autorenew.ON]


async def test_one_poll_reads_each_page_once(hoster: respx.MockRouter) -> None:
    plugin = adminvps()
    servers = await plugin.servers()
    calls = hoster.calls.call_count
    usage = await plugin.usage(servers[0].external_id)
    balance = await plugin.balance()

    assert hoster.calls.call_count == calls
    assert usage.traffic_gb == pytest.approx(418 / 1024)
    assert usage.cpu_percent is None and usage.ram_percent is None
    assert balance.money.amount == Decimal("1125.00")
    assert balance.money.currency == "RUB"


async def test_usage_on_its_own(records: list[dict[str, Any]]) -> None:
    details, data = records[-2], records[-1]
    with respx.mock(assert_all_called=True) as router:
        router.get(CLIENTAREA, params={"action": "productdetails"}).mock(
            return_value=httpx.Response(200, text=details["text"])
        )
        router.get(CLIENT_DATA).mock(return_value=httpx.Response(200, json=data["json"]))
        usage = await adminvps().usage(details["query"]["id"])

    assert usage.server_id == details["query"]["id"]
    assert usage.traffic_gb == pytest.approx(418 / 1024)


async def test_balance_on_its_own(records: list[dict[str, Any]]) -> None:
    with html_only(services_page(records)["text"]):
        balance = await adminvps().balance()
    assert balance.money.amount == Decimal("1125.00")


async def test_check_login(records: list[dict[str, Any]]) -> None:
    with html_only(services_page(records)["text"]):
        await adminvps().check_login()


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(302, headers={"Location": "/login"}), AuthExpired),
        (httpx.Response(200, text="<html><form action='dologin.php'></form></html>"), AuthExpired),
        (httpx.Response(403, text="<html>DDoS-Guard</html>"), Blocked),
        (httpx.Response(200, text="<html><title>DDoS-Guard</title></html>"), Blocked),
        (httpx.Response(429), RateLimited),
        (httpx.Response(502), HosterError),
        (httpx.Response(200, text=LOGGED_IN), LayoutChanged),
    ],
)
async def test_errors_are_mapped(response: httpx.Response, error: type[Exception]) -> None:
    with respx.mock() as router:
        router.get(CLIENTAREA).mock(return_value=response)
        await check_error_mapped(adminvps().servers(), error)


async def test_timeout_is_mapped() -> None:
    with respx.mock() as router:
        router.get(CLIENTAREA).mock(side_effect=httpx.ConnectTimeout("slow"))
        await check_error_mapped(adminvps().servers(), TimeoutError)


async def test_balance_needs_the_header() -> None:
    with html_only(LOGGED_IN):
        await check_error_mapped(adminvps().balance(), LayoutChanged)


async def test_changed_table_is_layout_change(records: list[dict[str, Any]]) -> None:
    page = services_page(records)["text"].replace("status-active", "state-active")
    with serving(with_page(records, records.index(services_page(records)), page)):
        await check_error_mapped(adminvps().servers(), LayoutChanged)


async def test_both_autorenew_buttons_shown_is_layout_change(
    records: list[dict[str, Any]],
) -> None:
    page = records[1]["text"].replace('hidden="hidden" ', "")
    with serving(with_page(records, 1, page)):
        await check_error_mapped(adminvps().servers(), LayoutChanged)


async def test_service_page_without_server_id_is_layout_change(
    records: list[dict[str, Any]],
) -> None:
    page = records[1]["text"].replace("var vserverid", "var serverid")
    with serving(with_page(records, 1, page)):
        await check_error_mapped(adminvps().servers(), LayoutChanged)


async def test_hung_solusvm_leaves_one_server_unknown(records: list[dict[str, Any]]) -> None:
    answers = [r for r in records if r["url"] == CLIENT_DATA]
    with serving([r for r in records if r not in answers]) as router:
        router.get(CLIENT_DATA).mock(
            side_effect=[
                httpx.ReadTimeout("slow"),
                *(httpx.Response(200, json=r["json"]) for r in answers[1:]),
            ]
        )
        servers = await adminvps().servers()

    assert servers[0].status is ServerStatus.UNKNOWN
    assert servers[0].raw_status == "active/?"
    assert servers[0].ip == "192.0.2.10"  # from the service page
    assert all(s.status is ServerStatus.RUNNING for s in servers[1:])


async def test_session_cookie_renewed_by_the_site_is_saved(records: list[dict[str, Any]]) -> None:
    saved: list[Secrets] = []

    async def save(new: Secrets) -> None:
        saved.append(new)

    page = services_page(records)["text"]
    with respx.mock() as router:
        route = router.get(CLIENTAREA)
        route.side_effect = [
            httpx.Response(
                200,
                text=page,
                headers=[("Set-Cookie", "__ddg8_=fresh8; Path=/"), ("Set-Cookie", "x=1")],
            ),
            httpx.Response(200, text=page, headers={"Set-Cookie": f"{SESSION}=renewed; Path=/"}),
            httpx.Response(200, text=page),
        ]
        plugin = adminvps(save=save)
        await plugin.check_login()
        await plugin.check_login()
        await plugin.check_login()

    # DDoS-Guard's short-lived cookie is used at once but not written to the database.
    assert "__ddg8_=fresh8" in route.calls[1].request.headers["Cookie"]
    assert "x=1" not in route.calls[1].request.headers["Cookie"]
    assert saved == [{**SECRETS, SESSION: "renewed"}]
    assert f"{SESSION}=renewed" in route.calls[2].request.headers["Cookie"]


@pytest.mark.parametrize(
    ("text", "gb"),
    [("417.7 MB", 417.7 / 1024), ("5 TB", 5120), ("2 GB", 2), ("0 B", 0), ("", None)],
)
def test_traffic(text: str, gb: float | None) -> None:
    assert pages.traffic_gb(text) == (pytest.approx(gb) if gb is not None else None)


def test_unknown_traffic_unit() -> None:
    with pytest.raises(LayoutChanged):
        pages.traffic_gb("12 parsecs")


def test_scrub_page() -> None:
    scrubber = Scrubber(["whmcs-session-for-tests"])
    page = (
        "<!DOCTYPE html><html><head><style>.a{}</style>"
        "<script>var csrfToken='0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c';</script></head><body>"
        '<div class="message">Ваш баланс составляет 4321.09 руб.</div>'
        "<tr onclick=\"go('clientarea.php?action=productdetails&amp;id=731904')\">"
        '<td><strong>VPS</strong><br/><a href="http://my-node.example" target="_blank">'
        "my-node.example</a></td>"
        '<td data-order="499.00">499.00 руб.<br/>ежемесячно</td></tr>'
        '<input type="hidden" name="token" value="0f1e2d3c" />'
        '<div id="mc_jkykvk4af05bbnta3lptt49i7r7rpjp0"></div>'
        "<p>Сервер my-node.example, 198.51.100.77</p>"
        "<script>var vserverid = 642511;</script></body></html>"
    )

    text = scrubber.text(scrub_page(page, scrubber))

    for real in ("0f1e2d3c", "4321.09", "499.00", "731904", "642511", "my-node", "198.51."):
        assert real not in text
    assert "<style>" not in text and "csrfToken" not in text
    assert f'name="token" value="{MASK}"' in text
    assert "hostname-1" in text and "id=100001" in text and "vserverid = 100002" in text
    assert 'data-order="1125.00">1125.00 руб.' in text  # one price, one fake


def test_fixtures_hold_no_form_tokens(records: list[dict[str, Any]]) -> None:
    for record in records:
        for value in re.findall(r'name="token"[^>]*value="([^"]*)"', record.get("text", "")):
            assert value == MASK
