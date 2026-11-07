import httpx
import pytest

from hostwatch.core.errors import (
    AuthExpired,
    Blocked,
    HosterError,
    LayoutChanged,
    PluginError,
    RateLimited,
)
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.plugins.fake import FakePlugin
from tests.contract import check_contract, check_error_mapped


async def ignore(_: Secrets) -> None:
    pass


def fake(secrets: dict[str, str] | None = None) -> FakePlugin:
    return FakePlugin(PluginContext(secrets or {}, httpx.AsyncClient(), ignore))


async def test_fake_passes_contract() -> None:
    await check_contract(fake())


@pytest.mark.parametrize(
    ("fail", "error"),
    [
        ("auth_expired", AuthExpired),
        ("rate_limited", RateLimited),
        ("blocked", Blocked),
        ("layout_changed", LayoutChanged),
        ("hoster_error", HosterError),
    ],
)
async def test_fake_errors_are_core_types(fail: str, error: type[PluginError]) -> None:
    await check_error_mapped(fake({"fail": fail}).servers(), error)
