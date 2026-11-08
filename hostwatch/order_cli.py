"""`hostwatch catalog <account>`: read what a hoster sells and ask the price of the cheapest
monthly plan (stage 13). Read-only: it never orders.

It works on an in-memory copy of the database, so the live file is only read: not
migrated, not written (a session cookie the hoster reissues during the run is not kept;
the running bot keeps its own).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.catalog import HOURLY, Catalog, OrderRequest, Plan
from hostwatch.core.config import ConfigError, load_settings
from hostwatch.core.errors import describe
from hostwatch.core.http import make_client
from hostwatch.core.models import Money
from hostwatch.core.plugin import Plugin, SupportsOrder
from hostwatch.core.registry import Registry
from hostwatch.storage.repositories import AccountRepository, NotFound

DB_NAME = "hostwatch.sqlite3"
SHOWN = 5


class CatalogFailed(Exception):
    pass


def _memory_copy(path: Path) -> sqlite3.Connection:
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        conn = sqlite3.connect(":memory:")
        source.backup(conn)
    finally:
        source.close()
    conn.row_factory = sqlite3.Row
    return conn


async def read_catalog(registry: Registry, target: str) -> str:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise CatalogFailed(f"the database is unavailable:\n{exc}") from None
    path = settings.data_dir / DB_NAME
    if not path.is_file():
        raise CatalogFailed(f"no database at {path}")
    conn = _memory_copy(path)
    try:
        accounts = AccountRepository(conn, settings.encryption_key)
        account_id = _find(accounts, target)
        runner = AccountRunner(accounts, registry, make_client)

        async def catalog(plugin: Plugin) -> Catalog:
            if not isinstance(plugin, SupportsOrder):
                raise CatalogFailed("this plugin cannot order")
            return await plugin.catalog()

        try:
            found = await runner.call(account_id, catalog, track_status=False)
        except CatalogFailed:
            raise
        except Exception as exc:
            raise CatalogFailed(f"catalog: {describe(exc)}") from None
        lines = render(found)
        cheapest = _cheapest_monthly(found)
        if cheapest is not None:
            plan, price, months = cheapest
            os_id = found.systems_for(plan)[0].id
            request = OrderRequest(
                plan_id=plan.id,
                os_id=os_id,
                period_months=months,
                name="hw-check",
                price=price,
            )

            async def quote(plugin: Plugin) -> Money:
                assert isinstance(plugin, SupportsOrder)
                return await plugin.quote(request)

            try:
                now = await runner.call(account_id, quote, track_status=False)
                lines.append(f"Quote now for {plan.name}, {months} mo: {now} (catalog {price})")
            except Exception as exc:
                lines.append(f"Quote for {plan.name} failed: {describe(exc)}")
        return "\n".join(lines)
    finally:
        conn.close()


def render(catalog: Catalog) -> list[str]:
    lines = [
        f"Locations: {len(catalog.used_locations())}, plans: {len(catalog.plans)},"
        f" OS: {len(catalog.systems)}, SSH key at order: {'yes' if catalog.ssh_keys else 'no'},"
        f" tested live: {'yes' if catalog.tested else 'no'}",
    ]
    plans = sorted(catalog.plans, key=lambda p: (p.cheapest_month.amount, p.name))
    for plan in plans[:SHOWN]:
        location = catalog.location(plan.location_id)
        prices = ", ".join(
            f"{'hour' if p.period_months == HOURLY else str(p.period_months) + ' mo'} {p.amount}"
            for p in plan.prices
        )
        lines.append(
            f"  {plan.name} [{plan.id}] in {location.name if location else plan.location_id}:"
            f" {plan.specs}; {prices}; OS {len(catalog.systems_for(plan))}"
        )
    return lines


def _cheapest_monthly(catalog: Catalog) -> tuple[Plan, Money, int] | None:
    best: tuple[Plan, Money, int] | None = None
    for plan in catalog.plans:
        if not catalog.systems_for(plan):
            continue
        for price in plan.prices:
            months = price.period_months
            if months in (HOURLY, 1) and (best is None or price.amount < best[1]):
                best = (plan, price.amount, months)
    return best


def _find(accounts: AccountRepository, target: str) -> int:
    if target.isdigit():
        try:
            return accounts.get(int(target)).id
        except NotFound:
            pass
    found = accounts.find(target)
    if len(found) != 1:
        raise CatalogFailed(f"no single account named {target!r}")
    return found[0].id
