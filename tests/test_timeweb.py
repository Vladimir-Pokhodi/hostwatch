"""Timeweb Cloud plugin on answers recorded by `hostwatch record timeweb`.

The recorded finances carry a made-up balance instead of the owner's real one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

import httpx
import pytest
import respx

from hostwatch.core.errors import (
    AuthExpired,
    Blocked,
    HosterError,
    LayoutChanged,
    PluginError,
    RateLimited,
)
from hostwatch.core.forecast import Method, forecast
from hostwatch.core.models import (
    Autorenew,
    BillingModel,
    Money,
    ServerKind,
    ServerStatus,
)
from hostwatch.core.plugin import BrowserAuth, Capability, PluginContext, Secrets, capabilities
from hostwatch.core.record import Scrubber
from hostwatch.plugins.timeweb import TimewebPlugin
from tests.contract import check_contract, check_error_mapped

FIXTURES = Path(__file__).parent / "fixtures" / "timeweb"
API = "https://api.timeweb.cloud/api/v1/"
STATISTICS = re.compile(r"/api/v1/servers/(\d+)/statistics/[^/]+/\d+/system\.cpu\.util$")


def recorded(name: str) -> dict[str, Any]:
    (path,) = [p for p in sorted(FIXTURES.glob("*.json")) if name in p.name][:1]
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return record


def mock_recorded(router: respx.MockRouter) -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        url = httpx.URL(record["url"])
        if match := STATISTICS.search(url.path):
            server_id = match.group(1)
            # The recorder turned the period of one hour into a fake id; the plugin asks for 1.
            route = router.get(
                host=url.host,
                path__regex=rf"/api/v1/servers/{server_id}/statistics/[^/]+/1/system\.cpu\.util$",
            )
        else:
            route = router.get(host=url.host, path=url.path)
        route.respond(record["status"], json=record["json"])


async def ignore(_: Secrets) -> None:
    pass


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def plugin() -> Iterator[TimewebPlugin]:
    yield TimewebPlugin(PluginContext({"api_token": "test-token"}, httpx.AsyncClient(), ignore))


async def test_passes_contract(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    mock_recorded(api)
    await check_contract(plugin)


def test_meta_has_token_and_panel_login() -> None:
    auth = TimewebPlugin.meta.auth
    assert [type(a).__name__ for a in auth] == ["ApiKeyAuth", "BrowserAuth"]
    browser = auth[1]
    assert isinstance(browser, BrowserAuth)
    assert "refresh_token" in browser.cookies
    assert capabilities(TimewebPlugin) == {Capability.USAGE}


async def test_servers_from_recording(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    mock_recorded(api)
    servers = await plugin.servers()

    assert [s.external_id for s in servers] == ["100001", "100003"]
    preset, configured = servers
    assert preset.kind is ServerKind.VPS
    assert preset.status is ServerStatus.RUNNING and preset.raw_status == "on"
    assert preset.ip == "192.0.2.10"
    assert preset.plan == "Cloud MSK Ultra 15"
    # 2010 ₽ a month with backups and the IP address, over 730 hours.
    assert preset.price == Money.of("2.7534", "RUB")
    assert preset.billing is BillingModel.HOURLY
    assert preset.autorenew is Autorenew.NOT_AVAILABLE
    assert preset.period_months is None and preset.next_charge_at is None
    assert preset.actions == frozenset()
    # Main IPv4 even when an IPv6 address comes first.
    assert configured.ip == "192.0.2.11"
    assert configured.plan == "2 CPU, 2 GB RAM, 15 GB"
    assert configured.price == Money.of("4.4521", "RUB")
    assert api.calls.last.request.headers["Authorization"] == "Bearer test-token"


async def test_balance_uses_hoster_hours(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    mock_recorded(api)
    balance = await plugin.balance()
    assert balance.money == Money.of("1234.56", "RUB")
    assert balance.money.amount == Decimal("1234.56")
    assert balance.hours_left == 171

    now = datetime(2026, 11, 7, tzinfo=UTC)
    result = forecast(balance, await plugin.servers(), now)
    assert result.method is Method.HOSTER_HOURS
    assert result.runs_out_at == datetime(2026, 11, 14, 3, tzinfo=UTC)


async def test_usage_averages_last_quarter_hour(
    api: respx.MockRouter, plugin: TimewebPlugin
) -> None:
    mock_recorded(api)
    usage = await plugin.usage("100001")

    points = recorded("statistics")["json"]["statistics"][0]["list"]
    assert usage.cpu_percent == pytest.approx(fmean(p["value"] for p in points[-15:]))
    assert usage.measured_at == datetime.fromisoformat(points[-1]["time"])
    assert usage.disk_percent == pytest.approx(3910 / 15360 * 100)
    assert usage.ram_percent is None


async def test_usage_of_dedicated_server_is_empty(plugin: TimewebPlugin) -> None:
    usage = await plugin.usage("dedicated-7")
    assert usage.cpu_percent is None and usage.disk_percent is None


def finances() -> dict[str, Any]:
    return recorded("account_finances")["json"]


def cloud_server(**changes: Any) -> dict[str, Any]:
    server = dict(recorded("api_v1_servers.json")["json"]["servers"][0])
    server.update(changes)
    return server


def mock_lists(
    api: respx.MockRouter,
    servers: list[dict[str, Any]],
    dedicated: list[dict[str, Any]] | None = None,
    costs: list[dict[str, Any]] | None = None,
) -> None:
    api.get(API + "account/finances").respond(json=finances())
    api.get(API + "account/services/cost").respond(
        json={"services_costs": costs or [], "meta": {"total": len(costs or [])}}
    )
    api.get(host="api.timeweb.cloud", path="/api/v1/servers").respond(
        json={"servers": servers, "meta": {"total": len(servers)}}
    )
    api.get(API + "dedicated-servers").respond(
        json={"dedicated_servers": dedicated or [], "meta": {"total": len(dedicated or [])}}
    )


@pytest.mark.parametrize(
    ("raw", "blocked", "status"),
    [
        ("on", False, ServerStatus.RUNNING),
        ("off", False, ServerStatus.STOPPED),
        ("no_paid", False, ServerStatus.UNPAID),
        ("blocked", False, ServerStatus.SUSPENDED),
        ("on", True, ServerStatus.SUSPENDED),
        ("rebooting", False, ServerStatus.UNKNOWN),
        ("something_new", False, ServerStatus.UNKNOWN),
    ],
)
async def test_cloud_status(
    api: respx.MockRouter, plugin: TimewebPlugin, raw: str, blocked: bool, status: ServerStatus
) -> None:
    mock_lists(api, [cloud_server(status=raw, is_blocked=blocked)])
    (server,) = await plugin.servers()
    assert server.status is status
    assert server.raw_status == raw
    assert server.price is None  # no line in services/cost


async def test_dedicated_server(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    dedicated = {
        "id": 7,
        "name": "big",
        "status": "installing",
        "ip": "192.0.2.50",
        "price": 7300,
        "cpu_description": "Xeon E-2236",
        "password": None,
    }
    mock_lists(api, [], [dedicated])
    (server,) = await plugin.servers()
    assert server.external_id == "dedicated-7"
    assert server.kind is ServerKind.DEDICATED
    assert server.status is ServerStatus.UNKNOWN and server.raw_status == "installing"
    assert server.price == Money.of("10", "RUB")
    assert server.billing is BillingModel.HOURLY


async def test_dedicated_price_prefers_services_cost(
    api: respx.MockRouter, plugin: TimewebPlugin
) -> None:
    dedicated = {
        "id": 7,
        "name": "big",
        "status": "on",
        "ip": None,
        "price": 7300,
        "cpu_description": "",
    }
    cost = {"type": "dedicated_server", "service_id": 7, "cost": 7300, "total_cost": 14600}
    mock_lists(api, [], [dedicated], [cost])
    (server,) = await plugin.servers()
    assert server.status is ServerStatus.RUNNING
    assert server.price == Money.of("20", "RUB")
    assert server.ip is None and server.plan is None


async def test_servers_are_paged(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    first = [cloud_server(id=n) for n in range(1, 101)]
    api.get(API + "servers", params={"offset": "0"}).respond(
        json={"servers": first, "meta": {"total": 101}}
    )
    api.get(API + "servers", params={"offset": "100"}).respond(
        json={"servers": [cloud_server(id=101)], "meta": {"total": 101}}
    )
    mock_lists(api, [])
    servers = await plugin.servers()
    assert len(servers) == 101


async def test_balance_without_hours_left(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    answer = finances()
    answer["finances"] = {**answer["finances"], "hours_left": None}
    api.get(API + "account/finances").respond(json=answer)
    assert (await plugin.balance()).hours_left is None


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401, json={"error_code": "unauthorized"}), AuthExpired),
        (httpx.Response(403, text="<html>QRATOR</html>"), Blocked),
        (httpx.Response(429, json={"error_code": "too_many_requests"}), RateLimited),
        (httpx.Response(500, text="oops"), HosterError),
        (httpx.Response(200, text="<html>not json</html>"), LayoutChanged),
        (httpx.Response(200, json={"finances": {"balance": 1}}), LayoutChanged),
    ],
)
async def test_errors_are_core_types(
    api: respx.MockRouter, plugin: TimewebPlugin, response: httpx.Response, error: type[PluginError]
) -> None:
    api.get(API + "account/finances").mock(return_value=response)
    await check_error_mapped(plugin.balance(), error)


async def test_hoster_error_shows_code_not_message(
    api: respx.MockRouter, plugin: TimewebPlugin
) -> None:
    api.get(API + "account/finances").respond(
        400, json={"error_code": "bad_request", "message": "echo of test-token"}
    )
    with pytest.raises(HosterError) as caught:
        await plugin.balance()
    assert caught.value.reason == "bad_request"


async def test_no_token_means_login_again() -> None:
    plugin = TimewebPlugin(PluginContext({}, httpx.AsyncClient(), ignore))
    await check_error_mapped(plugin.servers(), AuthExpired)


async def test_timeout_is_core_timeout(api: respx.MockRouter, plugin: TimewebPlugin) -> None:
    api.get(API + "account/finances").mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(TimeoutError):
        await plugin.balance()


def test_recorder_scrubs_what_timeweb_sends() -> None:
    scrubber = Scrubber([], TimewebPlugin.record_names)
    server = {
        "name": "my-vpn",
        "root_pass": "hunter2",
        "vnc_pass": "",
        "is_allow_root_ssh_pass": True,
        "networks": [{"ips": [{"ip": "2a03:6f00:a::2:aaf4", "id": "591f9f41-5627"}]}],
        "start_at": "2026-10-28T19:54:15.000Z",
    }
    clean = scrubber.value({"servers": [server]}, ())["servers"][0]
    assert clean["name"] != "my-vpn"
    assert clean["root_pass"] == clean["vnc_pass"] == "***"
    assert clean["is_allow_root_ssh_pass"] is True
    ip = clean["networks"][0]["ips"][0]
    assert ip["ip"].startswith("2001:db8::")
    assert ip["id"] != "591f9f41-5627"
    assert clean["start_at"] == "2026-10-28T19:54:15.000Z"
    assert scrubber.text("at 12:30:00 from ::1") == "at 12:30:00 from 2001:db8::b"
