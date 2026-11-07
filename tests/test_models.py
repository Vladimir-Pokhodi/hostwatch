from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from hostwatch.core.models import Money
from hostwatch.core.plugin import Capability, Plugin, PluginContext, Secrets, capabilities
from hostwatch.plugins.fake import FakePlugin


def test_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        Money(amount=4.5, currency="EUR")  # type: ignore[arg-type]


def test_money_rejects_bad_currency() -> None:
    with pytest.raises(ValidationError):
        Money.of("1", "eur")


def test_money_arithmetic_keeps_decimal() -> None:
    total = Money.of("4.50", "EUR") + Money.of("0.10", "EUR")

    assert total == Money(amount=Decimal("4.60"), currency="EUR")
    assert Money.of("1", "EUR") * 3 == Money.of("3", "EUR")


def test_money_refuses_mixed_currencies() -> None:
    with pytest.raises(ValueError, match="currency mismatch"):
        _ = Money.of("1", "EUR") + Money.of("1", "USD")


@pytest.mark.parametrize(
    ("money", "text"),
    [
        (Money.of("4.5", "EUR"), "€4.50"),
        (Money.of("-1", "USD"), "-$1.00"),
        (Money.of("1500", "RUB"), "1 500.00 ₽"),
        (Money.of("3", "KZT"), "3.00 KZT"),
    ],
)
def test_money_str(money: Money, text: str) -> None:
    assert str(money) == text


class Minimal(Plugin):
    async def servers(self) -> list:  # type: ignore[type-arg]
        return []

    async def balance(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class WithPower(Minimal):
    async def power(self, server_id, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError


async def ignore(_: Secrets) -> None:
    pass


def test_capabilities_follow_methods() -> None:
    ctx = PluginContext({}, httpx.AsyncClient(), ignore)

    assert capabilities(Minimal(ctx)) == set()
    assert capabilities(WithPower(ctx)) == {Capability.POWER}
    assert capabilities(WithPower) == {Capability.POWER}


def test_fake_has_every_capability() -> None:
    assert capabilities(FakePlugin) == set(Capability)
