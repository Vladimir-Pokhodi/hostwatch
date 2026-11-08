"""Tests of the Template Hoster plugin on recorded fixtures (tests/fixtures/template_hoster)."""

from collections.abc import Iterator
from decimal import Decimal
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
from hostwatch.core.models import ServerStatus
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.plugins._template import BASE_URL, TemplateHosterPlugin
from tests.contract import check_contract, check_error_mapped
from tests.replay import load, replay


async def ignore(_: Secrets) -> None:
    pass


def plugin() -> TemplateHosterPlugin:
    return TemplateHosterPlugin(
        PluginContext({"api_key": "key-for-tests"}, httpx.AsyncClient(), ignore)
    )


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return load("template_hoster")


@pytest.fixture
def hoster(records: list[dict[str, Any]]) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        replay(router, records)
        yield router


async def test_passes_contract(hoster: respx.MockRouter) -> None:
    await check_contract(plugin())


async def test_reads_servers(hoster: respx.MockRouter) -> None:
    servers = await plugin().servers()

    assert servers, "record fixtures with at least one server"
    first = servers[0]
    assert first.status is not ServerStatus.UNKNOWN, first.raw_status
    assert first.price is not None and first.price.amount > Decimal(0)


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, AuthExpired), (403, Blocked), (429, RateLimited), (500, HosterError)],
)
async def test_http_errors_are_core_types(status: int, error: type[PluginError]) -> None:
    with respx.mock(base_url=BASE_URL) as router:
        router.route().respond(status)
        await check_error_mapped(plugin().servers(), error)


async def test_changed_answer_is_layout_changed() -> None:
    with respx.mock(base_url=BASE_URL) as router:
        router.route().respond(200, json={"items": []})
        await check_error_mapped(plugin().servers(), LayoutChanged)
