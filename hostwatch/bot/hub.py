"""Everything the handlers need, and the data they show, read from the database."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from hostwatch.core.alerts import AccountView
from hostwatch.core.control import Confirmations, Request, finished_stops
from hostwatch.core.forecast import RECONCILE_WINDOW, Forecast, forecast
from hostwatch.core.models import Account, Balance, PayOption, Server, Usage
from hostwatch.core.order import CatalogCache, Draft
from hostwatch.core.plugin import ApiKeyAuth, Capability, Plugin, capabilities
from hostwatch.core.rates import Rates
from hostwatch.core.registry import Registry
from hostwatch.scheduler.autopay import AutopayJob
from hostwatch.scheduler.maintenance import Backups
from hostwatch.scheduler.polling import Poller
from hostwatch.storage.autopay import AutopayStore
from hostwatch.storage.repositories import (
    AccountRepository,
    BalanceHistoryRepository,
    JournalRepository,
    OwnerStopRepository,
    PaymentRepository,
    ServerRepository,
    UsageRepository,
)

from .prefs import Prefs

if TYPE_CHECKING:
    from .login import BotLogin

# The fake plugin is for tests and demos; the owner does not see it unless asked to.
HIDDEN_PLUGINS = frozenset({"fake"})


class RatesSource(Protocol):
    async def get(self) -> Rates: ...


@dataclass(frozen=True)
class AccountData:
    account: Account
    plugin: type[Plugin]
    servers: list[Server]
    # When the server list was read; None if it never was.
    servers_at: datetime | None
    balance: Balance | None
    balance_at: datetime | None
    forecast: Forecast | None
    # Servers the owner marked for the bot's autopay (stage 11).
    autopay: frozenset[str] = frozenset()

    @property
    def caps(self) -> set[Capability]:
        return capabilities(self.plugin)

    @property
    def title(self) -> str:
        name = self.plugin.meta.name
        return name if self.account.label in ("", name) else f"{name} ({self.account.label})"


@dataclass
class Hub:
    registry: Registry
    accounts: AccountRepository
    servers: ServerRepository
    usage: UsageRepository
    balances: BalanceHistoryRepository
    journal: JournalRepository
    prefs: Prefs
    poller: Poller
    rates: RatesSource
    payments: PaymentRepository
    owner_stops: OwnerStopRepository
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    hidden_plugins: frozenset[str] = HIDDEN_PLUGINS
    # Public IP of the server the bot runs on, once known (spec, decision 17).
    own_ip: str | None = None
    backups: Backups | None = None
    # Browser login (stage 06); None when the login page is not running.
    login: BotLogin | None = None
    # After «Paid» the balance is read this many seconds later.
    paid_checks: tuple[float, ...] = (120.0, 300.0, 600.0)
    background: set[asyncio.Task[None]] = field(default_factory=set)
    # «Yes» buttons of server and money actions waiting for the owner (stage 10).
    confirmations: Confirmations[Request] = field(default_factory=Confirmations)
    # Autopay marks and rule (stage 11); None: autopay is off and nothing is marked.
    autopay: AutopayStore | None = None
    autopay_job: AutopayJob | None = None
    # Orders (stage 13): catalogs read within the hour, and orders waiting for «Yes».
    catalogs: CatalogCache = field(default_factory=CatalogCache)
    order_confirmations: Confirmations[Draft] = field(default_factory=Confirmations)
    # Coins of recent top-up invoices that are chosen after the invoice, by `payments` row.
    # Lost on restart: the plugin reads them again.
    topup_options: dict[int, tuple[PayOption, ...]] = field(default_factory=dict)

    def plugins(self) -> list[type[Plugin]]:
        ids = [pid for pid in self.registry.ids() if pid not in self.hidden_plugins]
        return sorted((self.registry.get(pid) for pid in ids), key=lambda p: p.meta.name)

    def account_titles(self) -> dict[int, str]:
        return {data.account.id: data.title for data in self.load_all()}

    def load_all(self) -> list[AccountData]:
        return [
            self.load(account.id)
            for account in self.accounts.all()
            if account.plugin_id in self.registry
        ]

    def load(self, account_id: int) -> AccountData:
        account = self.accounts.get(account_id)
        plugin = self.registry.get(account.plugin_id)
        servers = self.servers.for_account(account_id)
        latest = self.balances.latest(account_id)
        balance_at, balance = latest if latest is not None else (None, None)
        result_forecast = None
        autopay = self.autopay.marked(account_id) if self.autopay is not None else frozenset()
        if balance is not None and balance_at is not None:
            history = self.balances.since(account_id, balance_at - RECONCILE_WINDOW)
            result_forecast = forecast(balance, servers, balance_at, history, autopay)
        return AccountData(
            account=account,
            plugin=plugin,
            servers=servers,
            servers_at=self.servers.updated_at(account_id),
            balance=balance,
            balance_at=balance_at,
            forecast=result_forecast,
            autopay=autopay,
        )

    def usage_of(self, account_id: int, server_id: str) -> Usage | None:
        return self.usage.get(account_id, server_id)

    def is_own(self, server: Server) -> bool:
        return self.own_ip is not None and server.ip == self.own_ip

    def alert_views(self) -> list[AccountView]:
        """Accounts as the alert rules see them."""
        views = []
        for data in self.load_all():
            acc = data.account.id
            usage = {
                s.external_id: found
                for s in data.servers
                if (found := self.usage_of(acc, s.external_id)) is not None
            }
            schedule = self.poller.schedules.get(acc)
            views.append(
                AccountView(
                    account=data.account,
                    title=data.title,
                    servers=data.servers,
                    forecast=data.forecast,
                    usage=usage,
                    failures=0 if schedule is None else schedule.failures,
                    owner_stopped=self._owner_stopped(acc, data.servers),
                    autopay=data.autopay,
                )
            )
        return views

    def _owner_stopped(self, account_id: int, servers: list[Server]) -> frozenset[str]:
        """Servers still marked «stopped by the owner»; marks that did their job go away."""
        marks = self.owner_stops.for_account(account_id)
        for server_id in finished_stops(marks, servers, self.now()):
            self.owner_stops.clear(account_id, server_id)
            del marks[server_id]
        return frozenset(marks)


def key_fields(plugin: type[Plugin]) -> tuple[str, ...]:
    """Secret fields the owner types for an API key login; empty if the plugin has none."""
    for auth in plugin.meta.auth:
        if isinstance(auth, ApiKeyAuth):
            return auth.fields
    return ()
