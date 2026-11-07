"""Command line.

`python -m hostwatch` runs the bot and the polling scheduler;
`python -m hostwatch check <plugin_or_account> [--key ...]` reads and prints;
`python -m hostwatch record <plugin_or_account> [--key ...]` saves scrubbed test fixtures;
`python -m hostwatch login <plugin_or_account>` asks the running bot for a login link.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.config import ConfigError, load_settings
from hostwatch.core.errors import describe
from hostwatch.core.forecast import RECONCILE_WINDOW, Forecast, forecast
from hostwatch.core.http import make_client
from hostwatch.core.logs import register_secrets, setup_logging
from hostwatch.core.models import Balance, Money, Server
from hostwatch.core.plugin import (
    ApiKeyAuth,
    Capability,
    Plugin,
    PluginContext,
    Secrets,
    SupportsInvoices,
    SupportsUsage,
    capabilities,
)
from hostwatch.core.record import Recorder, Scrubber, write_fixtures
from hostwatch.core.registry import Registry, discover
from hostwatch.storage.db import connect
from hostwatch.storage.repositories import AccountRepository, BalanceHistoryRepository, NotFound

T = TypeVar("T")
DB_NAME = "hostwatch.sqlite3"
FIXTURES_DIR = Path("tests/fixtures")


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
    if args.command is None:
        return _run_bot()
    if args.command == "login":
        return _login_link(args.target)
    handler = setup_logging(logging.DEBUG if args.verbose else logging.WARNING, stream=sys.stderr)
    try:
        registry = discover(_plugins_dir())
        if args.command == "record":
            written = asyncio.run(_record(registry, args.target, args.key, args.out))
            print("\n".join(str(path) for path in written))
            return 0
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
    commands = parser.add_subparsers(dest="command")
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
        help="secret for a plugin id; a bare value goes to the plugin's first key field;"
        " '$NAME' reads it from the environment variable NAME",
    )
    check.add_argument("--lang", choices=("en", "ru"), default="en")
    check.add_argument("-v", "--verbose", action="store_true", help="log HTTP requests")
    record = commands.add_parser(
        "record",
        help="run a plugin's read calls and save its scrubbed HTTP answers as test fixtures",
    )
    record.add_argument(
        "target", help="plugin id (uses --key) or account label/id from the database"
    )
    record.add_argument(
        "--key",
        action="append",
        default=[],
        metavar="[FIELD=]VALUE",
        help="secret for a plugin id, as for check",
    )
    record.add_argument(
        "--out", type=Path, help="folder for the fixtures, default tests/fixtures/<plugin>"
    )
    record.add_argument("-v", "--verbose", action="store_true", help="log HTTP requests")
    login = commands.add_parser(
        "login",
        help="ask the running bot for a one-time login link; the bot also sends it to the owner",
    )
    login.add_argument(
        "target", help="account label/id, or a plugin id (a new account unless there is one)"
    )
    return parser


def _login_link(target: str) -> int:
    """Run inside the bot's container: `docker compose exec hostwatch hostwatch login timeweb`."""
    import httpx

    from hostwatch.web.server import ADMIN_PORT

    try:
        response = httpx.post(
            f"http://127.0.0.1:{ADMIN_PORT}/link", json={"target": target}, timeout=30
        )
    except httpx.HTTPError as exc:
        print(f"error: the bot is not running here ({type(exc).__name__})", file=sys.stderr)
        return 1
    data = response.json()
    if response.status_code != 200:
        print(f"error: {data.get('error')}", file=sys.stderr)
        return 1
    print(f"{data['hoster']}: {data['url']}")
    return 0


def _run_bot() -> int:
    from hostwatch.bot.app import run

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    setup_logging(logging.INFO)
    # Ctrl+C is the normal way to stop the bot when it runs in a terminal.
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings))
    return 0


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
            secrets[name] = _from_env(value)
        elif fields:
            secrets[fields[0]] = _from_env(item)
        else:
            raise CheckFailed(f"plugin {plugin_cls.meta.id} has no key field; use FIELD=VALUE")
    return secrets


def _from_env(value: str) -> str:
    """`$NAME` (quoted for the shell) reads the secret from the environment variable NAME,
    so the secret stays out of the process list."""
    if not value.startswith("$"):
        return value
    try:
        return os.environ[value[1:]]
    except KeyError:
        raise CheckFailed(f"environment variable {value[1:]} is not set") from None


async def _record(registry: Registry, target: str, keys: list[str], out: Path | None) -> list[Path]:
    recorder = Recorder()
    if target in registry:
        plugin_cls = registry.get(target)
        secrets: Secrets = parse_keys(plugin_cls, keys)
        register_secrets(secrets.values())
        async with recorder.attach(make_client(plugin_cls.meta.id)) as http:
            plugin = plugin_cls(PluginContext(secrets, http, _discard_secrets))

            def invoke(action: Callable[[Plugin], Awaitable[T]]) -> Awaitable[T]:
                return action(plugin)

            await _exercise(plugin_cls, invoke)
    elif keys:
        raise CheckFailed(f"unknown plugin {target!r}; known: {', '.join(registry.ids())}")
    else:
        plugin_cls, secrets = await _record_account(registry, target, recorder)
    scrubber = Scrubber(secrets.values(), plugin_cls.record_names)
    records = scrubber.exchanges(recorder.exchanges)
    return write_fixtures(records, out or FIXTURES_DIR / plugin_cls.meta.id)


async def _discard_secrets(new: Secrets) -> None:
    register_secrets(new.values())
    print("note: the hoster issued new secrets; they are not saved", file=sys.stderr)


async def _record_account(
    registry: Registry, target: str, recorder: Recorder
) -> tuple[type[Plugin], Secrets]:
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
        plugin_cls = registry.get(accounts.get(account_id).plugin_id)
        runner = AccountRunner(
            accounts, registry, lambda pid: recorder.attach(make_client(pid, settings.tg_proxy))
        )

        def invoke(action: Callable[[Plugin], Awaitable[T]]) -> Awaitable[T]:
            return runner.call(account_id, action)

        await _exercise(plugin_cls, invoke)
        # Read after the calls: a token rotated during them must be scrubbed too.
        return plugin_cls, accounts.load_secrets(account_id)
    finally:
        conn.close()


async def _exercise(plugin_cls: type[Plugin], invoke: Invoke) -> None:
    """Every read-only call the plugin supports; nothing that changes state at the hoster."""

    async def step(name: str, action: Callable[[Plugin], Awaitable[Any]]) -> Any:
        try:
            return await invoke(action)
        except Exception as exc:
            raise CheckFailed(f"{name}: {describe(exc)}") from None

    servers: list[Server] = await step("servers", lambda p: p.servers())
    await step("balance", lambda p: p.balance())
    caps = capabilities(plugin_cls)
    if Capability.USAGE in caps and servers:
        server_id = servers[0].external_id
        await step("usage", lambda p: cast(SupportsUsage, p).usage(server_id))
    if Capability.INVOICES in caps:
        await step("invoices", lambda p: cast(SupportsInvoices, p).invoices())


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
