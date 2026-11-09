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
from hostwatch.core.history import balance_move, hoster_charges, server_changes
from hostwatch.core.models import AccountStatus, Balance, Invoice, Server, Usage
from hostwatch.core.plugin import Plugin, SupportsInvoices, SupportsUsage, SupportsUsageHistory
from hostwatch.storage.history import SAMPLES_KEPT, HistoryRepository
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
# Charge records of the hoster (for the money history) are read this often.
INVOICES_INTERVAL = 24 * 60 * MINUTE
# Past load is read for this many servers per poll, so a big account spreads it out.
BACKFILL_PER_POLL = 2
BACKFILL_TIMEOUT = 120.0


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
    next_invoices: float = 0.0
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
    # Read once a day for the money history; None when not read or not readable.
    invoices: list[Invoice] | None = None


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
        history: HistoryRepository | None = None,
    ) -> None:
        self._runner = runner
        self._history = history
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
        self._waiters: dict[int, list[asyncio.Future[None]]] = {}

    def start(self) -> None:
        for account in self._accounts.all():
            if account.status not in (AccountStatus.DISABLED, AccountStatus.RELOGIN):
                self.start_account(account.id)

    def start_account(self, account_id: int) -> None:
        """Start or restart polling, e.g. after a new key or a fresh login."""
        self.stop_account(account_id)
        try:
            if self._runner.plugin_of(account_id).login_only:
                return
        except LookupError:
            return
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

    @property
    def runner(self) -> AccountRunner:
        return self._runner

    async def read_balance(self, account_id: int) -> Balance:
        """Read the balance now and keep it in the history, e.g. after a top-up."""
        balance = await self._runner.call(account_id, lambda plugin: plugin.balance())
        self._keep_balance(account_id, balance, self._now())
        return balance

    def refresh(self, account_id: int) -> bool:
        """The «Refresh» button: poll now, at most once a minute. False if it was refused."""
        schedule = self.schedules.get(account_id)
        now = self._clock()
        if schedule is None or schedule.stopped or schedule.paused:
            return False
        if account_id not in self._tasks:
            return False
        if now - schedule.last_refresh < REFRESH_MIN_GAP:
            return False
        schedule.last_refresh = now
        schedule.next_poll = schedule.next_balance = now
        self._wake[account_id].set()
        return True

    def poll_done(self, account_id: int) -> asyncio.Future[None]:
        """Resolves when the next poll of the account ends, whatever its result.

        Take it before starting the poll: a fast poll may end before the caller awaits.
        """
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(account_id, []).append(waiter)
        return waiter

    async def poll_account(self, account_id: int) -> None:
        """One poll. Any error stays inside this account."""
        servers = None
        try:
            servers = await self._poll(account_id)
        finally:
            for waiter in self._waiters.pop(account_id, []):
                if not waiter.done():
                    waiter.set_result(None)
        if servers:
            # After the waiters: a «Refresh» does not wait for the past load.
            try:
                await self._backfill(account_id, servers)
            except Exception:
                log.exception("past load on account %s failed", account_id)

    async def _poll(self, account_id: int) -> list[Server] | None:
        """One poll; the servers read, or None when it failed."""
        schedule = self.schedules.setdefault(account_id, Schedule())
        started = self._clock()
        with_balance = started >= schedule.next_balance
        with_invoices = (
            with_balance and self._history is not None and started >= schedule.next_invoices
        )
        try:
            async with asyncio.timeout(self._call_timeout):
                snapshot = await self._runner.call(
                    account_id, lambda plugin: self._collect(plugin, with_balance, with_invoices)
                )
        except Exception as error:
            if isinstance(error, TimeoutError):
                self._accounts.set_status(account_id, AccountStatus.ERROR, describe(error))
            event = schedule.failed(self._clock(), error)
            log.warning("poll of account %s failed: %s", account_id, describe(error))
            if event is not None:
                await self._send(PollEvent(account_id, event, describe(error)))
            return None
        self._store(account_id, snapshot)
        schedule.succeeded(self._clock(), with_balance, self._rng)
        if with_invoices:
            schedule.next_invoices = self._clock() + INVOICES_INTERVAL
        return snapshot.servers

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
    async def _collect(plugin: Plugin, with_balance: bool, with_invoices: bool = False) -> Snapshot:
        servers = await plugin.servers()
        usage = []
        if isinstance(plugin, SupportsUsage):
            usage = [await plugin.usage(server.external_id) for server in servers]
        balance = await plugin.balance() if with_balance else None
        invoices = None
        if with_invoices and isinstance(plugin, SupportsInvoices):
            try:
                invoices = await plugin.invoices()
            except Exception as error:
                # Only the money history needs them: the poll itself went fine.
                log.info("charge records not read: %s", describe(error))
        return Snapshot(servers, usage, balance, invoices)

    def _store(self, account_id: int, snapshot: Snapshot) -> None:
        at = self._now()
        if self._history is not None:
            old = self._servers.for_account(account_id)
            changes = server_changes(old, snapshot.servers)
            self._history.add_server_events(account_id, changes, at)
        self._servers.replace(account_id, snapshot.servers, at)
        for item in snapshot.usage:
            self._usage.put(account_id, item)
        if self._history is not None:
            self._history.add_samples(account_id, snapshot.usage)
        if snapshot.balance is not None:
            self._keep_balance(account_id, snapshot.balance, at)
        if self._history is not None and snapshot.invoices is not None:
            self._history.add_money(account_id, hoster_charges(snapshot.invoices, at))

    def _keep_balance(self, account_id: int, balance: Balance, at: datetime) -> None:
        """The balance history gets a row on a change; the change is a money event too."""
        latest = self._balances.latest(account_id)
        self._balances.add(account_id, balance.money, at, balance.hours_left)
        move = balance_move(None if latest is None else latest[1], balance, at)
        if self._history is not None and move is not None:
            self._history.add_money(account_id, [move])

    async def _backfill(self, account_id: int, servers: list[Server]) -> None:
        """Read past load from the hoster once per server, a few servers per poll.

        A rate limit or a timeout leaves the server for the next poll; another error
        counts as an attempt, and after a few the server is left alone.
        """
        history = self._history
        if history is None or not servers:
            return
        try:
            plugin_cls = self._runner.plugin_of(account_id)
        except LookupError:
            return
        if not callable(getattr(plugin_cls, "usage_history", None)):
            return
        wanted = history.needs_backfill(account_id, [s.external_id for s in servers])
        since = self._now() - SAMPLES_KEPT
        for server_id in wanted[:BACKFILL_PER_POLL]:

            async def read(plugin: Plugin, sid: str = server_id) -> list[Usage]:
                if not isinstance(plugin, SupportsUsageHistory):
                    return []
                return await plugin.usage_history(sid, since)

            try:
                async with asyncio.timeout(BACKFILL_TIMEOUT):
                    points = await self._runner.call(account_id, read, track_status=False)
            except (RateLimited, Blocked, TimeoutError) as error:
                log.info("past load on account %s left for later: %s", account_id, describe(error))
                return
            except Exception as error:
                log.warning("past load on account %s not read: %s", account_id, describe(error))
                history.backfill_done(account_id, server_id, self._now(), None)
                continue
            added = history.add_samples(account_id, points)
            history.backfill_done(account_id, server_id, self._now(), added)

    async def _send(self, event: PollEvent) -> None:
        try:
            await self._notify(event)
        except Exception:
            log.exception("could not deliver event %s for account %s", event.kind, event.account_id)
