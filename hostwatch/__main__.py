"""Command line: `python -m hostwatch check <plugin_or_account> [--key ...]`."""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.config import ConfigError, load_settings
from hostwatch.core.errors import describe
from hostwatch.core.forecast import RECONCILE_WINDOW, Forecast, forecast
from hostwatch.core.http import make_client
from hostwatch.core.logs import register_secrets, setup_logging
from hostwatch.core.models import Balance, Money, Server
from hostwatch.core.plugin import ApiKeyAuth, Plugin, PluginContext, Secrets, capabilities
from hostwatch.core.registry import Registry, discover
from hostwatch.storage.db import connect
from hostwatch.storage.repositories import AccountRepository, BalanceHistoryRepository, NotFound

T = TypeVar("T")
DB_NAME = "hostwatch.sqlite3"


class CheckFailed(Exception):
    pass


@dataclass(frozen=True)
class CheckResult:
    plugin: type[Plugin]
    servers: list[Server]
    balance: Balance
    timings: dict[str, float]
    forecast: Forecast


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    # Windows pipes default to a legacy code page; Russian text and € need UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    handler = setup_logging(logging.DEBUG if args.verbose else logging.WARNING, stream=sys.stderr)
    try:
        registry = discover(_plugins_dir())
        result = asyncio.run(_check(registry, args.target, args.key))
    except CheckFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        logging.getLogger().removeHandler(handler)
    print(render(result, args.lang))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hostwatch")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser(
        "check", help="read servers, balance and forecast from a hoster without changing anything"
    )
    check.add_argument(
        "target", help="plugin id (uses --key) or account label/id from the database"
    )
    check.add_argument(
        "--key",
        action="append",
        default=[],
        metavar="[FIELD=]VALUE",
        help="secret for a plugin id; a bare value goes to the plugin's first key field",
    )
    check.add_argument("--lang", choices=("en", "ru"), default="en")
    check.add_argument("-v", "--verbose", action="store_true", help="log HTTP requests")
    return parser


def _plugins_dir() -> Path | None:
    value = os.environ.get("PLUGINS_DIR")
    return Path(value) if value else None


async def _check(registry: Registry, target: str, keys: list[str]) -> CheckResult:
    if target in registry:
        return await _check_plugin(registry.get(target), keys)
    if keys:
        raise CheckFailed(f"unknown plugin {target!r}; known: {', '.join(registry.ids())}")
    return await _check_account(registry, target)


async def _check_plugin(plugin_cls: type[Plugin], keys: list[str]) -> CheckResult:
    secrets = parse_keys(plugin_cls, keys)
    register_secrets(secrets.values())

    async def discard(new: Secrets) -> None:
        register_secrets(new.values())
        print("note: the hoster issued new secrets; they are not saved", file=sys.stderr)

    async with make_client(plugin_cls.meta.id) as http:
        plugin = plugin_cls(PluginContext(secrets, http, discard))

        def invoke(action: Callable[[Plugin], Awaitable[T]]) -> Awaitable[T]:
            return action(plugin)

        return await _read(plugin_cls, invoke, history=[])


async def _check_account(registry: Registry, target: str) -> CheckResult:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise CheckFailed(
            f"{target!r} is not a plugin, and the database is unavailable:\n{exc}"
        ) from None
    path = settings.data_dir / DB_NAME
    if not path.is_file():
        raise CheckFailed(f"{target!r} is not a plugin, and there is no database at {path}")
    conn = connect(path)
    try:
        accounts = AccountRepository(conn, settings.encryption_key)
        account_id = _find_account(accounts, target)
        account = accounts.get(account_id)
        runner = AccountRunner(accounts, registry, lambda pid: make_client(pid, settings.tg_proxy))
        since = datetime.now(UTC) - RECONCILE_WINDOW
        history = BalanceHistoryRepository(conn).since(account_id, since)

        def invoke(action: Callable[[Plugin], Awaitable[T]]) -> Awaitable[T]:
            return runner.call(account_id, action)

        return await _read(registry.get(account.plugin_id), invoke, history)
    finally:
        conn.close()


def _find_account(accounts: AccountRepository, target: str) -> int:
    if target.isdigit():
        try:
            return accounts.get(int(target)).id
        except NotFound:
            pass
    found = accounts.find(target)
    if len(found) != 1:
        raise CheckFailed(
            f"no plugin or account named {target!r}"
            if not found
            else f"several accounts are labelled {target!r}; use the id"
        )
    return found[0].id


class Invoke(Protocol):
    def __call__(self, action: Callable[[Plugin], Awaitable[T]], /) -> Awaitable[T]: ...


async def _read(
    plugin_cls: type[Plugin], invoke: Invoke, history: list[tuple[datetime, Money]]
) -> CheckResult:
    timings: dict[str, float] = {}

    async def timed(name: str, action: Callable[[Plugin], Awaitable[T]]) -> T:
        started = time.perf_counter()
        try:
            return await invoke(action)
        except Exception as exc:
            raise CheckFailed(f"{name}: {describe(exc)}") from None
        finally:
            timings[name] = time.perf_counter() - started

    servers = await timed("servers", lambda p: p.servers())
    balance = await timed("balance", lambda p: p.balance())
    result_forecast = forecast(balance, servers, datetime.now(UTC), history)
    return CheckResult(plugin_cls, servers, balance, timings, result_forecast)


def parse_keys(plugin_cls: type[Plugin], keys: list[str]) -> dict[str, str]:
    fields = [
        f for auth in plugin_cls.meta.auth if isinstance(auth, ApiKeyAuth) for f in auth.fields
    ]
    secrets: dict[str, str] = {}
    for item in keys:
        name, sep, value = item.partition("=")
        if sep and name:
            secrets[name] = value
        elif fields:
            secrets[fields[0]] = item
        else:
            raise CheckFailed(f"plugin {plugin_cls.meta.id} has no key field; use FIELD=VALUE")
    return secrets


def render(result: CheckResult, lang: str) -> str:
    meta = result.plugin.meta
    caps = sorted(cap.value for cap in capabilities(result.plugin)) or ["none"]
    lines = [
        f"{meta.name} ({meta.id}), {meta.site}",
        "Response time: "
        + ", ".join(f"{name} {seconds:.2f} s" for name, seconds in result.timings.items()),
        f"Balance: {result.balance.money}"
        + ("" if result.balance.hours_left is None else f" ({result.balance.hours_left:g} h left)"),
        f"Capabilities: {', '.join(caps)}",
        f"Servers: {len(result.servers)}",
    ]
    lines.extend(f"  {_server_line(server)}" for server in result.servers)
    lines.append("Forecast:")
    lines.extend(f"  {line}" for line in result.forecast.explain(lang).splitlines())
    return "\n".join(lines)


def _server_line(s: Server) -> str:
    price = "price ?"
    if s.price is not None:
        per = "h" if s.period_months is None else f"{s.period_months} mo"
        price = f"{s.price}/{per}"
    next_charge = "-" if s.next_charge_at is None else s.next_charge_at.strftime("%Y-%m-%d")
    actions = ",".join(sorted(a.value for a in s.actions)) or "-"
    return (
        f"{s.name} [{s.external_id}] {s.kind.value} {s.status.value} ({s.raw_status})"
        f" ip {s.ip or '-'} plan {s.plan or '-'} {price} {s.billing.value}"
        f" next {next_charge} autorenew {s.autorenew.value} actions {actions}"
    )


if __name__ == "__main__":
    sys.exit(main())
