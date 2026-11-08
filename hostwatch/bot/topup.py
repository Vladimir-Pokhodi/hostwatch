"""Balance → Top up: method → network (crypto) → amount → invoice with a link, a QR and «Paid».

The invoice is made when the owner picks the amount, not earlier: an SBP payment of
ЮKassa lives about ten minutes. After «Paid» the bot reads the balance 2, 5 and 10
minutes later and says whether the money arrived.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from html import escape

import segno
from aiogram import F
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.errors import AuthExpired, describe, explain
from hostwatch.core.forecast import end_of_next_month, needed_until
from hostwatch.core.models import Money, Payment, TopupKind, TopupMethod
from hostwatch.core.plugin import Plugin, SupportsTopup, browser_auth
from hostwatch.storage.repositories import JournalEntry, NotFound

from .callbacks import AccountCb, Nav, PaidCb, TopupAmountCb, TopupCb
from .handlers import answer, on, show, view
from .hub import AccountData, Hub
from .views import Screen, View
from .views import _button as button
from .views import _nav as nav

log = logging.getLogger(__name__)

CRYPTO = "*crypto"
QR_SCALE = 8
NSPK = "https://qr.nspk.ru/"


class TopupAmount(StatesGroup):
    amount = State()


@dataclass(frozen=True)
class Choice:
    data: AccountData
    methods: list[TopupMethod]

    def method(self, method_id: str) -> TopupMethod | None:
        return next((m for m in self.methods if m.id == method_id), None)

    @property
    def crypto(self) -> list[TopupMethod]:
        return [m for m in self.methods if m.kind is TopupKind.CRYPTO]


# Screens


def methods_screen(v: View, choice: Choice, networks: bool = False) -> Screen:
    """The ways to pay; with `networks` the crypto networks alone."""
    data = choice.data
    acc = data.account.id
    lines = _head(v, data)
    kb = InlineKeyboardBuilder()
    if networks:
        lines += ["", v.t("topup.choose_network")]
        for method in choice.crypto:
            kb.row(button(method.name, TopupCb(acc=acc, m=method.id)))
        kb.row(*nav(v, TopupCb(acc=acc)))
        return Screen("\n".join(lines), kb.as_markup())
    lines += ["", v.t("topup.choose_method")]
    for method in choice.methods:
        if method.kind is TopupKind.SBP:
            kb.row(button(v.t("topup.sbp", name=method.name), TopupCb(acc=acc, m=method.id)))
    if len(choice.crypto) == 1:
        only = choice.crypto[0]
        kb.row(button(v.t("topup.crypto_one", name=only.name), TopupCb(acc=acc, m=only.id)))
    elif choice.crypto:
        kb.row(button(v.t("topup.crypto"), TopupCb(acc=acc, m=CRYPTO)))
    _page_button(v, kb, data.plugin)
    kb.row(*nav(v, Nav(to="balance")))
    return Screen("\n".join(lines), kb.as_markup())


def amount_screen(v: View, choice: Choice, method: TopupMethod, until: Money | None) -> Screen:
    data = choice.data
    acc = data.account.id
    lines = [*_head(v, data), v.t("topup.method", name=escape(method.name))]
    if method.minimum is not None:
        lines.append(v.t("topup.minimum", amount=method.minimum))
    lines += ["", v.t("topup.choose_amount")]
    kb = InlineKeyboardBuilder()
    if method.minimum is not None:
        amount = method.minimum
        kb.row(
            button(
                v.t("topup.amount_minimum", amount=amount),
                TopupAmountCb(acc=acc, m=method.id, amt=_plain(amount.amount)),
            )
        )
    if until is not None:
        kb.row(
            button(
                v.t("topup.amount_until", amount=until),
                TopupAmountCb(acc=acc, m=method.id, amt=_plain(until.amount)),
            )
        )
    kb.row(button(v.t("topup.amount_custom"), TopupAmountCb(acc=acc, m=method.id, amt="")))
    kb.row(*nav(v, _back_from_amount(choice, method)))
    return Screen("\n".join(lines), kb.as_markup())


def ask_amount(v: View, method: TopupMethod, back: CallbackData) -> Screen:
    text = v.t("topup.ask_amount", currency=_currency(method))
    if method.minimum is not None:
        text += "\n" + v.t("topup.minimum", amount=method.minimum)
    kb = InlineKeyboardBuilder()
    kb.row(*nav(v, back))
    return Screen(text, kb.as_markup())


def payment_text(v: View, title: str, method: TopupMethod, payment: Payment) -> str:
    lines = [
        v.t("topup.invoice_title", title=escape(title)),
        v.t("topup.invoice_line", amount=payment.amount, method=escape(method.name)),
        v.t("topup.invoice_id", id=escape(payment.id)),
        "",
    ]
    if payment.address:
        sent = f"{payment.pay_amount or ''} {payment.pay_currency or ''}".strip()
        lines.append(v.t("topup.send_exactly", amount=escape(sent)))
        lines.append(f"<code>{escape(payment.address)}</code>")
        if payment.expires_at is not None:
            lines.append(v.t("topup.expires", date=v.moment(payment.expires_at)))
    elif payment.url and payment.url.startswith(NSPK):
        lines.append(v.t("topup.pay_sbp", url=escape(payment.url)))
    elif payment.url:
        lines.append(v.t("topup.pay_page", url=escape(payment.url)))
    lines += ["", v.t("topup.press_paid")]
    return "\n".join(lines)


def payment_markup(v: View, payment: Payment, record_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    if payment.url:
        kb.row(InlineKeyboardButton(text=v.t("topup.open"), url=payment.url))
    kb.row(button(v.t("topup.paid"), PaidCb(pay=record_id)))
    kb.row(*nav(v))
    return kb


def failed_screen(v: View, data: AccountData, error: BaseException, back: CallbackData) -> Screen:
    kb = InlineKeyboardBuilder()
    if isinstance(error, AuthExpired):
        text = v.t("topup.need_login", title=escape(data.title))
        if browser_auth(data.plugin) is not None:
            kb.row(button(v.t("account.relogin"), AccountCb(acc=data.account.id, op="relogin")))
    else:
        text = v.t("topup.failed", reason=escape(explain(describe(error), v.lang)))
    _page_button(v, kb, data.plugin)
    kb.row(*nav(v, back))
    return Screen(text, kb.as_markup())


def _head(v: View, data: AccountData) -> list[str]:
    lines = [v.t("topup.title", title=escape(data.title))]
    if data.balance is not None:
        lines.append(v.t("topup.balance", amount=data.balance.money))
    return lines


def _page_button(v: View, kb: InlineKeyboardBuilder, plugin: type[Plugin]) -> None:
    if plugin.topup_page:
        kb.row(InlineKeyboardButton(text=v.t("topup.page"), url=plugin.topup_page))


def _back_from_amount(choice: Choice, method: TopupMethod) -> TopupCb:
    acc = choice.data.account.id
    if method.kind is TopupKind.CRYPTO and len(choice.crypto) > 1:
        return TopupCb(acc=acc, m=CRYPTO)
    return TopupCb(acc=acc)


def _currency(method: TopupMethod) -> str:
    return method.minimum.currency if method.minimum is not None else ""


def _plain(amount: Decimal) -> str:
    return format(amount.normalize(), "f")


def qr_png(data: str) -> bytes:
    out = io.BytesIO()
    segno.make(data, error="m").save(out, kind="png", scale=QR_SCALE, border=2)
    return out.getvalue()


def parse_amount(text: str) -> Decimal | None:
    """«500», «12,5», «€ 7.50» → a positive sum with at most two decimals; else None."""
    cleaned = "".join(ch for ch in text.replace(",", ".") if ch.isdigit() or ch == ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        return None
    return amount


# Data


async def _choice(hub: Hub, acc: int) -> Choice:
    data = hub.load(acc)

    async def methods(plugin: Plugin) -> list[TopupMethod]:
        if not isinstance(plugin, SupportsTopup):
            return []
        return await plugin.topup_methods()

    return Choice(data, await hub.poller.runner.call(acc, methods, track_status=False))


def until_next_month(hub: Hub, data: AccountData, method: TopupMethod) -> Money | None:
    """«So that it lasts to the end of next month», rounded up; None unless above the minimum."""
    if data.balance is None:
        return None
    now = hub.now()
    need = needed_until(data.balance, data.servers, now, end_of_next_month(now, hub.prefs.tz))
    amount = need.amount.to_integral_value(rounding=ROUND_CEILING)
    if amount <= 0 or (method.minimum is not None and amount <= method.minimum.amount):
        return None
    return Money(amount=amount, currency=need.currency)


# Handlers


@on("callback_query", TopupCb.filter())
async def on_topup(
    query: CallbackQuery, callback_data: TopupCb, hub: Hub, state: FSMContext
) -> None:
    await state.clear()
    v = view(hub)
    acc = callback_data.acc
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    await answer(query)
    try:
        choice = await _choice(hub, acc)
    except Exception as error:
        await show(query, failed_screen(v, data, error, Nav(to="balance")))
        return
    if callback_data.m in ("", CRYPTO):
        await show(query, methods_screen(v, choice, networks=callback_data.m == CRYPTO))
        return
    method = choice.method(callback_data.m)
    if method is None:
        await show(query, methods_screen(v, choice))
        return
    await show(query, amount_screen(v, choice, method, until_next_month(hub, choice.data, method)))


@on("callback_query", TopupAmountCb.filter())
async def on_amount(
    query: CallbackQuery, callback_data: TopupAmountCb, hub: Hub, state: FSMContext
) -> None:
    v = view(hub)
    acc = callback_data.acc
    try:
        choice = await _choice(hub, acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    except Exception as error:
        await answer(query)
        await show(query, failed_screen(v, hub.load(acc), error, Nav(to="balance")))
        return
    method = choice.method(callback_data.m)
    if method is None:
        await answer(query)
        await show(query, methods_screen(v, choice))
        return
    back = TopupCb(acc=acc, m=method.id)
    if not callback_data.amt:
        await answer(query)
        await state.set_state(TopupAmount.amount)
        await state.update_data(acc=acc, method=method.id)
        await show(query, ask_amount(v, method, back))
        return
    amount = parse_amount(callback_data.amt)
    if amount is None:
        await answer(query)
        return
    await answer(query, v.t("topup.creating"))
    await show(query, Screen(v.t("topup.creating")))
    delivery = await create(hub, choice, method, Money(amount=amount, currency=_currency(method)))
    await _deliver(query.message, delivery)


@on("message", TopupAmount.amount, F.text & ~F.text.startswith("/"))
async def on_amount_text(message: Message, hub: Hub, state: FSMContext) -> None:
    v = view(hub)
    saved = await state.get_data()
    acc, method_id = int(saved["acc"]), str(saved["method"])
    amount = parse_amount(message.text or "")
    try:
        choice = await _choice(hub, acc)
    except Exception as error:
        await state.clear()
        screen = failed_screen(v, hub.load(acc), error, Nav(to="balance"))
        await message.answer(screen.text, reply_markup=screen.markup)
        return
    method = choice.method(method_id)
    if method is None:
        await state.clear()
        await show(message, methods_screen(v, choice))
        return
    back = TopupCb(acc=acc, m=method.id)
    if amount is None:
        await show(message, ask_amount(v, method, back))
        return
    if method.minimum is not None and amount < method.minimum.amount:
        await message.answer(v.t("topup.below_minimum", amount=method.minimum))
        return
    await state.clear()
    await message.answer(v.t("topup.creating"))
    delivery = await create(hub, choice, method, Money(amount=amount, currency=_currency(method)))
    await _deliver(message, delivery)


@dataclass(frozen=True)
class Delivery:
    """What to send after an invoice attempt: a QR picture with a caption, or a text."""

    text: str
    markup: InlineKeyboardBuilder
    qr: bytes | None = None


async def create(hub: Hub, choice: Choice, method: TopupMethod, amount: Money) -> Delivery:
    """Make the invoice at the hoster; log it in the journal and in `payments`."""
    v = view(hub)
    data = choice.data
    acc = data.account.id

    async def topup(plugin: Plugin) -> Payment:
        if not isinstance(plugin, SupportsTopup):
            raise NotImplementedError
        return await plugin.topup(amount, method.id)

    params = {"amount": str(amount.amount), "currency": amount.currency, "method": method.id}
    try:
        payment = await hub.poller.runner.call(acc, topup, track_status=False)
    except Exception as error:
        log.warning("top-up of account %s failed: %s", acc, describe(error))
        _journal(hub, acc, params, describe(error), ok=False)
        screen = failed_screen(v, data, error, TopupCb(acc=acc, m=method.id))
        kb = InlineKeyboardBuilder.from_markup(screen.markup) if screen.markup else None
        return Delivery(screen.text, kb or InlineKeyboardBuilder())
    record_id = hub.payments.add(acc, payment)
    _journal(hub, acc, {**params, "invoice": payment.id}, "invoice created", ok=True)
    text = payment_text(v, data.title, method, payment)
    qr = qr_png(payment.qr_data) if payment.qr_data else None
    return Delivery(text, payment_markup(v, payment, record_id), qr)


def _journal(hub: Hub, acc: int, params: dict[str, str], response: str, ok: bool) -> None:
    hub.journal.add(
        JournalEntry(
            at=hub.now(),
            actor="owner",
            account_id=acc,
            action="topup",
            params=params,
            response=response,
            ok=ok,
        )
    )


async def _deliver(message: Message | InaccessibleMessage | None, delivery: Delivery) -> None:
    if message is None or isinstance(message, InaccessibleMessage):
        return
    markup = delivery.markup.as_markup()
    if delivery.qr is None:
        await message.answer(delivery.text, reply_markup=markup)
        return
    photo = BufferedInputFile(delivery.qr, filename="qr.png")
    await message.answer_photo(photo, caption=delivery.text, reply_markup=markup)


# «Paid»


@on("callback_query", PaidCb.filter())
async def on_paid(query: CallbackQuery, callback_data: PaidCb, hub: Hub) -> None:
    v = view(hub)
    try:
        record = hub.payments.get(callback_data.pay)
        data = hub.load(record.account_id)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    if record.status == "waiting":
        await answer(query, v.t("topup.already_checking"), alert=True)
        return
    before = data.balance.money if data.balance is not None else None
    hub.payments.set_status(record.id, "waiting", before)
    await answer(query, v.t("topup.will_check"))
    message = query.message
    if message is None or isinstance(message, InaccessibleMessage):
        return
    await message.answer(v.t("topup.will_check"))
    task = asyncio.create_task(watch_balance(hub, record.id, message), name=f"paid-{record.id}")
    hub.background.add(task)
    task.add_done_callback(hub.background.discard)


async def watch_balance(hub: Hub, record_id: int, message: Message) -> None:
    """Read the balance at each of `hub.paid_checks` (seconds after «Paid»)."""
    record = hub.payments.get(record_id)
    acc = record.account_id
    title = hub.load(acc).title
    before = record.balance_before
    waited = 0.0
    for at in hub.paid_checks:
        await asyncio.sleep(max(at - waited, 0))
        waited = at
        try:
            balance = await hub.poller.read_balance(acc)
        except Exception as error:
            log.warning("balance check after top-up failed: %s", describe(error))
            continue
        now = balance.money
        if before is None or before.currency != now.currency:
            before = now  # nothing to compare with yet: this read is the base
            continue
        if now > before:
            hub.payments.set_status(record_id, "arrived")
            v = view(hub)
            lines = [v.t("topup.arrived", title=escape(title), amount=now)]
            forecast = hub.load(acc).forecast
            if forecast is not None:
                lines.append(escape(forecast.explain(v.lang, v.tz)))
            await message.answer("\n".join(lines))
            return
    hub.payments.set_status(record_id, "not_seen")
    v = view(hub)
    minutes = int(hub.paid_checks[-1] // 60) if hub.paid_checks else 0
    await message.answer(v.t("topup.not_seen", title=escape(title), minutes=minutes))
