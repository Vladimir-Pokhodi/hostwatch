"""The order core (stage 13): catalog cache, limit, shortfall, price check before the order."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from hostwatch.core.catalog import HOURLY, OrderRequest, OrderResult, Price, first_charge, monthly
from hostwatch.core.logs import REDACTOR
from hostwatch.core.models import Money
from hostwatch.core.order import (
    CatalogCache,
    PriceChanged,
    check_limit,
    month_start,
    parse_limit,
    parse_ssh_key,
    place,
    server_name,
    shortfall,
)
from hostwatch.core.plugin import PluginContext
from hostwatch.order_cli import render
from hostwatch.plugins.fake import FakePlugin


def eur(amount: str) -> Money:
    return Money.of(amount, "EUR")


def rub(amount: str) -> Money:
    return Money.of(amount, "RUB")


def to_rub(money: Money, currency: str) -> Money | None:
    rates = {"EUR": Decimal(100), "RUB": Decimal(1)}
    if money.currency not in rates:
        return None
    return Money(amount=money.amount * rates[money.currency], currency=currency)


async def test_catalog_cache_keeps_an_hour() -> None:
    clock = [0.0]
    cache = CatalogCache(clock=lambda: clock[0])
    loads = 0

    async def load() -> object:
        nonlocal loads
        loads += 1
        return f"catalog {loads}"

    assert await cache.get(1, load) == "catalog 1"  # type: ignore[arg-type]
    clock[0] = 3599
    assert await cache.get(1, load) == "catalog 1"  # type: ignore[arg-type]
    clock[0] = 3601
    assert await cache.get(1, load) == "catalog 2"  # type: ignore[arg-type]
    cache.forget(1)
    assert await cache.get(1, load) == "catalog 3"  # type: ignore[arg-type]


def test_limit_converts_and_compares() -> None:
    check = check_limit(rub("1000"), [eur("4.50"), rub("100")], eur("4.50"), to_rub)
    assert check.spent == rub("550")
    assert check.this_order == rub("450")
    assert not check.over
    assert check_limit(rub("999.99"), [eur("5.50")], eur("4.50"), to_rub).over


def test_zero_limit_makes_every_order_over() -> None:
    assert check_limit(rub("0"), [], eur("0.01"), to_rub).over


def test_unknown_rate_counts_as_over_the_limit() -> None:
    assert check_limit(rub("100000"), [Money.of("1", "USD")], eur("1"), to_rub).over
    assert check_limit(rub("100000"), [], Money.of("1", "USD"), to_rub).over


def test_shortfall() -> None:
    assert shortfall(eur("1.00"), eur("4.50")) == eur("3.50")
    assert shortfall(eur("4.50"), eur("4.50")) is None
    assert shortfall(rub("10"), eur("4.50")) is None  # cannot compare: the hoster decides


def test_hourly_prices_are_for_730_hours() -> None:
    price = Price(period_months=HOURLY, amount=rub("350"))
    assert monthly(price) == rub("350")
    assert first_charge(rub("350"), HOURLY) == rub("0.48")
    assert first_charge(eur("16.91"), 3) == eur("16.91")
    assert monthly(Price(period_months=3, amount=eur("15.00"))) == eur("5.00")


def test_month_start_in_the_owners_zone() -> None:
    now = datetime(2026, 11, 30, 22, 0, tzinfo=UTC)  # 1 December in Moscow
    start = month_start(now, ZoneInfo("Europe/Moscow"))
    assert start.astimezone(ZoneInfo("Europe/Moscow")).day == 1
    assert start.astimezone(ZoneInfo("Europe/Moscow")).month == 12


def test_parsers() -> None:
    assert parse_limit("1 000,5") == Decimal("1000.5")
    assert parse_limit("0") == 0
    assert parse_limit("-1") is None
    assert parse_limit("1.001") is None
    assert parse_limit("abc") is None
    key = "ssh-ed25519 " + "A" * 68
    assert parse_ssh_key(f"  {key}   me@pc ") == f"{key} me@pc"
    assert parse_ssh_key("ssh-ed25519 short") is None
    assert parse_ssh_key("-----BEGIN OPENSSH PRIVATE KEY-----") is None
    assert server_name(datetime(2026, 11, 8, 15, 30)) == "hw-2611081530"


def _fake(**secrets: str) -> FakePlugin:
    async def save(_: object) -> None:
        pass

    return FakePlugin(PluginContext(secrets, httpx.AsyncClient(), save))


def _request(price: Money) -> OrderRequest:
    return OrderRequest(
        plan_id="small", os_id="debian-12", period_months=1, name="hw-1", price=price
    )


async def test_place_orders_at_the_confirmed_price() -> None:
    FakePlugin.calls.clear()
    result = await place(_fake(password="Very-Secret-Root-1"), _request(eur("4.50")))

    assert result.ok and result.server_id == "9"
    assert FakePlugin.calls == ["order small debian-12 1"]
    # Whatever logs the answer later masks the password.
    assert REDACTOR.redact("pw Very-Secret-Root-1") == "pw ***"


async def test_place_refuses_a_new_price() -> None:
    FakePlugin.calls.clear()
    with pytest.raises(PriceChanged) as caught:
        await place(_fake(order_price="4.60"), _request(eur("4.50")))
    assert caught.value.now == eur("4.60")
    assert FakePlugin.calls == []


def test_result_never_shows_the_password() -> None:
    result = OrderResult(ok=True, detail="done", password="Very-Secret-Root-1")
    assert "Very-Secret-Root-1" not in repr(result)
    assert "password" not in result.model_dump()


async def test_catalog_command_renders_the_cheapest_plans() -> None:
    catalog = await _fake().catalog()

    lines = render(catalog)

    assert lines[0].startswith("Locations: 1, plans: 2, OS: 2, SSH key at order: yes")
    assert "Fake Small [small] in Nowhere" in lines[1]
    assert "1 mo €4.50, 12 mo €45.00" in lines[1]
