"""Server and money actions in the bot: confirmation, one-time nonce, journal (stage 10)."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator

import pytest
import respx
from aiogram.methods import DeleteMessage, SendMessage

from hostwatch.bot.callbacks import (
    ActionCb,
    ConfirmCb,
    InvoicesCb,
    Nav,
    PayCb,
)
from hostwatch.core.models import Autorenew, ServerStatus
from hostwatch.plugins.aeza import BASE_URL, AezaPlugin
from hostwatch.plugins.fake import FakePlugin
from hostwatch.storage.repositories import AccountRepository
from tests.test_bot import Harness, _store_from_fixtures


@pytest.fixture
async def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> AsyncIterator[Harness]:
    FakePlugin.calls.clear()
    h = Harness(conn, accounts)
    yield h
    await h.poller.stop()
    await h.bot.session.close()


async def _account(h: Harness, **secrets: str) -> int:
    h.onboard("ru")
    acc = h.hub.accounts.add("fake", "main", dict(secrets))
    await h.poller.poll_account(acc)
    return acc


def _confirm(h: Harness) -> tuple[ConfirmCb, ConfirmCb]:
    """«Yes» and «Cancel» under the last screen."""
    data = [ConfirmCb.unpack(b) for b in h.session.buttons() if b.startswith("cf:")]
    yes = next(d for d in data if d.yes)
    no = next(d for d in data if not d.yes)
    return yes, no


async def test_action_asks_first_and_calls_nothing(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(ActionCb(acc=acc, sid="1", act="stop"))

    text = harness.session.last_text()
    assert text.startswith("<b>❓ Выключить сервер?</b>")
    assert "🖥 beaver · Fake hoster (main) · 192.0.2.11" in text
    assert "💰 Без списания" in text
    assert FakePlugin.calls == []
    assert harness.hub.journal.recent() == []


async def test_yes_runs_once_and_a_second_press_is_ignored(harness: Harness) -> None:
    acc = await _account(harness)
    await harness.press(ActionCb(acc=acc, sid="1", act="stop"))
    yes, _ = _confirm(harness)

    await harness.press(yes)
    await harness.press(yes)

    assert FakePlugin.calls == ["power 1 stop"]
    assert "✅ Готово</b>\nвыключить сервер · beaver" in harness.session.texts()[-1]
    assert harness.alerts()[-1] == "Кнопка устарела или уже нажата. Ничего не сделал."
    entries = harness.hub.journal.recent()
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.action, entry.ok, entry.actor) == ("stop", True, "owner")
    assert entry.params == {"action": "stop", "server": "1"}
    assert entry.response == "server 1: stop"


async def test_cancel_does_nothing_and_kills_the_nonce(harness: Harness) -> None:
    acc = await _account(harness)
    await harness.press(ActionCb(acc=acc, sid="1", act="restart"))
    yes, no = _confirm(harness)

    await harness.press(no)
    await harness.press(yes)

    assert FakePlugin.calls == []
    assert "<b>🟢 beaver · Fake hoster (main)</b>" in harness.session.texts()[-1]
    assert harness.hub.journal.recent() == []


async def test_expired_confirmation_does_nothing(harness: Harness) -> None:
    acc = await _account(harness)
    clock = [0.0]
    harness.hub.confirmations.clock = lambda: clock[0]
    await harness.press(ActionCb(acc=acc, sid="1", act="stop"))
    yes, _ = _confirm(harness)

    clock[0] += 301
    await harness.press(yes)

    assert FakePlugin.calls == []
    assert harness.alerts()[-1] == "Кнопка устарела или уже нажата. Ничего не сделал."


async def test_button_from_an_old_screen_is_refused(harness: Harness) -> None:
    acc = await _account(harness)

    # beaver runs: there is no «Start» on its card any more.
    await harness.press(ActionCb(acc=acc, sid="1", act="start"))

    assert harness.alerts()[-1].startswith("Это действие сейчас недоступно")
    assert not [b for b in harness.session.buttons() if b.startswith("cf:")]
    assert FakePlugin.calls == []


async def test_own_stop_is_marked_and_start_clears_it(harness: Harness) -> None:
    acc = await _account(harness)
    await harness.press(ActionCb(acc=acc, sid="1", act="stop"))
    await harness.press(_confirm(harness)[0])
    assert set(harness.hub.owner_stops.for_account(acc)) == {"1"}

    views = {v.account.id: v for v in harness.hub.alert_views()}
    assert views[acc].owner_stopped == frozenset({"1"})

    await harness.press(ActionCb(acc=acc, sid="3", act="start"))
    await harness.press(_confirm(harness)[0])
    assert harness.hub.owner_stops.for_account(acc) == {"1": harness.hub.now()}


async def test_renewal_shows_the_price_and_tells_about_money(harness: Harness) -> None:
    acc = await _account(harness, renew_price="4.50")

    await harness.press(ActionCb(acc=acc, sid="1", act="renew"))
    text = harness.session.last_text()
    assert text.startswith("<b>❓ Продлить на один период?</b>")
    assert "💰 Спишется €4.50 · на балансе €20.00" in text
    assert FakePlugin.calls == []

    await harness.press(_confirm(harness)[0])

    assert FakePlugin.calls == ["renew 1 4.50"]
    notices = [
        c.text
        for c in harness.session.calls
        if isinstance(c, SendMessage) and "Действие с деньгами" in (c.text or "")
    ]
    assert len(notices) == 1 and "Готово</b>\nпродлить" in notices[0]
    entry = harness.hub.journal.recent()[0]
    assert (entry.action, entry.ok) == ("renew", True)
    assert entry.params["amount"] == "4.50"


async def test_failed_money_action_is_journalled_and_told(harness: Harness) -> None:
    acc = await _account(harness, renew_price="4.50")
    await harness.press(ActionCb(acc=acc, sid="1", act="renew"))
    yes, _ = _confirm(harness)
    # The price went up after the owner saw it: the plugin refuses.
    harness.hub.accounts.save_secrets(acc, {"renew_price": "5.00"})

    await harness.press(yes)

    entry = harness.hub.journal.recent()[0]
    assert (entry.action, entry.ok) == ("renew", False)
    assert "price has changed" in entry.response
    texts = [c.text or "" for c in harness.session.calls if isinstance(c, SendMessage)]
    assert any("Действие с деньгами" in t and "Не получилось" in t for t in texts)


async def test_plain_action_sends_no_money_notice(harness: Harness) -> None:
    acc = await _account(harness)
    await harness.press(ActionCb(acc=acc, sid="1", act="autorenew_off"))
    assert "💰 Без списания" in harness.session.last_text()
    await harness.press(_confirm(harness)[0])

    assert FakePlugin.calls == ["autorenew 1 off"]
    assert not any(isinstance(c, SendMessage) for c in harness.session.calls)


async def test_autorenew_on_names_the_coming_charges(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.servers.replace(
        acc,
        [
            s.model_copy(update={"autorenew": Autorenew.OFF}) if s.external_id == "1" else s
            for s in harness.hub.servers.for_account(acc)
        ],
        harness.hub.now(),
    )
    await harness.press(ActionCb(acc=acc, sid="1", act="autorenew_on"))
    assert (
        "Сейчас без списания, дальше €4.50/мес за каждое продление" in harness.session.last_text()
    )


async def test_hoster_error_is_shown_and_journalled(harness: Harness) -> None:
    acc = await _account(harness)
    await harness.press(ActionCb(acc=acc, sid="1", act="stop"))
    yes, _ = _confirm(harness)
    harness.hub.accounts.save_secrets(acc, {"fail": "hoster_error"})

    await harness.press(yes)

    assert "Причина: хостер отказал: fake hoster says no" in harness.session.texts()[-1]
    entry = harness.hub.journal.recent()[0]
    assert not entry.ok and entry.response.endswith("fake hoster says no")
    assert harness.hub.owner_stops.for_account(acc) == {}


async def test_pay_invoice_from_the_balance_screen(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(Nav(to="balance"))
    assert InvoicesCb(acc=acc).pack() in harness.session.buttons()

    await harness.press(InvoicesCb(acc=acc))
    text = harness.session.last_text()
    assert "#100 · €4.50 · 🖥 beaver" in text
    assert PayCb(acc=acc, inv="100").pack() in harness.session.buttons()

    await harness.press(PayCb(acc=acc, inv="100"))
    text = harness.session.last_text()
    assert text.startswith("<b>❓ Оплатить счёт #100 с баланса?</b>")
    assert "💰 Спишется €4.50" in text
    assert FakePlugin.calls == []

    await harness.press(_confirm(harness)[0])

    assert FakePlugin.calls == ["pay_invoice 100 4.50"]
    entry = harness.hub.journal.recent()[0]
    assert entry.params == {
        "action": "pay_invoice",
        "server": "1",
        "invoice": "100",
        "amount": "4.50",
        "currency": "EUR",
    }
    notice, result = harness.session.texts()[-2:]
    assert "Действие с деньгами" in notice and "оплатить счёт #100" in notice
    assert isinstance(harness.session.calls[-1], DeleteMessage)  # the menu went below it
    assert "Действие с деньгами" not in result


async def test_journal_names_the_actions(harness: Harness) -> None:
    acc = await _account(harness, renew_price="4.50")
    await harness.press(ActionCb(acc=acc, sid="1", act="renew"))
    await harness.press(_confirm(harness)[0])

    await harness.press(Nav(to="journal"))

    assert "✅ 07.11 15:00 · Fake hoster (main) · продление €4.50" in harness.session.last_text()


async def test_journal_keeps_no_secrets(harness: Harness) -> None:
    """Aeza echoing the key back must not put it into the journal."""
    key = "k" * 32
    acc = await _store_from_fixtures(harness, AezaPlugin, {"api_key": key})
    harness.onboard("ru")
    server = next(
        s for s in harness.hub.servers.for_account(acc) if s.status is ServerStatus.RUNNING
    )
    await harness.press(ActionCb(acc=acc, sid=server.external_id, act="restart"))
    yes, _ = _confirm(harness)

    with respx.mock:
        respx.post(f"{BASE_URL}services/{server.external_id}/ctl/restart").respond(
            200, json={"ok": True, "echo": key}
        )
        await harness.press(yes)

    entry = harness.hub.journal.recent()[0]
    assert entry.ok
    assert key not in entry.response and "***" in entry.response
    assert all(key not in t for t in harness.session.texts())
