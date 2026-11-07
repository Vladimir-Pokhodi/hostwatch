"""Runs plugin calls for an account: one call at a time, secrets saved as soon as they rotate."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import httpx

from hostwatch.core.errors import AuthExpired, describe
from hostwatch.core.logs import register_secrets
from hostwatch.core.models import AccountStatus
from hostwatch.core.plugin import Plugin, PluginContext, Secrets
from hostwatch.core.registry import Registry
from hostwatch.storage.repositories import AccountRepository

log = logging.getLogger(__name__)

T = TypeVar("T")


def status_for(error: BaseException) -> AccountStatus:
    return AccountStatus.RELOGIN if isinstance(error, AuthExpired) else AccountStatus.ERROR


class AccountRunner:
    def __init__(
        self,
        accounts: AccountRepository,
        registry: Registry,
        make_http: Callable[[str], httpx.AsyncClient],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._accounts = accounts
        self._registry = registry
        self._make_http = make_http
        self._now = now
        # Two calls must not refresh one token at the same time: the hoster keeps only one.
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def plugin_of(self, account_id: int) -> type[Plugin]:
        return self._registry.get(self._accounts.get(account_id).plugin_id)

    async def call(self, account_id: int, action: Callable[[Plugin], Awaitable[T]]) -> T:
        async with self._locks[account_id]:
            account = self._accounts.get(account_id)
            plugin_cls = self._registry.get(account.plugin_id)
            secrets = self._accounts.load_secrets(account_id)
            register_secrets(secrets.values())

            async def save(new: Secrets) -> None:
                register_secrets(new.values())
                self._accounts.save_secrets(account_id, new)

            try:
                async with self._make_http(account.plugin_id) as http:
                    plugin = plugin_cls(PluginContext(secrets, http, save))
                    result = await action(plugin)
            except Exception as error:
                self._accounts.set_status(account_id, status_for(error), describe(error))
                log.warning("account %s (%s): %s", account_id, account.plugin_id, describe(error))
                raise
            self._accounts.mark_ok(account_id, self._now())
            return result
