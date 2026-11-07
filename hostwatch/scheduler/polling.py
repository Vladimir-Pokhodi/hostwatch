"""Polls every account in its own asyncio task (spec, decision 7)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.errors import AuthExpired, Blocked, RateLimited, describe
from hostwatch.core.models import AccountStatus, Balance, Server, Usage
from hostwatch.core.plugin import Plugin, SupportsUsage
from hostwatch.storage.repositories import (
    AccountRepository,
    BalanceHistoryRepository,
    ServerRepository,
    UsageRepository,
)

log = logging.getLogger(__name__)

MINUTE = 60.0
STATUS_INTERVAL = 15 * MINUTE
STATUS_JITTER = 2 * MINUTE
BALANCE_INTERVAL = 60 * MINUTE
RETRY_DELAYS = (15 * MINUTE, 30 * MINUTE, 60 * MINUTE, 120 * MINUTE)
FAILURES_BEFORE_NOTICE = 3
BLOCKED_PAUSE = 120 * MINUTE
CALL_TIMEOUT = 60.0
REFRESH_MIN_GAP = MINUTE


class EventKind(StrEnum):
    RELOGIN = "relogin"
    BLOCKED = "blocked"
    FAILING = "failing"


@dataclass(frozen=True)
class PollEvent:
    account_id: int
    kind: EventKind
    detail: str


@dataclass
class Schedule:
    """When an account is polled next. Times come from a monotonic clock."""

    next_poll: float = 0.0
    next_balance: float = 0.0
    failures: int = 0
    stopped: bool = False
    paused: bool = False
    last_refresh: float = field(default=float("-inf"))

    def succeeded(self, now: float, with_balance: bool, rng: random.Random) -> None:
        self.failures = 0
        self.paused = False
        self.next_poll = now + STATUS_INTERVAL + rng.uniform(-STATUS_JITTER, STATUS_JITTER)
        if with_balance:
            self.next_balance = now + BALANCE_INTERVAL

    def failed(self, now: float, error: BaseException) -> EventKind | None:
        """Plan the retry and say which event, if any, the owner should get."""
        if isinstance(error, AuthExpired):
            self.stopped = True
            return EventKind.RELOGIN
        if isinstance(error, Blocked | RateLimited):
            self.paused = True
            self.next_poll = now + BLOCKED_PAUSE
            return EventKind.BLOCKED
        self.failures += 1
        self.next_poll = now + RETRY_DELAYS[min(self.failures, len(RETRY_DELAYS)) - 1]
        return EventKind.FAILING if self.failures == FAILURES_BEFORE_NOTICE else None


@dataclass(frozen=True)
class Snapshot:
    servers: list[Server]
    usage: list[Usage]
    balance: Balance | None


class Poller:
    def __init__(
        self,
        runner: AccountRunner,
        accounts: AccountRepository,
        servers: ServerRepository,
        usage: UsageRepository,
        balances: BalanceHistoryRepository,
        notify: Callable[[PollEvent], Awaitable[None]],
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
        call_timeout: float = CALL_TIMEOUT,
    ) -> None:
        self._runner = runner
        self._accounts = accounts
        self._servers = servers
        self._usage = usage
        self._balances = balances
        self._notify = notify
        self._clock = clock
        self._now = now
        self._rng = rng or random.Random()
        self._call_timeout = call_timeout
        self.schedules: dict[int, Schedule] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._wake: dict[int, asyncio.Event] = {}

    def start(self) -> None:
        for account in self._accounts.all():
            if account.status not in (AccountStatus.DISABLED, AccountStatus.RELOGIN):
                self.start_account(account.id)

    def start_account(self, account_id: int) -> None:
        """Start or restart polling, e.g. after a new key or a fresh login."""
        self.stop_account(account_id)
        self.schedules[account_id] = Schedule()
        self._wake[account_id] = asyncio.Event()
        self._tasks[account_id] = asyncio.create_task(
            self._loop(account_id), name=f"poll-account-{account_id}"
        )

    def stop_account(self, account_id: int) -> None:
        task = self._tasks.pop(account_id, None)
        if task is not None:
            task.cancel()

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def refresh(self, account_id: int) -> bool:
        """The «Refresh» button: poll now, at most once a minute. False if it was refused."""
        schedule = self.schedules.get(account_id)
        now = self._clock()
        if schedule is None or schedule.stopped or schedule.paused:
            return False
        if now - schedule.last_refresh < REFRESH_MIN_GAP:
            return False
        schedule.last_refresh = now
        schedule.next_poll = schedule.next_balance = now
        self._wake[account_id].set()
        return True

    async def poll_account(self, account_id: int) -> None:
        """One poll. Any error stays inside this account."""
        schedule = self.schedules.setdefault(account_id, Schedule())
        started = self._clock()
        with_balance = started >= schedule.next_balance
        try:
            async with asyncio.timeout(self._call_timeout):
                snapshot = await self._runner.call(
                    account_id, lambda plugin: self._collect(plugin, with_balance)
                )
        except Exception as error:
            if isinstance(error, TimeoutError):
                self._accounts.set_status(account_id, AccountStatus.ERROR, describe(error))
            event = schedule.failed(self._clock(), error)
            log.warning("poll of account %s failed: %s", account_id, describe(error))
            if event is not None:
                await self._send(PollEvent(account_id, event, describe(error)))
            return
        self._store(account_id, snapshot)
        schedule.succeeded(self._clock(), with_balance, self._rng)

    async def _loop(self, account_id: int) -> None:
        schedule = self.schedules[account_id]
        wake = self._wake[account_id]
        while not schedule.stopped:
            delay = schedule.next_poll - self._clock()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), delay)
                wake.clear()
                continue
            await self.poll_account(account_id)

    @staticmethod
    async def _collect(plugin: Plugin, with_balance: bool) -> Snapshot:
        servers = await plugin.servers()
        usage = []
        if isinstance(plugin, SupportsUsage):
            usage = [await plugin.usage(server.external_id) for server in servers]
        balance = await plugin.balance() if with_balance else None
        return Snapshot(servers, usage, balance)

    def _store(self, account_id: int, snapshot: Snapshot) -> None:
        at = self._now()
        self._servers.replace(account_id, snapshot.servers, at)
        for item in snapshot.usage:
            self._usage.put(account_id, item)
        if snapshot.balance is not None:
            self._balances.add(account_id, snapshot.balance.money, at)

    async def _send(self, event: PollEvent) -> None:
        try:
            await self._notify(event)
        except Exception:
            log.exception("could not deliver event %s for account %s", event.kind, event.account_id)
