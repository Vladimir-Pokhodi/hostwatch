import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
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
    ServerKind,
    ServerStatus,
)
from hostwatch.core.plugin import Capability, PluginContext, Secrets, capabilities
from hostwatch.core.record import Scrubber
from hostwatch.plugins.regxa import SITE, RegxaPlugin
from tests.contract import check_contract, check_error_mapped
from tests.replay import load, replay

COOKIE = "rgx-session-token-for-tests"
SECRETS = {"rgx.session-token": COOKIE}
SERVICE = "100003"
SESSION = f"{SITE}/api/auth/session"
PROFILE = f"{SITE}/api/profile/client/100002"


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return load("regxa")


@pytest.fixture
def hoster(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with serving(records) as router:
        yield router


@contextmanager
def serving(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        replay(router, records)
        yield router


class Saved:
    def __init__(self) -> None:
        self.secrets: list[Secrets] = []

    async def __call__(self, secrets: Secrets) -> None:
        self.secrets.append(secrets)


def regxa(secrets: Secrets = SECRETS, save: Saved | None = None) -> RegxaPlugin:
    return RegxaPlugin(PluginContext(secrets, httpx.AsyncClient(), save or Saved()))


def answer(records: list[dict[str, Any]], path: str) -> Any:
    """The first recorded answer for a URL path, to change it in a test."""
    return next(r for r in records if httpx.URL(r["url"]).path == path)["json"]


def changed(records: list[dict[str, Any]], path: str, **changes: Any) -> list[dict[str, Any]]:
    records = copy.deepcopy(records)
    for record in records:
        if httpx.URL(record["url"]).path == path:
            target = record["json"][0] if isinstance(record["json"], list) else record["json"]
            target.update(changes)
    return records


def logged_in(router: respx.MockRouter) -> None:
    router.get(SESSION).respond(json={"user": {"client_id": "100002"}, "expires": "x"})


async def test_regxa_passes_contract(hoster: respx.MockRouter) -> None:
    await check_contract(regxa())


def test_capabilities() -> None:
    assert capabilities(RegxaPlugin) == {Capability.USAGE, Capability.INVOICES, Capability.TOPUP}
    assert not RegxaPlugin.login_only


async def test_servers_map_fields(hoster: respx.MockRouter) -> None:
    [server] = await regxa().servers()

    assert server.external_id == SERVICE
    assert server.name == "hostname-3"
    assert server.kind is ServerKind.VPS
    assert server.ip == "192.0.2.12"  # IPv4 first, though the IPv6 comes first
    assert server.plan == "Cloud VPS X1 - Europe"
    assert server.status is ServerStatus.RUNNING
    assert server.raw_status == "Active"
    assert server.price == Money.of("1000.5", "USD")
    assert server.period_months == 1
    assert server.next_charge_at == datetime(2026, 11, 28, tzinfo=UTC)
    assert server.autorenew is Autorenew.NOT_AVAILABLE
    assert server.billing is BillingModel.PERIODIC
    assert server.actions == frozenset()


async def test_requests_carry_the_session_and_client(hoster: respx.MockRouter) -> None:
    await regxa().servers()

    requests = [c.request for c in hoster.calls]
    assert all(r.headers["Cookie"] == f"rgx.session-token={COOKIE}" for r in requests)
    services = next(r for r in requests if r.url.path == "/api/services/getservices")
    assert services.url.params["clientId"] == "100002"
    # One session check per plugin call, however many requests follow.
    assert sum(r.url.path == "/api/auth/session" for r in requests) == 1


async def test_balance(hoster: respx.MockRouter) -> None:
    balance = await regxa().balance()

    assert balance.money == Money.of("1125.00", "USD")
    assert balance.hours_left is None


async def test_usage(hoster: respx.MockRouter) -> None:
    usage = await regxa().usage(SERVICE)

    assert usage.server_id == SERVICE
    assert usage.cpu_percent == 0.0
    assert usage.ram_percent == 80.2
    assert usage.disk_percent == 7.76
    assert usage.traffic_gb == 62.27
    status = [c for c in hoster.calls if "/vps/status/" in c.request.url.path]
    assert status[0].request.url.path == "/api/services/vps/status/100006"


async def test_usage_of_unknown_server(hoster: respx.MockRouter) -> None:
    with pytest.raises(HosterError):
        await regxa().usage("999")


async def test_invoices_newest_first_and_unpaid_opened(hoster: respx.MockRouter) -> None:
    invoices = await regxa().invoices()

    assert len(invoices) == 25
    newest = invoices[0]
    assert newest.id == "100007"
    assert newest.status is InvoiceStatus.UNPAID
    assert newest.amount == Money.of("1500.00", "USD")
    assert newest.description == "description-15"
    assert newest.due_at == datetime(2026, 11, 13, tzinfo=UTC)
    assert newest.server_id is None  # adding funds renews nothing
    statuses = [i.status for i in invoices]
    assert statuses.count(InvoiceStatus.UNPAID) == 2
    assert statuses.count(InvoiceStatus.CANCELLED) == 1
    opened = [c for c in hoster.calls if c.request.url.path.startswith("/api/billing/invoices/")]
    assert len(opened) == 2


async def test_renewal_invoice_points_to_its_server(records: list[dict[str, Any]]) -> None:
    records = copy.deepcopy(records)
    item = answer(records, "/api/billing/invoices/100007")["items"]["item"][0]
    item.update(type="Hosting", relid=int(SERVICE))

    with serving(records):
        invoices = await regxa().invoices()

    assert invoices[0].server_id == SERVICE


async def test_invoice_for_several_services_has_no_server(records: list[dict[str, Any]]) -> None:
    records = copy.deepcopy(records)
    items = answer(records, "/api/billing/invoices/100007")["items"]["item"]
    items[0].update(type="Hosting", relid=1)
    items.append({**items[0], "relid": 2})

    with serving(records):
        invoices = await regxa().invoices()

    assert invoices[0].server_id is None


async def test_no_invoices() -> None:
    with respx.mock as router:
        logged_in(router)
        router.get(f"{SITE}/api/billing/invoices").respond(
            json={"result": "success", "totalresults": 0, "invoices": []}
        )
        assert await regxa().invoices() == []


@pytest.mark.parametrize(
    ("power", "status"),
    [(1, ServerStatus.RUNNING), (0, ServerStatus.STOPPED), (-1, ServerStatus.UNKNOWN)],
)
async def test_power_state(records: list[dict[str, Any]], power: int, status: ServerStatus) -> None:
    with serving(changed(records, "/api/services/vps/status/100006", status=power)):
        [server] = await regxa().servers()

    assert server.status is status


async def test_status_as_a_bare_number(records: list[dict[str, Any]]) -> None:
    """While a VPS is set up, the status endpoint answers with the state alone."""
    records = copy.deepcopy(records)
    for record in records:
        if "/vps/status/" in record["url"]:
            record["json"] = 0

    with serving(records):
        [server] = await regxa().servers()
        usage = await regxa().usage(SERVICE)

    assert server.status is ServerStatus.STOPPED
    assert usage.cpu_percent is None and usage.ram_percent is None


async def test_suspended(records: list[dict[str, Any]]) -> None:
    with serving(changed(records, "/api/services/vps/details/100006", suspended=True)):
        [server] = await regxa().servers()
    assert server.status is ServerStatus.SUSPENDED

    with serving(changed(records, "/api/services/getservices", status="Suspended")) as router:
        [server] = await regxa().servers()
    assert server.status is ServerStatus.SUSPENDED
    assert server.raw_status == "Suspended"
    assert not any("/vps/status/" in c.request.url.path for c in router.calls)


async def test_terminated_and_non_vps_services_are_skipped(
    records: list[dict[str, Any]],
) -> None:
    records = copy.deepcopy(records)
    services = answer(records, "/api/services/getservices")
    hosting = {**services[0], "id": 7, "customfields": []}
    terminated = {**services[0], "id": 8, "status": "Terminated"}
    services.extend([hosting, terminated])

    with serving(records):
        servers = await regxa().servers()

    assert [s.external_id for s in servers] == [SERVICE]


async def test_one_time_service_has_no_price(records: list[dict[str, Any]]) -> None:
    records = changed(
        records, "/api/services/getservices", billingCycle="One Time", nextDueDate="0000-00-00"
    )
    with serving(records):
        [server] = await regxa().servers()

    assert server.price is None
    assert server.period_months is None
    assert server.next_charge_at is None


async def test_reissued_cookie_is_saved() -> None:
    """NextAuth answers the session check with a fresh cookie; the old chunks go."""
    saved = Saved()
    secrets = {"rgx.session-token.0": "old-0", "rgx.session-token.1": "old-1", "other": "x"}
    plugin = regxa(secrets, saved)
    with respx.mock as router:
        router.get(SESSION).respond(
            json={"user": {"client_id": "100002"}, "expires": "x"},
            headers=[
                ("Set-Cookie", "rgx.csrf-token=csrf; Path=/; HttpOnly; Secure"),
                ("Set-Cookie", "rgx.session-token=new-token; Path=/; HttpOnly; Secure"),
            ],
        )
        await plugin.check_login()

    assert saved.secrets == [{"other": "x", "rgx.session-token": "new-token"}]
    assert plugin.ctx.secrets == saved.secrets[0]


async def test_same_cookie_is_not_saved_again() -> None:
    saved = Saved()
    with respx.mock as router:
        router.get(SESSION).respond(
            json={"user": {"client_id": "100002"}},
            headers={"Set-Cookie": f"rgx.session-token={COOKIE}; Path=/"},
        )
        await regxa(save=saved).check_login()

    assert saved.secrets == []


async def test_login_check() -> None:
    with respx.mock as router:
        route = router.get(SESSION)
        route.respond(json={"user": {"email": "x"}, "expires": "2026-11-08"})
        await regxa().check_login()
        route.respond(json={})
        await check_error_mapped(regxa().check_login(), AuthExpired)


async def test_session_without_client_id_is_layout_change() -> None:
    with respx.mock as router:
        router.get(SESSION).respond(json={"user": {"email": "x"}})
        await check_error_mapped(regxa().balance(), LayoutChanged)


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"error": "Authentication required"}, AuthExpired),
        (307, {}, AuthExpired),
        (403, {}, Blocked),
        (403, {"error": "You are not authorized to access this client's services"}, HosterError),
        (429, {}, RateLimited),
        (500, {}, HosterError),
    ],
)
async def test_http_errors_are_core_types(
    status: int, body: dict[str, Any], error: type[PluginError]
) -> None:
    with respx.mock as router:
        logged_in(router)
        router.get(PROFILE).respond(status, json=body)
        await check_error_mapped(regxa().balance(), error)


async def test_hoster_error_keeps_the_message() -> None:
    with respx.mock as router:
        logged_in(router)
        router.get(PROFILE).respond(
            403, json={"success": False, "message": "You are not authorized"}
        )
        with pytest.raises(HosterError, match=r"HTTP 403: You are not authorized"):
            await regxa().balance()


@pytest.mark.parametrize(
    "body", [b"<html>maintenance</html>", b'{"success": true, "data": {"credit": "lots"}}']
)
async def test_unexpected_answer_is_layout_change(body: bytes) -> None:
    with respx.mock as router:
        logged_in(router)
        router.get(PROFILE).respond(200, content=body)
        await check_error_mapped(regxa().balance(), LayoutChanged)


async def test_network_errors() -> None:
    with respx.mock as router:
        router.get(SESSION).mock(side_effect=httpx.ConnectError("down"))
        await check_error_mapped(regxa().balance(), HosterError)
        router.get(SESSION).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(TimeoutError):
            await regxa().balance()


def test_record_text_scrubs_whmcs_fields() -> None:
    scrubber = Scrubber([])
    raw = {
        "userid": 4242,
        "total": "12.50",
        "credit": "12.50",
        "tax": "0.00",
        "ips": {"777001": "203.0.113.5"},
        "customfield": [{"name": "vps_uuid", "value": "abcdefgh12345678"}],
        "items": {"item": [{"relid": 31337, "type": "Hosting"}]},
    }

    scrubbed = json.loads(RegxaPlugin.record_text(json.dumps(raw), scrubber))

    assert scrubbed["userid"] != 4242 and isinstance(scrubbed["userid"], int)
    assert scrubbed["total"] == scrubbed["credit"] != "12.50"
    assert scrubbed["tax"] == "0.00"
    assert "777001" not in scrubbed["ips"]
    assert scrubbed["customfield"][0]["value"] == "vps_uuid-1"
    assert scrubbed["items"]["item"][0]["relid"] != 31337
    assert RegxaPlugin.record_text("<html>", scrubber) == "<html>"


def test_check_regxa_cli(hoster: respx.MockRouter, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "regxa", "--key", f"rgx.session-token={COOKIE}"]) == 0

    out = capsys.readouterr().out
    assert "Balance: $1 125.00" in out
    assert "Capabilities: invoices, topup, usage" in out
    assert "Servers: 1" in out
    assert (
        "hostname-3 [100003] vps running (Active) ip 192.0.2.12 plan Cloud VPS X1 - Europe"
        " $1 000.50/1 mo periodic next 2026-11-28 autorenew n/a" in out
    )


def test_record_regxa_cli(
    hoster: respx.MockRouter, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["record", "regxa", "--key", f"rgx.session-token={COOKIE}", "--out", str(tmp_path)])
        == 0
    )

    written = sorted(tmp_path.glob("*.json"))
    assert len(written) == len(capsys.readouterr().out.split())
    text = "".join(p.read_text(encoding="utf-8") for p in written)
    assert COOKIE not in text
