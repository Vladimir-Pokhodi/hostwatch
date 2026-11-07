"""Contract every plugin must pass (spec, decision 14).

A plugin's test module builds the plugin on recorded fixtures and calls these helpers:

    await check_contract(plugin)
    await check_error_mapped(plugin_with_dead_key.servers(), AuthExpired)
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable
from decimal import Decimal

import pytest

from hostwatch.core.errors import PluginError
from hostwatch.core.models import Balance, Money, Server
from hostwatch.core.plugin import (
    ACTION_CAPABILITY,
    PROTOCOLS,
    ApiKeyAuth,
    BrowserAuth,
    Plugin,
    capabilities,
)


async def check_contract(plugin: Plugin) -> None:
    check_meta(type(plugin))
    check_capability_methods(plugin)
    servers = await plugin.servers()
    balance = await plugin.balance()
    check_servers(plugin, servers)
    check_balance(plugin, balance)


async def check_error_mapped(call: Awaitable[object], expected: type[PluginError]) -> None:
    """A failing hoster call must surface as a core error type, never a raw one."""
    with pytest.raises(expected):
        await call


def check_meta(cls: type[Plugin]) -> None:
    meta = cls.meta
    assert re.fullmatch(r"[a-z][a-z0-9_]*", meta.id), meta.id
    assert meta.name
    assert meta.site.startswith("https://")
    assert re.fullmatch(r"[A-Z]{3}", meta.currency)
    assert meta.auth, "a plugin needs at least one way to log in"
    for auth in meta.auth:
        assert isinstance(auth, ApiKeyAuth | BrowserAuth)
        if isinstance(auth, ApiKeyAuth):
            assert auth.fields
        else:
            assert auth.start_url.startswith("https://")
            assert auth.allowed_domains


def check_capability_methods(plugin: Plugin) -> None:
    """Every method a capability needs is a coroutine function, and half a protocol is a bug."""
    for capability, protocol in PROTOCOLS.items():
        names = [name for name in vars(protocol) if not name.startswith("_")]
        present = [name for name in names if hasattr(plugin, name)]
        assert present in ([], names), f"{capability}: has {present} of {names}"
        for name in present:
            assert inspect.iscoroutinefunction(getattr(plugin, name)), name


def check_servers(plugin: Plugin, servers: list[Server]) -> None:
    caps = capabilities(plugin)
    ids = [server.external_id for server in servers]
    assert len(ids) == len(set(ids)), "server ids must be unique"
    for server in servers:
        assert isinstance(server, Server)
        if server.price is not None:
            check_money(server.price)
        for action in server.actions:
            needed = ACTION_CAPABILITY[action]
            assert needed in caps, f"{server.name}: action {action} without capability {needed}"


def check_balance(plugin: Plugin, balance: Balance) -> None:
    assert isinstance(balance, Balance)
    check_money(balance.money)
    assert balance.money.currency == plugin.meta.currency


def check_money(money: Money) -> None:
    assert isinstance(money.amount, Decimal)
    assert money.amount.is_finite()
