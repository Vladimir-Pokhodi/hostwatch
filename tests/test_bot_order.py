"""Ordering in the bot (stage 13): limit, second confirmation, price changes, shortfall,
root password kept out of the journal and the logs."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from aiogram.methods import DeleteMessage, SendMessage

from hostwatch.bot import order as bot_order
from hostwatch.bot.callbacks import ActionCb, SetCb, TopupAmountCb
from hostwatch.bot.order import OrderCb, OrderConfirmCb
from hostwatch.plugins.fake import FakePlugin
from hostwatch.storage.repositories import AccountRepository, JournalEntry
from tests.test_bot import NOW, Harness

PASSWORD = "Fake-Root-Pass-1"
SMALL = OrderCb(acc=0, loc="nw", plan="small", os="debian-12", per=1)
KEY = "ssh-ed25519 " + "A" * 68 + " owner@pc"


@pytest.fixture
async def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> AsyncIterator[Harness]:
    FakePlugin.calls.clear()
    h = Harness(conn, accounts)
    yield h
    for task in list(h.hub.background):
        task.cancel()
    await h.poller.stop()
    await h.bot.session.close()


async def _account(h: Harness, **secrets: str) -> int:
    h.onboard("ru")
    acc = h.hub.accounts.add("fake", "main", dict(secrets))
    await h.poller.poll_account(acc)
    return acc


def _small(acc: int) -> OrderCb:
    return SMALL.model_copy(update={"acc": acc})


def _yes(h: Harness) -> OrderConfirmCb:
    found = [OrderConfirmCb.unpack(b) for b in h.session.buttons() if b.startswith("oc:")]
    return next(d for d in found if d.yes)


def _orders(h: Harness) -> list[JournalEntry]:
    return [e for e in h.hub.journal.recent(50) if e.action == "order"]


async def test_steps_lead_from_location_to_period(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(OrderCb(acc=acc))
    assert "Выберите локацию" in harness.session.last_text()
    assert OrderCb(acc=acc, loc="nw").pack() in harness.session.buttons()

    await harness.press(OrderCb(acc=acc, loc="nw"))
    assert "Fake Small" in harness.session.last_text()
    await harness.press(OrderCb(acc=acc, loc="nw", plan="big"))
    # «Fake Big» takes Debian only.
    assert harness.session.buttons()[:1] == [
        OrderCb(acc=acc, loc="nw", plan="big", os="debian-12").pack()
    ]
    await harness.press(OrderCb(acc=acc, loc="nw", plan="small", os="ubuntu-24"))
    assert "Выберите срок" in harness.session.last_text()
    assert FakePlugin.calls == []


async def test_default_limit_asks_twice_and_orders_once(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(_small(acc))
    summary = harness.session.last_text()
    assert "❓ Заказать за €4.50/мес?" in summary
    assert "⚠️ Выше лимита: 0.00 ₽ из 0.00 ₽ за месяц" in summary
    assert "не проверен вживую" not in summary
    assert FakePlugin.calls == []

    first = _yes(harness)
    await harness.press(first)
    assert "Заказ выше месячного лимита" in harness.session.last_text()
    assert FakePlugin.calls == []

    await harness.press(_yes(harness))
    await harness.press(first)  # an old button does nothing

    assert FakePlugin.calls == ["order small debian-12 1"]
    [entry] = _orders(harness)
    assert entry.ok
    assert entry.params["amount"] == "4.50"
    assert entry.params["currency"] == "EUR"
    assert entry.params["above_limit"] == "yes"
    assert entry.params["server"] == "9"
    texts = harness.session.texts()
    assert any("Действие с деньгами" in t and "Заказано" in t for t in texts)
    assert any(f"<code>{PASSWORD}</code>" in t for t in texts)
    assert harness.alerts()[-1] == "Кнопка устарела или уже нажата. Ничего не сделал."


async def test_order_under_the_limit_takes_one_yes(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.prefs.order_limit = Decimal(1000)  # RUB; €4.50 is 450 ₽

    await harness.press(_small(acc))
    await harness.press(_yes(harness))

    assert FakePlugin.calls == ["order small debian-12 1"]
    assert "above_limit" not in _orders(harness)[0].params


async def test_orders_of_the_month_count_against_the_limit(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.prefs.order_limit = Decimal(800)
    harness.hub.journal.add(
        JournalEntry(
            at=NOW,
            actor="owner",
            account_id=acc,
            action="order",
            params={"amount": "4.50", "currency": "EUR"},
            response="ok",
            ok=True,
        )
    )

    await harness.press(_small(acc))
    assert "450.00 ₽ из 800.00 ₽ за месяц" in harness.session.last_text()
    await harness.press(_yes(harness))

    assert "Заказ выше месячного лимита" in harness.session.last_text()
    assert FakePlugin.calls == []


async def test_price_from_the_hoster_wins_over_the_catalog(harness: Harness) -> None:
    acc = await _account(harness, order_price="5.00")
    harness.hub.prefs.order_limit = Decimal(10000)

    await harness.press(_small(acc))

    summary = harness.session.last_text()
    assert "Цена изменилась: в каталоге было €4.50, хостер просит €5.00" in summary
    assert "❓ Заказать за €5.00/мес?" in summary
    await harness.press(_yes(harness))
    assert FakePlugin.calls == ["order small debian-12 1"]
    assert _orders(harness)[0].params["amount"] == "5.00"


async def test_price_change_after_the_yes_orders_nothing(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.prefs.order_limit = Decimal(10000)
    await harness.press(_small(acc))
    harness.hub.accounts.save_secrets(acc, {"order_price": "6.00"})

    await harness.press(_yes(harness))

    assert FakePlugin.calls == []
    assert "Цена изменилась перед заказом: было €4.50, стало €6.00" in "\n".join(
        harness.session.texts()
    )
    [entry] = _orders(harness)
    assert not entry.ok
    assert "price" in entry.response


async def test_shortfall_offers_a_top_up_of_the_missing_sum(harness: Harness) -> None:
    acc = await _account(harness, balance="1.00")

    await harness.press(_small(acc))

    text = harness.session.last_text()
    assert "нужно €4.50, на балансе €1.00, не хватает €3.50" in text
    buttons = harness.session.buttons()
    assert TopupAmountCb(acc=acc, m="sbp", amt="4").pack() in buttons
    assert TopupAmountCb(acc=acc, m="usdt", amt="5").pack() in buttons  # its minimum
    assert _small(acc).pack() in buttons  # «Check again»
    assert not [b for b in buttons if b.startswith("oc:")]
    assert FakePlugin.calls == []


async def test_cancel_orders_nothing(harness: Harness) -> None:
    acc = await _account(harness)
    await harness.press(_small(acc))
    no = next(
        d
        for d in (
            OrderConfirmCb.unpack(b) for b in harness.session.buttons() if b.startswith("oc:")
        )
        if not d.yes
    )

    await harness.press(no)

    assert "Заказ отменён" in harness.session.last_text()
    assert FakePlugin.calls == []
    assert _orders(harness) == []


async def test_ssh_key_from_the_settings_goes_with_the_order(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.prefs.order_limit = Decimal(10000)

    await harness.press(SetCb(key="order", value="key"))
    await harness.send("-----BEGIN OPENSSH PRIVATE KEY----- abc")
    assert harness.hub.prefs.ssh_key is None
    assert "приватный ключ" in harness.session.last_text()
    await harness.send(KEY)
    assert harness.hub.prefs.ssh_key == KEY

    await harness.press(_small(acc))
    assert "🔑 SSH-ключ из настроек" in harness.session.last_text()
    await harness.press(_yes(harness))
    assert FakePlugin.calls == ["order small debian-12 1 with key"]


async def test_limit_is_set_in_the_settings(harness: Harness) -> None:
    harness.onboard("ru")
    await harness.press(SetCb(key="order", value="limit"))
    await harness.send("-5")
    assert harness.hub.prefs.order_limit == 0
    await harness.send("1 500,50")
    assert harness.hub.prefs.order_limit == Decimal("1500.50")
    assert "💰 Лимит в месяц: 1 500.50 ₽" in harness.session.last_text()


async def test_password_is_shown_then_deleted_and_never_kept(
    harness: Harness, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(bot_order, "PASSWORD_TTL", 0.0)
    acc = await _account(harness)

    await harness.press(ActionCb(acc=acc, sid="1", act="password"))
    for _ in range(5):
        await asyncio.sleep(0)

    sent = [c for c in harness.session.calls if isinstance(c, SendMessage)]
    # The password is a message of its own; the menu goes below it with the server card.
    assert PASSWORD in str(sent[-2].text)
    assert "<b>🟢 beaver · Fake hoster (main)</b>" in str(sent[-1].text)
    assert any(isinstance(c, DeleteMessage) for c in harness.session.calls)
    assert FakePlugin.calls == ["root_password 1"]
    [entry] = [e for e in harness.hub.journal.recent() if e.action == "root_password"]
    assert entry.ok
    assert PASSWORD not in f"{entry.params} {entry.response}"
    assert PASSWORD not in caplog.text
    dump = "\n".join(harness.hub.journal._conn.iterdump())  # the whole database
    assert PASSWORD not in dump


async def test_password_of_an_order_stays_out_of_the_journal(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.prefs.order_limit = Decimal(10000)
    await harness.press(_small(acc))
    await harness.press(_yes(harness))

    dump = "\n".join(harness.hub.journal._conn.iterdump())
    assert PASSWORD not in dump
    assert any(PASSWORD in t for t in harness.session.texts())
