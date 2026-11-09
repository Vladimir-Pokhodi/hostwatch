"""The one live menu in the chat (plan v1.1, part 1): replaced at the bottom, edited in place,
old menus closed, the owner's messages deleted, notification buttons open a menu below."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import ReplyKeyboardMarkup

from hostwatch.bot.app import alert_markup
from hostwatch.bot.callbacks import (
    AccountCb,
    Nav,
    PaidCb,
    ServerCb,
    SetCb,
    TopupAmountCb,
    TopupCb,
    note,
)
from hostwatch.bot.views import View, alert_buttons
from hostwatch.storage.repositories import AccountRepository
from tests.test_bot import Harness

STALE = "Это меню закрыто, актуальное внизу"


@pytest.fixture
async def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> AsyncIterator[Harness]:
    h = Harness(conn, accounts)
    yield h
    for task in [*h.hub.background, *h.hub.timers]:
        task.cancel()
    await h.poller.stop()
    await h.bot.session.close()


def _of(h: Harness, kind: type[TelegramMethod[Any]]) -> list[Any]:
    return [c for c in h.session.calls if isinstance(c, kind)]


def _since(h: Harness, start: int) -> list[TelegramMethod[Any]]:
    return h.session.calls[start:]


async def _start(h: Harness) -> int:
    await h.send("/start")
    menu_id = h.hub.prefs.menu_id
    assert menu_id is not None
    return menu_id


# A new menu at the bottom


async def test_start_sends_the_keyboard_and_a_menu(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.send("/start")

    first, keyboard, carrier, menu = harness.session.calls
    # The owner's /start goes, the keyboard comes with a message that goes, then the menu.
    assert isinstance(first, DeleteMessage) and first.message_id != harness.hub.prefs.menu_id
    assert isinstance(keyboard, SendMessage)
    markup = keyboard.reply_markup
    assert isinstance(markup, ReplyKeyboardMarkup) and markup.is_persistent
    assert markup.keyboard[0][0].text == "☰ Меню"
    assert isinstance(carrier, DeleteMessage)
    assert isinstance(menu, SendMessage) and "Выберите раздел" in str(menu.text)
    assert harness.hub.prefs.menu_id is not None


async def test_menu_button_replaces_the_old_menu(harness: Harness) -> None:
    harness.onboard("en")
    old = await _start(harness)
    start = len(harness.session.calls)

    await harness.send("☰ Menu")

    calls = _since(harness, start)
    new = harness.hub.prefs.menu_id
    assert new is not None and new != old
    deleted = [c.message_id for c in calls if isinstance(c, DeleteMessage)]
    assert old in deleted  # the old menu
    assert len(deleted) == 2  # and the owner's «☰ Menu»
    sent = [c for c in calls if isinstance(c, SendMessage)]
    assert len(sent) == 1  # the keyboard is there already: only the menu
    assert "Pick a section" in str(sent[0].text)


async def test_old_menu_loses_its_buttons_when_it_cannot_be_deleted(harness: Harness) -> None:
    harness.onboard("ru")
    old = await _start(harness)

    def refuse(method: TelegramMethod[Any]) -> str | None:
        if isinstance(method, DeleteMessage) and method.message_id == old:
            return "Bad Request: message can't be deleted for everyone"
        return None

    harness.session.refuse = refuse
    await harness.send("☰ Меню")

    [closed] = [c for c in _of(harness, EditMessageText) if c.message_id == old]
    assert closed.text == "Меню закрыто: актуальное ниже."
    assert closed.reply_markup is None
    assert harness.hub.prefs.menu_id != old


async def test_old_photo_menu_loses_its_buttons(harness: Harness) -> None:
    harness.onboard("ru")
    old = await _start(harness)

    def refuse(method: TelegramMethod[Any]) -> str | None:
        if isinstance(method, DeleteMessage | EditMessageText) and method.message_id == old:
            return "Bad Request: there is no text in the message to edit"
        return None

    harness.session.refuse = refuse
    await harness.send("☰ Меню")

    [stripped] = _of(harness, EditMessageReplyMarkup)
    assert stripped.message_id == old and stripped.reply_markup is None


# Navigation edits the one menu; other menus are closed


async def test_navigation_edits_the_menu_in_place(harness: Harness) -> None:
    harness.onboard("ru")
    menu_id = await _start(harness)
    start = len(harness.session.calls)

    await harness.press(Nav(to="settings"))
    await harness.press(Nav(to="menu"))

    edits = [c for c in _since(harness, start) if isinstance(c, EditMessageText)]
    assert [c.message_id for c in edits] == [menu_id, menu_id]
    assert not [c for c in _since(harness, start) if isinstance(c, SendMessage)]


async def test_a_press_in_an_old_menu_does_nothing(harness: Harness) -> None:
    harness.onboard("ru")
    acc = harness.hub.accounts.add("fake", "main", {})
    menu_id = await _start(harness)
    start = len(harness.session.calls)

    await harness.press(AccountCb(acc=acc, op="delete_yes"), message_id=menu_id - 1)

    assert harness.hub.accounts.all()  # nothing was deleted
    [only] = _since(harness, start)
    assert isinstance(only, AnswerCallbackQuery)
    assert only.text == STALE and only.show_alert
    assert harness.hub.prefs.menu_id == menu_id


async def test_not_modified_is_not_an_error(harness: Harness) -> None:
    harness.onboard("ru")
    menu_id = await _start(harness)

    def refuse(method: TelegramMethod[Any]) -> str | None:
        if isinstance(method, EditMessageText):
            return "Bad Request: message is not modified"
        return None

    harness.session.refuse = refuse
    start = len(harness.session.calls)
    await harness.press(Nav(to="menu"))

    assert not [c for c in _since(harness, start) if isinstance(c, SendMessage)]
    assert harness.hub.prefs.menu_id == menu_id


async def test_a_menu_deleted_by_the_owner_comes_back_below(harness: Harness) -> None:
    harness.onboard("ru")
    menu_id = await _start(harness)

    def refuse(method: TelegramMethod[Any]) -> str | None:
        if isinstance(method, EditMessageText) and method.message_id == menu_id:
            return "Bad Request: message to edit not found"
        return None

    harness.session.refuse = refuse
    await harness.press(Nav(to="settings"))

    assert "<b>Настройки</b>" in str(_of(harness, SendMessage)[-1].text)
    assert harness.hub.prefs.menu_id != menu_id


async def test_the_first_press_after_the_update_adopts_its_menu(harness: Harness) -> None:
    harness.onboard("ru")
    assert harness.hub.prefs.menu_id is None

    await harness.press(Nav(to="settings"), message_id=77)

    assert harness.hub.prefs.menu_id == 77
    assert _of(harness, EditMessageText)[-1].message_id == 77
    await harness.press(Nav(to="menu"), message_id=76)
    assert harness.alerts()[-1] == STALE


async def test_the_menu_survives_a_restart(
    harness: Harness, conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    harness.onboard("ru")
    menu_id = await _start(harness)

    again = Harness(conn, accounts)  # a new process on the same database
    try:
        assert again.hub.prefs.menu_id == menu_id
        await again.press(Nav(to="settings"), message_id=menu_id)
        assert _of(again, EditMessageText)[-1].message_id == menu_id
        await again.press(Nav(to="settings"), message_id=menu_id - 3)
        assert again.alerts()[-1] == STALE
    finally:
        await again.poller.stop()
        await again.bot.session.close()


# Typed answers: the question is in the menu, the answer goes


async def test_typed_amount_is_deleted_and_the_menu_asks_again(harness: Harness) -> None:
    harness.onboard("ru")
    acc = harness.hub.accounts.add("fake", "main", {"balance": "20.00"})
    await harness.poller.poll_account(acc)
    menu_id = await _start(harness)
    await harness.press(TopupAmountCb(acc=acc, m="usdt", amt=""))
    assert "Напишите сумму" in harness.session.last_text()
    start = len(harness.session.calls)

    await harness.send("3")

    deleted, edited = _since(harness, start)
    assert isinstance(deleted, DeleteMessage) and deleted.message_id != menu_id
    assert isinstance(edited, EditMessageText) and edited.message_id == menu_id
    assert "меньше минимума" in str(edited.text) and "Напишите сумму" in str(edited.text)


async def test_typed_threshold_is_deleted_and_the_menu_shows_settings(harness: Harness) -> None:
    harness.onboard("ru")
    menu_id = await _start(harness)
    await harness.press(SetCb(key="th", value="quiet_hours"))
    start = len(harness.session.calls)

    await harness.send("22:30 - 07:00")

    deleted, edited = _since(harness, start)
    assert isinstance(deleted, DeleteMessage)
    assert isinstance(edited, EditMessageText) and edited.message_id == menu_id
    assert harness.hub.prefs.threshold("quiet_hours") == "22:30-07:00"


async def test_a_new_menu_cancels_the_question(harness: Harness) -> None:
    harness.onboard("ru")
    await _start(harness)
    await harness.press(SetCb(key="th", value="quiet_hours"))

    await harness.send("☰ Меню")
    await harness.send("22:00-07:00")

    assert harness.hub.prefs.threshold("quiet_hours") != "22:00-07:00"
    assert "Выберите раздел" in harness.session.last_text()


# Notifications: their buttons open a new menu below


async def test_notification_button_opens_a_menu_below_on_its_screen(harness: Harness) -> None:
    harness.onboard("ru")
    acc = harness.hub.accounts.add("fake", "main", {"balance": "20.00"})
    await harness.poller.poll_account(acc)
    old = await _start(harness)
    assert harness.hub.menu is not None
    notice = await harness.hub.menu.notify("alert")
    assert notice is not None

    await harness.press(note(TopupCb(acc=acc)), message_id=notice)

    new = harness.hub.prefs.menu_id
    assert new not in (None, old, notice)
    assert old in [c.message_id for c in _of(harness, DeleteMessage)]
    assert notice not in [c.message_id for c in _of(harness, DeleteMessage)]
    last = _of(harness, EditMessageText)[-1]
    assert last.message_id == new and "Пополнение" in str(last.text)
    assert STALE not in harness.alerts()


async def test_notification_buttons_keep_working(harness: Harness) -> None:
    harness.onboard("ru")
    await _start(harness)
    notice = 4242

    await harness.press(note(Nav(to="settings")), message_id=notice)
    await harness.press(note(Nav(to="journal")), message_id=notice)

    assert "Журнал" in harness.session.last_text()
    assert STALE not in harness.alerts()


async def test_notification_button_cancels_a_question(harness: Harness) -> None:
    harness.onboard("ru")
    await _start(harness)
    await harness.press(SetCb(key="th", value="quiet_hours"))

    await harness.press(note(Nav(to="menu")), message_id=4242)
    await harness.send("22:00-07:00")

    assert harness.hub.prefs.threshold("quiet_hours") != "22:00-07:00"


async def test_paid_invoice_news_is_a_notification_once_the_menu_moved_on(
    harness: Harness,
) -> None:
    harness.onboard("ru")
    acc = harness.hub.accounts.add("fake", "main", {"balance": "20.00"})
    await harness.poller.poll_account(acc)
    await _start(harness)
    harness.hub.paid_checks = (0.05,)
    await harness.press(TopupAmountCb(acc=acc, m="sbp", amt="5"))
    harness.hub.accounts.save_secrets(acc, {"balance": "25.00"})
    await harness.press(PaidCb(pay=1))
    await harness.press(Nav(to="settings"))  # the owner went on while the bot waits
    menu_id = harness.hub.prefs.menu_id
    start = len(harness.session.calls)

    await asyncio.gather(*harness.hub.background)

    [news] = [c for c in _since(harness, start) if isinstance(c, SendMessage)]
    assert "Пополнение дошло" in str(news.text)
    assert not [c for c in _since(harness, start) if isinstance(c, EditMessageText)]
    assert harness.hub.prefs.menu_id == menu_id  # the menu was left alone


async def test_invoice_that_expires_says_so_in_the_menu(harness: Harness) -> None:
    from datetime import timedelta

    from hostwatch.bot import topup
    from tests.test_bot import NOW

    harness.onboard("ru")
    acc = harness.hub.accounts.add("fake", "main", {"balance": "20.00"})
    await harness.poller.poll_account(acc)
    await _start(harness)
    await harness.press(TopupAmountCb(acc=acc, m="sbp", amt="5"))
    assert harness.hub.menu is not None
    harness.hub.menu.tag = topup.pay_tag(1)

    await topup.expire(harness.hub, 1, NOW - timedelta(seconds=1))

    assert "Срок счёта истёк" in harness.session.last_text()
    assert TopupCb(acc=acc).pack() in harness.session.buttons()


# Buttons under alerts


def test_alert_buttons_lead_to_their_screens() -> None:
    from datetime import UTC

    v = View("ru", UTC)
    keys = ["balance_low:1", "server_status:1:7", "load:1:7:cpu", "relogin:2", "disk:9:1"]
    markup = alert_buttons(v, keys, {1: "Aeza", 2: "Regxa"}, {(1, "7"): "bear"})

    assert markup is not None
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons == [
        ("💰 Пополнить Aeza", note(TopupCb(acc=1))),
        ("🖥 bear", note(ServerCb(acc=1, sid="7"))),  # one button for both alerts
        ("🔌 Regxa", note(AccountCb(acc=2, op="card"))),
    ]
    assert alert_buttons(v, [], {}, {}) is None


async def test_alert_markup_reads_names_from_the_database(harness: Harness) -> None:
    harness.onboard("en")
    acc = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(acc)

    markup = alert_markup(harness.hub, [f"ends_soon:{acc}:1", f"balance_low:{acc}"])

    assert markup is not None
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert texts == ["🖥 beaver", "💰 Top up Fake hoster (main)"]
