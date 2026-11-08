"""Ordering in the plugins (stage 13), against recorded catalogs and mocked order answers.

No order was sent to a live hoster: the order requests follow docs/research-orders.md."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hostwatch.core.catalog import HOURLY, OrderRequest
from hostwatch.core.errors import AuthExpired, HosterError
from hostwatch.core.models import Money
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.plugins.aeza import BASE_URL as AEZA
from hostwatch.plugins.aeza import AezaPlugin
from hostwatch.plugins.regxa import RegxaPlugin
from hostwatch.plugins.timeweb.plugin import TimewebPlugin

FIXTURES = Path(__file__).parent / "fixtures" / "orders"
TIMEWEB = "https://api.timeweb.cloud/api/v1/"
REGXA = "https://regxa.com"
KEY = "ssh-ed25519 " + "B" * 68 + " owner@pc"
PASSWORD = "Root-Pass-From-Hoster-1"


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def body(route: respx.Route) -> Any:
    return json.loads(route.calls.last.request.content)


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


async def _discard(_: Secrets) -> None:
    pass


def request(
    plan: str, os_id: str, period: int, price: Money, key: str | None = None
) -> OrderRequest:
    return OrderRequest(
        plan_id=plan, os_id=os_id, period_months=period, name="hw-1", price=price, ssh_key=key
    )


# Timeweb


@pytest.fixture
def timeweb(http: httpx.AsyncClient) -> TimewebPlugin:
    return TimewebPlugin(PluginContext({"api_token": "t" * 30}, http, _discard))


@pytest.fixture
def tw_catalog() -> AsyncIterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(TIMEWEB + "presets/servers").respond(text=text("timeweb/presets-servers.json"))
        router.get(TIMEWEB + "os/servers").respond(text=text("timeweb/os-servers.json"))
        yield router


async def test_timeweb_catalog(timeweb: TimewebPlugin, tw_catalog: respx.MockRouter) -> None:
    catalog = await timeweb.catalog()

    assert catalog.ssh_keys and catalog.tested
    assert catalog.used_locations()[0].name == "Екатеринбург"
    cheapest = catalog.plans_in("ru-4")[0]
    assert cheapest.name == "Cloud EKB 15"
    assert cheapest.specs == "1 CPU · 1 GB RAM · 15 GB NVME"
    assert cheapest.price(HOURLY) == Money.of("350", "RUB")
    names = {s.name for s in catalog.systems}
    assert "Ubuntu 26.04" in names
    assert not any(n.startswith(("Windows", "Bitrix")) for n in names)
    # Rocky asks for 2 GB of RAM: not on a 1 GB plan.
    assert "123" not in cheapest.os_ids and "123" in catalog.plans_in("ru-4")[1].os_ids


async def test_timeweb_order_adds_the_key_and_returns_the_password(
    timeweb: TimewebPlugin, tw_catalog: respx.MockRouter
) -> None:
    tw_catalog.get(TIMEWEB + "ssh-keys").respond(json={"ssh_keys": [], "meta": {"total": 0}})
    add_key = tw_catalog.post(TIMEWEB + "ssh-keys").respond(
        201, json={"ssh_key": {"id": 7, "body": KEY}}
    )
    create = tw_catalog.post(TIMEWEB + "servers").respond(
        201, json={"server": {"id": 42, "status": "installing", "root_pass": PASSWORD}}
    )
    wanted = request("6767", "145", HOURLY, Money.of("350", "RUB"), KEY)

    assert await timeweb.quote(wanted) == Money.of("350", "RUB")
    result = await timeweb.order(wanted)

    assert body(add_key) == {"name": "hostwatch", "body": KEY, "is_default": False}
    assert body(create) == {"name": "hw-1", "preset_id": 6767, "os_id": 145, "ssh_keys_ids": [7]}
    assert result.ok and result.server_id == "42"
    assert result.password == PASSWORD
    assert PASSWORD not in result.detail


async def test_timeweb_order_finds_a_key_already_there(
    timeweb: TimewebPlugin, tw_catalog: respx.MockRouter
) -> None:
    tw_catalog.get(TIMEWEB + "ssh-keys").respond(
        json={"ssh_keys": [{"id": 3, "body": KEY.rsplit(" ", 1)[0] + " other"}]}
    )
    add_key = tw_catalog.post(TIMEWEB + "ssh-keys")
    create = tw_catalog.post(TIMEWEB + "servers").respond(201, json={"server": {"id": 42}})

    await timeweb.order(request("6767", "145", HOURLY, Money.of("350", "RUB"), KEY))

    assert not add_key.called
    assert body(create)["ssh_keys_ids"] == [3]


async def test_timeweb_refusal_is_the_hosters_word(
    timeweb: TimewebPlugin, tw_catalog: respx.MockRouter
) -> None:
    tw_catalog.post(TIMEWEB + "servers").respond(
        403, json={"error_code": "no_balance_for_month", "message": "echo hw-1"}
    )

    with pytest.raises(HosterError, match="no_balance_for_month"):
        await timeweb.order(request("6767", "145", HOURLY, Money.of("350", "RUB")))


async def test_timeweb_password_and_template(
    timeweb: TimewebPlugin, tw_catalog: respx.MockRouter
) -> None:
    tw_catalog.get(TIMEWEB + "servers/42").respond(
        json={
            "server": {
                "id": 42,
                "status": "on",
                "preset_id": 6767,
                "os": {"id": 145, "name": "ubuntu"},
                "location": "ru-4",
                "root_pass": PASSWORD,
            }
        }
    )

    assert await timeweb.root_password("42") == PASSWORD
    template = await timeweb.order_template("42")
    assert (template.location_id, template.plan_id, template.os_id) == ("ru-4", "6767", "145")
    assert template.period_months == HOURLY


async def test_timeweb_quote_of_a_plan_gone(
    timeweb: TimewebPlugin, tw_catalog: respx.MockRouter
) -> None:
    with pytest.raises(HosterError, match="not sold"):
        await timeweb.quote(request("1", "145", HOURLY, Money.of("350", "RUB")))


# Aeza


@pytest.fixture
def aeza(http: httpx.AsyncClient) -> AezaPlugin:
    return AezaPlugin(PluginContext({"api_key": "a" * 30}, http, _discard))


@pytest.fixture
def az_catalog() -> AsyncIterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(AEZA + "accounts/me").respond(json={"balance": 1000, "currency": "eur"})
        router.get(AEZA + "services/products").respond(text=text("aeza/services-products-vps.json"))
        router.get(AEZA + "services/groups").respond(text=text("aeza/services-groups.json"))
        router.get(AEZA + "services/operating-systems").respond(
            text=text("aeza/services-operating-systems.json")
        )
        yield router


async def test_aeza_catalog(aeza: AezaPlugin, az_catalog: respx.MockRouter) -> None:
    catalog = await aeza.catalog()

    msk = catalog.plan("153")
    assert msk is not None
    assert msk.name == "MSKs-1 · Shared"
    assert catalog.location(msk.location_id) is not None
    assert msk.price(1) == Money.of("5.93", "EUR")
    assert msk.price(12) == Money.of("62.63", "EUR")
    assert msk.price(HOURLY) == Money.of("29.20", "EUR")  # €0.04 × 730 hours
    assert catalog.plan("462") is None  # «Custom SPBs» is built from resources
    assert all(not s.id.startswith("windows") for s in catalog.systems)
    assert catalog.ssh_keys and catalog.tested


async def test_aeza_catalog_skips_products_of_other_kinds(
    aeza: AezaPlugin, az_catalog: respx.MockRouter
) -> None:
    answer = json.loads(text("aeza/services-products-vps.json"))
    # A domain has no group and parameters of its own (seen live 2026-11-08).
    domain = {"id": 1, "name": ".ru", "groupId": None, "type": {"slug": "domain"}}
    answer["items"].append(domain)
    answer["total"] += 1
    az_catalog.get(AEZA + "services/products").respond(json=answer)

    assert len((await aeza.catalog()).plans) == 101


async def test_aeza_quote_reads_the_price_and_availability(
    aeza: AezaPlugin, az_catalog: respx.MockRouter
) -> None:
    price = az_catalog.get(AEZA + "services/orders/price").respond(
        json={"totalPrice": 593, "availability": {"status": "ok"}}
    )
    wanted = request("153", "ubuntu_2404", 1, Money.of("5.93", "EUR"))

    assert await aeza.quote(wanted) == Money.of("5.93", "EUR")
    params = price.calls.last.request.url.params
    assert (params["productId"], params["paymentTerm"], params["termCount"]) == (
        "153",
        "month",
        "1",
    )

    price.respond(json={"totalPrice": 593, "availability": {"status": "no_resources"}})
    with pytest.raises(HosterError, match="no_resources"):
        await aeza.quote(wanted)


async def test_aeza_order_sends_the_wizard_body(
    aeza: AezaPlugin, az_catalog: respx.MockRouter
) -> None:
    az_catalog.get(AEZA + "services/ssh-keys").respond(json={"items": [], "total": 0})
    az_catalog.post(AEZA + "services/ssh-keys").respond(201, json={"id": 11})
    create = az_catalog.post(AEZA + "services/orders").respond(
        201, json={"id": 900, "serviceId": 555, "orderInfo": {"createdServiceIds": [555]}}
    )
    os_id = json.loads(text("aeza/services-operating-systems.json"))[0]["slug"]

    result = await aeza.order(request("153", os_id, 1, Money.of("5.93", "EUR"), KEY))

    assert body(create) == {
        "orders": [
            {
                "count": 1,
                "term": "month",
                "termCount": 1,
                "name": "hw-1",
                "productId": 153,
                "parameters": {"os": os_id, "sshKeyIds": [11]},
                "autoProlong": True,
                "backups": False,
            }
        ],
        "method": "balance",
    }
    assert result.ok and result.server_id == "555"
    assert "transaction 900" in result.detail


async def test_aeza_error_in_a_200_body_is_a_refusal(
    aeza: AezaPlugin, az_catalog: respx.MockRouter
) -> None:
    az_catalog.post(AEZA + "services/orders").respond(
        200, json={"error": {"message": "Недостаточно средств", "slug": "no_money"}}
    )
    with pytest.raises(HosterError, match="Недостаточно средств"):
        await aeza.order(request("153", "ubuntu_2404", HOURLY, Money.of("29.20", "EUR")))


async def test_aeza_password(aeza: AezaPlugin, az_catalog: respx.MockRouter) -> None:
    az_catalog.get(AEZA + "services/100006").respond(
        json={"data": {"id": 100006, "secureParameters": {"password": PASSWORD}}}
    )
    az_catalog.get(AEZA + "services/100007").respond(json={"id": 1, "secureParameters": {}})

    assert await aeza.root_password("100006") == PASSWORD
    with pytest.raises(HosterError):
        await aeza.root_password("100007")
    with pytest.raises(HosterError):
        await aeza.root_password("../x")


# Regxa


@pytest.fixture
def regxa(http: httpx.AsyncClient) -> RegxaPlugin:
    return RegxaPlugin(PluginContext({"rgx.session-token": "s" * 30}, http, _discard))


@pytest.fixture
def rx_catalog() -> AsyncIterator[respx.MockRouter]:
    service = {
        "id": 7,
        "name": "Cloud VPS X2 - Europe\t",
        "status": "Active",
        "nextDueDate": "2026-12-01",
        "amount": "7.99",
        "billingCycle": "Monthly",
        "customfields": {"customfield": [{"name": "vpsid", "value": "99"}]},
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(REGXA + "/api/auth/session").respond(json={"user": {"client_id": "5"}})
        router.get(REGXA + "/api/cloudvps/order-plans").respond(
            text=text("regxa/cloudvps-order-plans.json")
        )
        router.get(REGXA + "/api/services/getservices").respond(json=[service])
        yield router


async def test_regxa_catalog_is_marked_untested(
    regxa: RegxaPlugin, rx_catalog: respx.MockRouter
) -> None:
    catalog = await regxa.catalog()

    assert not catalog.tested and not catalog.ssh_keys
    x1 = catalog.plan("31-27")
    assert x1 is not None
    assert x1.name == "Cloud VPS X1 - Europe"
    assert catalog.location("27") is not None
    assert x1.price(1) == Money.of("4.50", "USD")
    assert x1.price(24) == Money.of("102.60", "USD")  # 5 % off
    assert x1.price(36) == Money.of("145.80", "USD")  # 10 % off


async def test_regxa_order_pays_with_credit(
    regxa: RegxaPlugin, rx_catalog: respx.MockRouter
) -> None:
    create = rx_catalog.post(REGXA + "/api/orders/cloud/create-order").respond(
        json={"success": True, "useCredit": True, "productIds": [123]}
    )
    wanted = request("31-27", "26", 1, Money.of("4.50", "USD"))

    assert await regxa.quote(wanted) == Money.of("4.50", "USD")
    result = await regxa.order(wanted)

    sent = body(create)
    assert sent["selectedPlan"]["id"] == 31 and sent["selectedPlan"]["location_id"] == 27
    assert sent["selectedLocation"]["id"] == 27
    assert sent["selectedDuration"]["duration_months"] == 1
    assert sent["selectedOS"] == "Ubuntu"
    assert sent["paymentMethod"] == "stripe"
    assert sent["quantity"] == 1 and sent["ipv6Enabled"] is False
    assert sent["operatingSystems"]
    assert result.ok and result.server_id == "123"


async def test_regxa_order_with_an_invoice_and_a_refusal(
    regxa: RegxaPlugin, rx_catalog: respx.MockRouter
) -> None:
    create = rx_catalog.post(REGXA + "/api/orders/cloud/create-order")
    wanted = request("31-27", "26", 1, Money.of("4.50", "USD"))

    create.respond(json={"success": True, "invoiceId": 4321})
    result = await regxa.order(wanted)
    assert result.ok and result.invoice_id == "4321" and result.server_id is None

    create.respond(json={"success": False, "error": "Out of stock"})
    with pytest.raises(HosterError, match="Out of stock"):
        await regxa.order(wanted)


async def test_regxa_password_and_template(
    regxa: RegxaPlugin, rx_catalog: respx.MockRouter
) -> None:
    rx_catalog.get(REGXA + "/api/services/vps/password/99").respond(
        json={"username": "root", "password": PASSWORD}
    )

    assert await regxa.root_password("7") == PASSWORD
    template = await regxa.order_template("7")
    # «X2 - Europe» is sold in four locations: the plan cannot be told, the term can.
    assert template.plan_id is None and template.period_months == 1


async def test_regxa_without_a_session(http: httpx.AsyncClient) -> None:
    plugin = RegxaPlugin(PluginContext({"rgx.session-token": "s" * 30}, http, _discard))
    with respx.mock() as router:
        router.get(REGXA + "/api/auth/session").respond(json={})
        with pytest.raises(AuthExpired):
            await plugin.catalog()
