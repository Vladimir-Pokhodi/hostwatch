"""Balance → Top up in the bot, on the fake hoster."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from aiogram.methods import SendMessage, SendPhoto

from hostwatch.bot.callbacks import PaidCb, TopupAmountCb, TopupCb
from hostwatch.bot.topup import CRYPTO, parse_amount
from hostwatch.storage.repositories import AccountRepository
from tests.test_bot import Harness


@pytest.fixture
async def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> AsyncIterator[Harness]:
    h = Harness(conn, accounts)
    yield h
    await h.poller.stop()
    await h.bot.session.close()


async def _account(h: Harness, balance: str = "20.00") -> int:
    h.onboard("ru")
    acc = h.hub.accounts.add("fake", "main", {"balance": balance})
    await h.poller.poll_account(acc)
    return acc


async def test_methods_then_networks(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(TopupCb(acc=acc))
    buttons = harness.session.keyboard()
    assert TopupCb(acc=acc, m="sbp").pack() in buttons
    assert TopupCb(acc=acc, m=CRYPTO).pack() in buttons
    assert "Пополнение" in harness.session.last_text()

    await harness.press(TopupCb(acc=acc, m=CRYPTO))
    assert set(harness.session.buttons()) >= {
        TopupCb(acc=acc, m="usdt").pack(),
        TopupCb(acc=acc, m="btc").pack(),
    }


async def test_amount_screen_offers_minimum_month_and_custom(harness: Harness) -> None:
    acc = await _account(harness, balance="1.00")

    await harness.press(TopupCb(acc=acc, m="sbp"))

    buttons = harness.session.keyboard()
    assert TopupAmountCb(acc=acc, m="sbp", amt="0.5").pack() in buttons
    assert TopupAmountCb(acc=acc, m="sbp", amt="").pack() in buttons
    until = [text for text in buttons.values() if "До конца следующего месяца" in text]
    assert until, buttons


async def test_sbp_invoice_comes_with_qr_and_paid_button(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(TopupAmountCb(acc=acc, m="sbp", amt="0.5"))

    photo = next(c for c in harness.session.calls if isinstance(c, SendPhoto))
    assert photo.caption is not None and "https://qr.nspk.ru/" in photo.caption
    assert "€0.50" in photo.caption
    markup = photo.reply_markup
    assert markup is not None
    data = [b.callback_data for row in markup.inline_keyboard for b in row]  # type: ignore[union-attr]
    urls = [b.url for row in markup.inline_keyboard for b in row]  # type: ignore[union-attr]
    assert PaidCb(pay=1).pack() in data
    assert "https://qr.nspk.ru/FAKE0000000000000000000000000000" in urls
    record = harness.hub.payments.get(1)
    assert (record.external_id, record.status) == ("200", "created")
    entry = harness.hub.journal.recent()[0]
    assert (entry.action, entry.ok, entry.params["invoice"]) == ("topup", True, "200")


async def test_crypto_invoice_shows_the_address(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(TopupAmountCb(acc=acc, m="usdt", amt="5"))

    photo = next(c for c in harness.session.calls if isinstance(c, SendPhoto))
    assert photo.caption is not None
    assert "<code>TFakeAddress000000000000000000000</code>" in photo.caption
    assert "5.45 USDT (TRC-20)" in photo.caption


async def test_custom_amount_is_checked_against_the_minimum(harness: Harness) -> None:
    acc = await _account(harness)

    await harness.press(TopupAmountCb(acc=acc, m="usdt", amt=""))
    assert "Напишите сумму" in harness.session.last_text()
    await harness.send("3")
    assert "меньше минимума" in harness.session.last_text()
    await harness.send("7,50")

    assert any(isinstance(c, SendPhoto) for c in harness.session.calls)
    assert harness.hub.payments.get(1).amount.amount == Decimal("7.50")


async def test_failed_invoice_offers_the_hoster_page(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.accounts.save_secrets(acc, {"fail": "hoster_error"})

    await harness.press(TopupCb(acc=acc))

    assert "Счёт не создался" in harness.session.last_text()
    entries = harness.hub.journal.recent()
    assert not entries  # nothing was created, so nothing to log
    # The account status belongs to polling: a top-up failure does not change it.
    assert harness.hub.accounts.get(acc).last_error is None


async def test_paid_reads_the_balance_until_the_money_arrives(harness: Harness) -> None:
    acc = await _account(harness, balance="20.00")
    harness.hub.paid_checks = (0.0, 0.0)
    await harness.press(TopupAmountCb(acc=acc, m="sbp", amt="5"))
    harness.hub.accounts.save_secrets(acc, {"balance": "25.00"})

    await harness.press(PaidCb(pay=1))
    await asyncio.gather(*harness.hub.background)

    texts = [str(c.text) for c in harness.session.calls if isinstance(c, SendMessage)]
    assert any("Проверю баланс" in t for t in texts)
    assert any("Пополнение дошло" in t and "€25.00" in t for t in texts)
    assert harness.hub.payments.get(1).status == "arrived"


async def test_paid_says_when_nothing_arrived(harness: Harness) -> None:
    acc = await _account(harness)
    harness.hub.paid_checks = (0.0,)
    await harness.press(TopupAmountCb(acc=acc, m="sbp", amt="5"))

    await harness.press(PaidCb(pay=1))
    await asyncio.gather(*harness.hub.background)

    assert "пополнение не видно" in harness.session.last_text()
    assert harness.hub.payments.get(1).status == "not_seen"


def test_parse_amount() -> None:
    assert parse_amount("500") == Decimal("500")
    assert parse_amount("12,5 €") == Decimal("12.5")
    assert parse_amount("1.005") is None
    assert parse_amount("ноль") is None
    assert parse_amount("0") is None
