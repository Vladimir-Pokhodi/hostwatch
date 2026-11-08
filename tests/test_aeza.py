import copy
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hostwatch.__main__ import main
from hostwatch.core.errors import (
    AuthExpired,
    Blocked,
    HosterError,
    LayoutChanged,
    PluginError,
    RateLimited,
)
from hostwatch.core.models import (
    Autorenew,
    BillingModel,
    InvoiceStatus,
    Money,
    ServerAction,
    ServerKind,
    ServerStatus,
)
from hostwatch.core.plugin import Capability, PluginContext, Secrets, capabilities
from hostwatch.plugins.aeza import BASE_URL, AezaPlugin
from tests.contract import check_contract, check_error_mapped
from tests.replay import load, replay

KEY = "aeza-key-for-tests"
SERVER = "100006"


async def ignore(_: Secrets) -> None:
    pass


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return load("aeza")


@pytest.fixture
def hoster(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with serving(records) as router:
        yield router


def aeza(plugin_cls: type[AezaPlugin] = AezaPlugin) -> AezaPlugin:
    return plugin_cls(PluginContext({"api_key": KEY}, httpx.AsyncClient(), ignore))


def with_first_vps(records: list[dict[str, Any]], **changes: Any) -> list[dict[str, Any]]:
    """The recordings with the first VPS in the services list changed."""
    records = copy.deepcopy(records)
    answer = next(r for r in records if r["url"].endswith("/services"))["json"]
    next(item for item in answer["items"] if item["typeSlug"] == "vps").update(changes)
    return records


@contextmanager
def serving(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        replay(router, records)
        yield router


async def test_aeza_passes_contract(hoster: respx.MockRouter) -> None:
    await check_contract(aeza())


def test_capabilities() -> None:
    assert capabilities(AezaPlugin) == {
        Capability.USAGE,
        Capability.INVOICES,
        Capability.TOPUP,
        Capability.POWER,
        Capability.AUTORENEW,
        Capability.RENEW,
    }


async def test_servers_skip_domains_and_map_fields(hoster: respx.MockRouter) -> None:
    servers = await aeza().servers()

    assert [s.external_id for s in servers] == [SERVER, "100009", "100010", "100013"]
    first = servers[0]
    assert first.name == "name-2"
    assert first.kind is ServerKind.VPS
    assert first.ip == "192.0.2.10"
    assert first.plan == "CLTs-1"
    assert first.status is ServerStatus.RUNNING
    assert first.raw_status == "active"
    assert first.price == Money.of("5.93", "EUR")
    assert first.period_months == 1
    assert first.next_charge_at == datetime(2026, 12, 6, 23, 49, 2, tzinfo=UTC)
    assert first.autorenew is Autorenew.ON
    assert first.billing is BillingModel.PERIODIC
    # `ctl` and `ctl.restart` in the service capabilities, and it is paid monthly.
    assert first.actions == frozenset(ServerAction)
    quarterly = servers[2]
    assert quarterly.price == Money.of("33.83", "EUR")
    assert quarterly.period_months == 3


async def test_requests_carry_the_key(hoster: respx.MockRouter) -> None:
    await aeza().servers()

    request = hoster.calls.last.request
    assert request.headers["X-API-Key"] == KEY
    assert str(request.url).startswith(BASE_URL)


async def test_balance(hoster: respx.MockRouter) -> None:
    balance = await aeza().balance()

    assert balance.money == Money.of("10.00", "EUR")
    assert balance.hours_left is None


async def test_usage_averages_the_last_window(hoster: respx.MockRouter) -> None:
    usage = await aeza().usage(SERVER)

    assert usage.server_id == SERVER
    assert usage.cpu_percent == 2.44
    assert usage.ram_percent == 77.35
    assert usage.net_in_mbits == 0.67
    assert usage.net_out_mbits == 0.01
    assert usage.measured_at == datetime.fromtimestamp(1794026760, UTC)
    stats = [c.request for c in hoster.calls if "/stats/" in c.request.url.path]
    assert len(stats) == 4
    assert all(r.url.params["resolution"] == "60" for r in stats)


async def test_usage_without_points(records: list[dict[str, Any]]) -> None:
    empty = copy.deepcopy(records)
    for record in empty:
        if "/stats/" in record["url"]:
            record["json"] = {"data": [[1794026580, None]]}

    with serving(empty):
        usage = await aeza().usage(SERVER)

    assert usage.cpu_percent is None
    assert usage.net_in_mbits is None


async def test_invoices_are_charges_newest_first(hoster: respx.MockRouter) -> None:
    invoices = await aeza().invoices()

    assert len(invoices) == 36
    newest = invoices[0]
    assert newest.amount == Money.of("22.50", "EUR")
    assert newest.status is InvoiceStatus.PAID
    assert newest.description == "prolong"
    assert newest.server_id == SERVER
    assert newest.due_at == datetime(2026, 11, 6, 23, 47, 32, tzinfo=UTC)
    assert all(i.amount.amount > 0 for i in invoices)
    dates = [i.due_at for i in invoices]
    assert dates == sorted(dates, reverse=True)  # type: ignore[type-var]
    offsets = [
        c.request.url.params["offset"] for c in hoster.calls if "billing" in c.request.url.path
    ]
    assert offsets == ["0", "109"]


async def test_cancelled_charge_uses_creation_date() -> None:
    transaction = {
        "id": 7,
        "amount": -487.79999999999995,
        "bonusAmount": 0,
        "status": "cancelled",
        "performedAt": "1970-01-01T00:00:00.000Z",
        "createdAt": "2024-09-25T18:37:04.000Z",
        "type": "order",
        "serviceId": None,
    }
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").respond(json={"balance": 0, "currency": "EUR"})
        respx.get(BASE_URL + "billing/transactions").respond(
            json={"items": [transaction], "total": 1}
        )
        [invoice] = await aeza().invoices()

    assert invoice.status is InvoiceStatus.CANCELLED
    assert invoice.amount.amount == Decimal("4.87800")
    assert invoice.due_at == datetime(2024, 9, 25, 18, 37, 4, tzinfo=UTC)
    assert invoice.server_id is None


async def test_unknown_status_keeps_the_raw_value(records: list[dict[str, Any]]) -> None:
    with serving(with_first_vps(records, status="being_moved")):
        server = (await aeza().servers())[0]

    assert server.status is ServerStatus.UNKNOWN
    assert server.raw_status == "being_moved"


async def test_hourly_service(records: list[dict[str, Any]]) -> None:
    with serving(with_first_vps(records, paymentTerm="hour", price=4)):
        server = (await aeza().servers())[0]

    assert server.billing is BillingModel.HOURLY
    assert server.period_months is None
    assert server.price == Money.of("0.04", "EUR")


async def test_unknown_term_is_layout_change(records: list[dict[str, Any]]) -> None:
    with serving(with_first_vps(records, paymentTerm="fortnight")):
        await check_error_mapped(aeza().servers(), LayoutChanged)


async def test_actions_follow_service_capabilities(records: list[dict[str, Any]]) -> None:
    with serving(records):
        servers = await aeza().servers()
    assert servers[0].actions == frozenset(ServerAction)

    with serving(with_first_vps(records, capabilities=["charts"])):
        servers = await aeza().servers()
    assert servers[0].actions == {ServerAction.AUTORENEW, ServerAction.RENEW}

    with serving(with_first_vps(records, paymentTerm="hour")):
        servers = await aeza().servers()
    # An hourly service is paid every hour: nothing to renew.
    assert servers[0].actions == {ServerAction.START, ServerAction.STOP, ServerAction.RESTART}


async def test_plugin_without_control_shows_no_actions(records: list[dict[str, Any]]) -> None:
    class ReadOnly(AezaPlugin):
        power = None  # type: ignore[assignment]

    with serving(records):
        servers = await aeza(ReadOnly).servers()
    assert ServerAction.STOP not in servers[0].actions


async def test_short_rate_limit_is_waited_out() -> None:
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}, json={"statusCode": 429}),
                httpx.Response(200, json={"balance": 1234, "currency": "EUR"}),
            ]
        )
        balance = await aeza().balance()
    assert balance.money == Money.of("12.34", "EUR")


async def test_long_rate_limit_is_an_error() -> None:
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").respond(
            429, headers={"Retry-After": "3600"}, json={"statusCode": 429}
        )
        await check_error_mapped(aeza().balance(), RateLimited)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthExpired),
        (403, Blocked),
        (429, RateLimited),
        (500, HosterError),
        (404, HosterError),
    ],
)
async def test_http_errors_are_core_types(status: int, error: type[PluginError]) -> None:
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").respond(
            status, json={"statusCode": status, "message": "Not authenticated"}
        )
        await check_error_mapped(aeza().balance(), error)


async def test_hoster_error_keeps_the_message() -> None:
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").respond(400, json={"message": "Bad Request"})
        with pytest.raises(HosterError, match=r"HTTP 400: Bad Request"):
            await aeza().balance()


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b'{"balance": "lots"}'])
async def test_unexpected_answer_is_layout_change(body: bytes) -> None:
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").respond(200, content=body)
        await check_error_mapped(aeza().balance(), LayoutChanged)


async def test_network_errors() -> None:
    with respx.mock:
        respx.get(BASE_URL + "accounts/me").mock(side_effect=httpx.ConnectError("down"))
        await check_error_mapped(aeza().balance(), HosterError)
        respx.get(BASE_URL + "accounts/me").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(TimeoutError):
            await aeza().balance()


def test_check_aeza_cli(hoster: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "aeza", "--key", KEY]) == 0

    out = capsys.readouterr().out
    assert "Balance: €10.00" in out
    assert "Capabilities: autorenew, invoices, power, renew, topup, usage" in out
    assert "Servers: 4" in out
    assert "name-2 [100006] vps running (active) ip 192.0.2.10 plan CLTs-1 €5.93/1 mo" in out


def test_record_aeza_cli(
    hoster: respx.MockRouter,
    records: list[dict[str, Any]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_AEZA_KEY", KEY)

    assert main(["record", "aeza", "--key", "$TEST_AEZA_KEY", "--out", str(tmp_path)]) == 0

    written = sorted(tmp_path.glob("*.json"))
    assert len(written) == len(records) == len(capsys.readouterr().out.split())
    text = "".join(p.read_text(encoding="utf-8") for p in written)
    assert KEY not in text
