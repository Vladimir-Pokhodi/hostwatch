"""Confirmations, own stops and the dispatch of confirmed actions (stage 10)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import httpx
import pytest

from hostwatch.core.alerts import Reason
from hostwatch.core.control import (
    OWNER_STOP_GRACE,
    Confirmations,
    Control,
    Request,
    finished_stops,
    journal_text,
    perform,
)
from hostwatch.core.errors import HosterError
from hostwatch.core.logs import register_secrets
from hostwatch.core.models import Money, ServerStatus
from hostwatch.core.plugin import PluginContext, Secrets
from hostwatch.plugins.fake import FakePlugin
from hostwatch.storage.repositories import AccountRepository, OwnerStopRepository
from tests.test_alerts import NOON, Book, server, view


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_nonce_is_good_once() -> None:
    confirmations: Confirmations[str] = Confirmations()
    nonce = confirmations.issue("stop beaver")
    assert confirmations.take(nonce) == "stop beaver"
    assert confirmations.take(nonce) is None


def test_nonce_lives_five_minutes() -> None:
    clock = Clock()
    confirmations: Confirmations[str] = Confirmations(clock=clock)
    fresh = confirmations.issue("a")
    late = confirmations.issue("b")
    clock.now += 299
    assert confirmations.take(fresh) == "a"
    clock.now += 2
    assert confirmations.take(late) is None


def test_unknown_nonce_and_expired_ones_are_forgotten() -> None:
    clock = Clock()
    confirmations: Confirmations[str] = Confirmations(clock=clock)
    assert confirmations.take("made-up") is None
    old = confirmations.issue("old")
    clock.now += 400
    confirmations.issue("new")
    assert old not in confirmations._pending


def test_nonces_differ_and_fit_callback_data() -> None:
    confirmations: Confirmations[int] = Confirmations()
    nonces = {confirmations.issue(i) for i in range(100)}
    assert len(nonces) == 100
    assert all(len(n) <= 16 for n in nonces)


def test_request_params_for_the_journal() -> None:
    request = Request(Control.RENEW, 3, server_id="7", amount=Money.of("4.50", "EUR"))
    assert request.params() == {
        "action": "renew",
        "server": "7",
        "amount": "4.50",
        "currency": "EUR",
    }
    assert Control.RENEW.spends_money and Control.PAY_INVOICE.spends_money
    assert not Control.STOP.spends_money
    assert Control.STOP.stops_server and Control.RESTART.stops_server
    assert not Control.START.stops_server


def test_journal_text_masks_secrets_and_is_short() -> None:
    register_secrets(["very-secret-session-cookie"])
    text = journal_text("echo very-secret-session-cookie " + "x" * 1000)
    assert "very-secret-session-cookie" not in text
    assert "***" in text
    assert len(text) == 500


async def _fake(secrets: Secrets | None = None) -> FakePlugin:
    async def keep(_: Secrets) -> None:
        pass

    return FakePlugin(PluginContext(secrets or {}, httpx.AsyncClient(), keep))


async def test_perform_calls_the_plugin_method() -> None:
    FakePlugin.calls.clear()
    plugin = await _fake()
    eur = Money.of("4.50", "EUR")
    for request in (
        Request(Control.STOP, 1, server_id="1"),
        Request(Control.AUTORENEW_OFF, 1, server_id="1"),
        Request(Control.RENEW, 1, server_id="1", amount=eur),
        Request(Control.PAY_INVOICE, 1, invoice_id="100", amount=eur),
    ):
        result = await perform(plugin, request)
        assert result.ok
    assert FakePlugin.calls == [
        "power 1 stop",
        "autorenew 1 off",
        "renew 1 4.50",
        "pay_invoice 100 4.50",
    ]


async def test_money_action_without_a_confirmed_sum_is_refused() -> None:
    FakePlugin.calls.clear()
    plugin = await _fake()
    with pytest.raises(HosterError):
        await perform(plugin, Request(Control.RENEW, 1, server_id="1"))
    assert FakePlugin.calls == []


async def test_action_the_plugin_lacks_is_refused() -> None:
    class ReadOnly(FakePlugin):
        power = None  # type: ignore[assignment]

    async def keep(_: Secrets) -> None:
        pass

    plugin = ReadOnly(PluginContext({}, httpx.AsyncClient(), keep))
    with pytest.raises(HosterError):
        await perform(plugin, Request(Control.STOP, 1, server_id="1"))


def test_own_stop_mark_ends_when_the_server_runs_again() -> None:
    at = NOON
    marks = {"s1": at, "s2": at, "gone": at}
    servers = [server("s1", ServerStatus.STOPPED), server("s2")]
    # Right after «Stop» the hoster may still say running: the mark stays.
    assert finished_stops(marks, servers, at + timedelta(minutes=2)) == ["gone"]
    later = at + OWNER_STOP_GRACE + timedelta(minutes=1)
    assert sorted(finished_stops(marks, servers, later)) == ["gone", "s2"]


def test_own_stop_does_not_alert_but_suspension_does() -> None:
    book = Book()
    stopped = [server("s1", ServerStatus.STOPPED)]
    assert book.tick(NOON, view(servers=stopped, owner_stopped=frozenset({"s1"}))) == []
    suspended = [server("s1", ServerStatus.SUSPENDED)]
    messages = book.tick(NOON, view(servers=suspended, owner_stopped=frozenset({"s1"})))
    assert [reason for reason, _ in messages] == [Reason.NEW]


def test_stop_without_the_mark_alerts() -> None:
    book = Book()
    messages = book.tick(NOON, view(servers=[server("s1", ServerStatus.STOPPED)]))
    assert [reason for reason, _ in messages] == [Reason.NEW]


def test_owner_stop_repository(conn: sqlite3.Connection, accounts: AccountRepository) -> None:
    acc = accounts.add("fake", "main", {})
    stops = OwnerStopRepository(conn)
    stops.mark(acc, "1", NOON)
    stops.mark(acc, "2", NOON)
    stops.clear(acc, "2")
    assert stops.for_account(acc) == {"1": NOON}
    accounts.delete(acc)
    assert stops.for_account(acc) == {}
