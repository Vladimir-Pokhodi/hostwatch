import asyncio
import io
import logging
import random
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.errors import AuthExpired, Blocked, HosterError, RateLimited
from hostwatch.core.logs import setup_logging
from hostwatch.core.models import AccountStatus
from hostwatch.core.plugin import Plugin
from hostwatch.core.registry import Registry
from hostwatch.plugins.fake import FakePlugin
from hostwatch.scheduler.polling import (
    BLOCKED_PAUSE,
    RETRY_DELAYS,
    STATUS_INTERVAL,
    STATUS_JITTER,
    EventKind,
    Poller,
    PollEvent,
    Schedule,
)
from hostwatch.storage.repositories import (
    AccountRepository,
    BalanceHistoryRepository,
    ServerRepository,
    UsageRepository,
)

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def make_runner(accounts: AccountRepository) -> AccountRunner:
    return AccountRunner(accounts, Registry({"fake": FakePlugin}), lambda _: httpx.AsyncClient())


class Harness:
    def __init__(self, conn: sqlite3.Connection, accounts: AccountRepository) -> None:
        self.accounts = accounts
        self.servers = ServerRepository(conn)
        self.usage = UsageRepository(conn)
        self.balances = BalanceHistoryRepository(conn)
        self.events: list[PollEvent] = []
        self.poller = Poller(
            make_runner(accounts),
            accounts,
            self.servers,
            self.usage,
            self.balances,
            self.notify,
            call_timeout=0.2,
        )

    async def notify(self, event: PollEvent) -> None:
        self.events.append(event)


@pytest.fixture
def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> Harness:
    return Harness(conn, accounts)


@pytest.mark.parametrize("fail", ["hoster_error", "crash", "auth_expired", "blocked"])
async def test_failing_account_does_not_stop_another(harness: Harness, fail: str) -> None:
    broken = harness.accounts.add("fake", "broken", {"fail": fail})
    healthy = harness.accounts.add("fake", "healthy", {})

    await asyncio.gather(harness.poller.poll_account(broken), harness.poller.poll_account(healthy))

    assert len(harness.servers.for_account(healthy)) == 3
    assert harness.usage.get(healthy, "1") is not None
    assert len(harness.balances.since(healthy, EPOCH)) == 1
    assert harness.accounts.get(healthy).status is AccountStatus.OK
    assert harness.servers.for_account(broken) == []
    assert harness.accounts.get(broken).status in (AccountStatus.ERROR, AccountStatus.RELOGIN)


async def test_hanging_account_times_out_without_blocking_another(harness: Harness) -> None:
    hanging = harness.accounts.add("fake", "hanging", {"sleep": "5"})
    healthy = harness.accounts.add("fake", "healthy", {})

    await asyncio.wait_for(
        asyncio.gather(harness.poller.poll_account(hanging), harness.poller.poll_account(healthy)),
        timeout=2,
    )

    assert harness.accounts.get(hanging).last_error == "timeout"
    assert harness.accounts.get(healthy).status is AccountStatus.OK


async def test_auth_expired_marks_relogin_and_notifies_once(harness: Harness) -> None:
    account = harness.accounts.add("fake", "a", {"fail": "auth_expired"})

    await harness.poller.poll_account(account)

    assert harness.accounts.get(account).status is AccountStatus.RELOGIN
    assert harness.poller.schedules[account].stopped
    assert harness.events == [PollEvent(account, EventKind.RELOGIN, "auth_expired")]


async def test_refresh_is_rate_limited(harness: Harness) -> None:
    account = harness.accounts.add("fake", "a", {})
    harness.poller.start_account(account)
    try:
        await asyncio.sleep(0.05)
        assert harness.poller.refresh(account)
        assert not harness.poller.refresh(account)
    finally:
        await harness.poller.stop()


async def test_loop_polls_on_start(harness: Harness) -> None:
    account = harness.accounts.add("fake", "a", {})
    harness.accounts.add("fake", "off", {})
    harness.accounts.set_status(2, AccountStatus.DISABLED)

    harness.poller.start()
    try:
        for _ in range(50):
            if harness.servers.for_account(account):
                break
            await asyncio.sleep(0.01)
    finally:
        await harness.poller.stop()

    assert len(harness.servers.for_account(account)) == 3
    assert 2 not in harness.poller.schedules


async def test_poll_done_resolves_after_a_failed_poll_too(harness: Harness) -> None:
    account = harness.accounts.add("fake", "a", {"fail": "crash"})
    done = harness.poller.poll_done(account)

    await harness.poller.poll_account(account)

    assert done.done()


async def test_refresh_is_refused_for_a_stopped_account(harness: Harness) -> None:
    account = harness.accounts.add("fake", "a", {})
    harness.poller.start_account(account)
    harness.poller.stop_account(account)

    assert not harness.poller.refresh(account)
    await harness.poller.stop()


def test_schedule_success_uses_interval_with_jitter() -> None:
    schedule = Schedule()

    schedule.succeeded(1000.0, with_balance=True, rng=random.Random(1))

    assert abs(schedule.next_poll - 1000.0 - STATUS_INTERVAL) <= STATUS_JITTER
    assert schedule.next_balance == 1000.0 + 3600


def test_schedule_retries_back_off_and_notify_after_three() -> None:
    schedule = Schedule()
    events = []
    delays = []
    for _ in range(5):
        events.append(schedule.failed(0.0, HosterError("x")))
        delays.append(schedule.next_poll)

    assert delays == [*RETRY_DELAYS, RETRY_DELAYS[-1]]
    assert events == [None, None, EventKind.FAILING, None, None]


@pytest.mark.parametrize("error", [Blocked(), RateLimited()])
def test_schedule_pauses_when_blocked(error: Exception) -> None:
    schedule = Schedule()

    assert schedule.failed(10.0, error) is EventKind.BLOCKED
    assert schedule.next_poll == 10.0 + BLOCKED_PAUSE
    assert schedule.paused


def test_schedule_stops_on_auth_expired() -> None:
    schedule = Schedule()

    assert schedule.failed(0.0, AuthExpired()) is EventKind.RELOGIN
    assert schedule.stopped


async def test_rotated_secrets_saved_even_if_call_fails_after(
    accounts: AccountRepository,
) -> None:
    account = accounts.add("fake", "a", {"token": "t0", "rotate": "1", "fail": "hoster_error"})
    runner = make_runner(accounts)

    with pytest.raises(HosterError):
        await runner.call(account, lambda plugin: plugin.servers())

    assert accounts.load_secrets(account)["token"] == "t0+"
    assert accounts.get(account).status is AccountStatus.ERROR
    assert accounts.get(account).last_error == "hoster_error: fake hoster says no"


async def test_rotation_chain_uses_latest_secrets(accounts: AccountRepository) -> None:
    account = accounts.add("fake", "a", {"token": "t0", "rotate": "1"})
    runner = make_runner(accounts)

    async def two_calls(plugin: Plugin) -> None:
        await plugin.servers()
        await plugin.balance()

    await runner.call(account, two_calls)
    await runner.call(account, lambda plugin: plugin.balance())

    assert accounts.load_secrets(account)["token"] == "t0+++"


async def test_calls_on_one_account_do_not_overlap(accounts: AccountRepository) -> None:
    account = accounts.add("fake", "a", {"sleep": "0.05"})
    runner = make_runner(accounts)
    active = 0
    peak = 0

    async def call(plugin: Plugin) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await plugin.balance()
        active -= 1

    await asyncio.gather(*(runner.call(account, call) for _ in range(3)))

    assert peak == 1


async def test_secret_never_reaches_logs(accounts: AccountRepository) -> None:
    secret = "s3cr3t-token-value"
    account = accounts.add("fake", "a", {"token": secret, "fail": "crash"})
    stream = io.StringIO()
    handler = setup_logging(logging.DEBUG, stream)
    log = logging.getLogger("test.leak")
    try:
        with pytest.raises(RuntimeError):
            await make_runner(accounts).call(account, lambda plugin: plugin.servers())
        log.warning("plugin answered with %s", secret)
        try:
            raise ValueError(f"bad token {secret}")
        except ValueError:
            log.exception("failure")
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()
    assert secret not in output
    assert "plugin answered with ***" in output
    assert "bad token ***" in output
