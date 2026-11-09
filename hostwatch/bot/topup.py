"""Balance → Top up: method → coin → network (crypto) → amount → invoice with a link, a QR
and «Paid», all in the one menu (`menu.py`).

The invoice is made when the owner picks the amount, not earlier: an SBP payment of
ЮKassa lives about ten minutes. Some payment pages list their coins per invoice
(Cryptomus at Regxa, `SupportsPayOptions`): there the coin and the network are chosen
under the invoice, and the bot then gets the address. After «Paid» the bot reads the
balance 2, 5 and 10 minutes later and says whether the money arrived: in the menu while it
still shows that invoice, else in a notification. An invoice with a QR code turns the menu
into a photo (a text message cannot get a picture); an invoice that expires says so.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from html import escape

import segno
from aiogram import F
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.errors import AuthExpired, describe, explain
from hostwatch.core.forecast import end_of_next_month, needed_until
from hostwatch.core.models import Money, Payment, PayOption, TopupKind, TopupMethod
from hostwatch.core.plugin import Plugin, SupportsPayOptions, SupportsTopup, browser_auth
from hostwatch.storage.repositories import JournalEntry, NotFound, PaymentRecord

from .callbacks import AccountCb, Nav, PaidCb, PayOptCb, TopupAmountCb, TopupCb, note
from .handlers import answer, home_markup, menu, on, show, view, with_error
from .hub import AccountData, Hub
from .views import Screen, View, pairs
from .views import _button as button
from .views import _lasts as money_lasts
from .views import _nav as nav

log = logging.getLogger(__name__)

CRYPTO = "*crypto"
COINS_PER_ROW = 4
COINS_PER_PAGE = 24
# Coin lists of this many recent invoices are kept in memory.
KEPT_OPTIONS = 20
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

    @property
    def coins(self) -> list[tuple[str, list[TopupMethod]]]:
        return by_coin(self.crypto, lambda m: m.coin or m.name)

    def coin_of(self, method: TopupMethod) -> list[TopupMethod]:
        return dict(self.coins).get(method.coin or method.name, [])

    def page_of(self, coin: str) -> int:
        names = [name for name, _ in self.coins]
        return names.index(coin) // COINS_PER_PAGE if coin in names else 0


# Screens


def by_coin[T](items: Sequence[T], coin: Callable[[T], str]) -> list[tuple[str, list[T]]]:
    """Items grouped by coin, in the order the coins first appear."""
    groups: dict[str, list[T]] = {}
    for item in items:
        groups.setdefault(coin(item), []).append(item)
    return list(groups.items())


def coin_buttons(
    kb: InlineKeyboardBuilder,
    buttons: list[InlineKeyboardButton],
    page: int,
    to_page: Callable[[int], CallbackData],
) -> None:
    """Coins in rows of four, a page at a time (Cryptomus has 80 and more), with ◀ ▶."""
    pages = max(1, -(-len(buttons) // COINS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    shown = buttons[page * COINS_PER_PAGE : (page + 1) * COINS_PER_PAGE]
    for start in range(0, len(shown), COINS_PER_ROW):
        kb.row(*shown[start : start + COINS_PER_ROW])
    if pages > 1:
        arrows = []
        if page > 0:
            arrows.append(button("◀", to_page(page - 1)))
        arrows.append(button(f"{page + 1}/{pages}", to_page(page)))
        if page < pages - 1:
            arrows.append(button("▶", to_page(page + 1)))
        kb.row(*arrows)


def methods_screen(v: View, choice: Choice) -> Screen:
    """The ways to pay."""
    data = choice.data
    acc = data.account.id
    lines = _head(v, data, 1)
    kb = InlineKeyboardBuilder()
    buttons = [
        button(_sbp_label(v, method), TopupCb(acc=acc, m=method.id))
        for method in choice.methods
        if method.kind is TopupKind.SBP
    ]
    if len(choice.crypto) == 1:
        only = choice.crypto[0]
        buttons.append(button(v.t("topup.crypto_one", name=only.name), TopupCb(acc=acc, m=only.id)))
    elif choice.crypto:
        buttons.append(button(v.t("topup.crypto"), TopupCb(acc=acc, m=CRYPTO)))
    if not buttons:
        lines.append(v.t("topup.no_methods"))
    pairs(kb, buttons)
    _page_button(v, kb, data.plugin)
    kb.row(*nav(v, Nav(to="balance")))
    return Screen("\n".join(lines), kb.as_markup())


def coins_screen(v: View, choice: Choice, page: int = 0) -> Screen:
    """Crypto before the invoice: a coin with one network leads straight to the amount."""
    acc = choice.data.account.id
    lines = [*_head(v, choice.data, 1), v.t("topup.choose_coin")]
    buttons = []
    for coin, methods in choice.coins:
        if len(methods) == 1:
            only = methods[0]
            buttons.append(button(only.name, TopupCb(acc=acc, m=only.id)))
        else:
            buttons.append(button(coin, TopupCb(acc=acc, m=CRYPTO, c=coin)))
    kb = InlineKeyboardBuilder()
    coin_buttons(kb, buttons, page, lambda p: TopupCb(acc=acc, m=CRYPTO, p=p))
    kb.row(*nav(v, TopupCb(acc=acc)))
    return Screen("\n".join(lines), kb.as_markup())


def networks_screen(v: View, choice: Choice, coin: str) -> Screen:
    acc = choice.data.account.id
    methods = dict(choice.coins).get(coin)
    if not methods:
        return coins_screen(v, choice)
    lines = [*_head(v, choice.data, 1), v.t("topup.choose_coin_network", coin=escape(coin))]
    kb = InlineKeyboardBuilder()
    for method in methods:
        kb.row(button(method.network or method.name, TopupCb(acc=acc, m=method.id)))
    kb.row(*nav(v, TopupCb(acc=acc, m=CRYPTO, p=choice.page_of(coin))))
    return Screen("\n".join(lines), kb.as_markup())


def options_screen(
    v: View,
    title: str,
    record: PaymentRecord,
    options: Sequence[PayOption],
    coin: str = "",
    page: int = 0,
) -> Screen:
    """An invoice whose coin is chosen after it is made: coins, then the coin's networks."""
    lines = [
        v.t("topup.invoice_title", title=escape(title)),
        _step(v, 3),
        v.t("topup.invoice_short", amount=record.amount, id=escape(record.external_id)),
        v.t("topup.choose_after"),
    ]
    groups = by_coin(options, lambda o: o.coin)
    networks = dict(groups).get(coin) if coin else None
    kb = InlineKeyboardBuilder()
    if networks:
        lines.append(v.t("topup.choose_coin_network", coin=escape(coin)))
        for option in networks:
            kb.row(button(option.network, PayOptCb(pay=record.id, o=option.id)))
        index = [c for c, _ in groups].index(coin)
        kb.row(button(v.t("bot.button.back"), PayOptCb(pay=record.id, p=index // COINS_PER_PAGE)))
    else:
        lines.append(v.t("topup.choose_coin"))
        buttons = [
            button(c, PayOptCb(pay=record.id, o=group[0].id))
            if len(group) == 1
            else button(c, PayOptCb(pay=record.id, c=c))
            for c, group in groups
        ]
        coin_buttons(kb, buttons, page, lambda p: PayOptCb(pay=record.id, p=p))
    _pay_row(v, kb, record.url if record.url.startswith("https://") else None, record.id)
    kb.row(*nav(v, Nav(to="balance")))
    return Screen("\n".join(lines), kb.as_markup())


def amount_screen(v: View, choice: Choice, method: TopupMethod, until: Money | None) -> Screen:
    data = choice.data
    acc = data.account.id
    lines = [*_head(v, data, 2), _method_line(v, method), v.t("topup.choose_amount")]
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
    text = f"{_step(v, 2)}\n{_method_line(v, method)}\n"
    text += v.t("topup.ask_amount", currency=_currency(method))
    kb = InlineKeyboardBuilder()
    kb.row(*nav(v, back))
    return Screen(text, kb.as_markup())


def payment_text(v: View, title: str, method: TopupMethod, payment: Payment) -> str:
    lines = [
        v.t("topup.invoice_title", title=escape(title)),
        _step(v, 3),
        v.t(
            "topup.invoice_line",
            amount=payment.amount,
            method=escape(method.name),
            id=escape(payment.id),
        ),
    ]
    if payment.address:
        sent = f"{payment.pay_amount or ''} {payment.pay_currency or ''}".strip()
        lines.append(v.t("topup.send_exactly", amount=escape(sent)))
        lines.append(f"<code>{escape(payment.address)}</code>")
        if payment.expires_at is not None:
            lines.append(v.t("topup.expires", date=v.moment(payment.expires_at)))
    elif payment.url and payment.url.startswith(NSPK):
        lines.append(v.t("topup.pay_sbp"))
    elif payment.url:
        lines.append(v.t("topup.pay_page"))
    lines.append(v.t("topup.press_paid"))
    return "\n".join(lines)


def payment_markup(v: View, payment: Payment, record_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    _pay_row(v, kb, payment.url, record_id)
    kb.row(*nav(v, Nav(to="balance")))
    return kb


def _pay_row(v: View, kb: InlineKeyboardBuilder, url: str | None, record_id: int) -> None:
    """«💳 Pay» (the link) and «✅ Paid» side by side."""
    row = []
    if url:
        row.append(InlineKeyboardButton(text=v.t("topup.open"), url=url))
    row.append(button(v.t("topup.paid"), PaidCb(pay=record_id)))
    kb.row(*row)


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


STEPS = ("method", "amount", "pay")


def _step(v: View, number: int) -> str:
    """«Step 2 of 3 · amount»: a top-up is the method, the amount, the payment."""
    what = v.t(f"topup.step.{STEPS[number - 1]}")
    return v.t("topup.step", n=number, total=len(STEPS), what=what)


def _head(v: View, data: AccountData, number: int) -> list[str]:
    lines = [v.t("topup.title", title=escape(data.title)), _step(v, number)]
    if data.balance is not None:
        lines.append(v.t("topup.balance", amount=data.balance.money))
    return lines


def _sbp_label(v: View, method: TopupMethod) -> str:
    if method.name.strip().upper() in ("СБП", "SBP"):
        return v.t("topup.sbp_plain")
    return v.t("topup.sbp", name=method.name)


def _method_line(v: View, method: TopupMethod) -> str:
    """«Method: SBP · minimum 100.00 ₽»."""
    line = v.t("topup.method", name=escape(method.name))
    if method.minimum is not None:
        line += " · " + v.t("topup.minimum", amount=method.minimum)
    return line


def _page_button(v: View, kb: InlineKeyboardBuilder, plugin: type[Plugin]) -> None:
    if plugin.topup_page:
        kb.row(InlineKeyboardButton(text=v.t("topup.page"), url=plugin.topup_page))


def _back_from_amount(choice: Choice, method: TopupMethod) -> TopupCb:
    acc = choice.data.account.id
    if method.kind is not TopupKind.CRYPTO or len(choice.crypto) < 2:
        return TopupCb(acc=acc)
    coin = method.coin or method.name
    if len(choice.coin_of(method)) > 1:
        return TopupCb(acc=acc, m=CRYPTO, c=coin)
    return TopupCb(acc=acc, m=CRYPTO, p=choice.page_of(coin))


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
        await show(hub, query, failed_screen(v, data, error, Nav(to="balance")))
        return
    if callback_data.m == CRYPTO:
        if callback_data.c:
            await show(hub, query, networks_screen(v, choice, callback_data.c))
        else:
            await show(hub, query, coins_screen(v, choice, callback_data.p))
        return
    if not callback_data.m:
        await show(hub, query, methods_screen(v, choice))
        return
    method = choice.method(callback_data.m)
    if method is None:
        await show(hub, query, methods_screen(v, choice))
        return
    await show(
        hub, query, amount_screen(v, choice, method, until_next_month(hub, choice.data, method))
    )


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
        await show(hub, query, failed_screen(v, hub.load(acc), error, Nav(to="balance")))
        return
    method = choice.method(callback_data.m)
    if method is None:
        await answer(query)
        await show(hub, query, methods_screen(v, choice))
        return
    back = TopupCb(acc=acc, m=method.id)
    if not callback_data.amt:
        await answer(query)
        await state.set_state(TopupAmount.amount)
        await state.update_data(acc=acc, method=method.id)
        await show(hub, query, ask_amount(v, method, back))
        return
    amount = parse_amount(callback_data.amt)
    if amount is None:
        await answer(query)
        return
    await answer(query, v.t("topup.creating"))
    await show(hub, query, Screen(v.t("topup.creating")))
    made = await create(hub, choice, method, Money(amount=amount, currency=_currency(method)))
    await show(hub, query, made.screen, made.tag)
    made.start_expiry(hub)


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
        await show(hub, message, failed_screen(v, hub.load(acc), error, Nav(to="balance")))
        return
    method = choice.method(method_id)
    if method is None:
        await state.clear()
        await show(hub, message, methods_screen(v, choice))
        return
    back = TopupCb(acc=acc, m=method.id)
    if amount is None:
        await show(hub, message, ask_amount(v, method, back))
        return
    if method.minimum is not None and amount < method.minimum.amount:
        ask = ask_amount(v, method, back)
        await show(hub, message, with_error(v.t("topup.below_minimum", amount=method.minimum), ask))
        return
    await state.clear()
    await show(hub, message, Screen(v.t("topup.creating")))
    made = await create(hub, choice, method, Money(amount=amount, currency=_currency(method)))
    await show(hub, message, made.screen, made.tag)
    made.start_expiry(hub)


def pay_tag(record_id: int) -> str:
    """The menu shows this invoice: «Paid» and its expiry change the screen by themselves."""
    return f"pay:{record_id}"


@dataclass(frozen=True)
class Invoice:
    """The screen after an invoice attempt; `record` is the `payments` row if one was made."""

    screen: Screen
    record: int | None = None
    expires_at: datetime | None = None

    @property
    def tag(self) -> str | None:
        return pay_tag(self.record) if self.record is not None else None

    def start_expiry(self, hub: Hub) -> None:
        if self.record is None or self.expires_at is None:
            return
        task = asyncio.create_task(
            expire(hub, self.record, self.expires_at), name=f"invoice-expiry-{self.record}"
        )
        hub.timers.add(task)
        task.add_done_callback(hub.timers.discard)


async def expire(hub: Hub, record_id: int, at: datetime) -> None:
    """When the invoice dies, its screen says so, unless it was paid or the menu moved on."""
    await asyncio.sleep(max((at - hub.now()).total_seconds(), 0))
    try:
        record = hub.payments.get(record_id)
    except NotFound:
        return
    if record.status != "created" or hub.menu is None:
        return
    v = view(hub)
    kb = InlineKeyboardBuilder()
    kb.row(button(v.t("topup.again"), TopupCb(acc=record.account_id)))
    kb.row(*nav(v, Nav(to="balance")))
    await hub.menu.update(pay_tag(record_id), Screen(v.t("topup.expired"), kb.as_markup()))


async def create(hub: Hub, choice: Choice, method: TopupMethod, amount: Money) -> Invoice:
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
        return Invoice(failed_screen(v, data, error, TopupCb(acc=acc, m=method.id)))
    record_id = hub.payments.add(acc, payment)
    _journal(hub, acc, {**params, "invoice": payment.id}, "invoice created", ok=True)
    if payment.options:
        remember_options(hub, record_id, payment.options)
        record = hub.payments.get(record_id)
        return Invoice(options_screen(v, data.title, record, payment.options), record_id)
    return Invoice(
        invoice_screen(v, data.title, method, payment, record_id), record_id, payment.expires_at
    )


def invoice_screen(
    v: View, title: str, method: TopupMethod, payment: Payment, record_id: int
) -> Screen:
    """The invoice: its link as a button, the QR code (if any) as the menu's picture."""
    qr = qr_png(payment.qr_data) if payment.qr_data else None
    markup = payment_markup(v, payment, record_id).as_markup()
    return Screen(payment_text(v, title, method, payment), markup, qr)


def remember_options(hub: Hub, record_id: int, options: Sequence[PayOption]) -> None:
    hub.topup_options[record_id] = tuple(options)
    while len(hub.topup_options) > KEPT_OPTIONS:
        del hub.topup_options[next(iter(hub.topup_options))]


def _payment_of(record: PaymentRecord) -> Payment:
    """The invoice as the plugin returned it, as far as `payments` keeps it."""
    link = record.url.startswith("https://")
    return Payment(
        id=record.external_id,
        amount=record.amount,
        method=record.method,
        created_at=record.created_at,
        url=record.url if link else None,
        address=None if link else record.url,
    )


@on("callback_query", PayOptCb.filter())
async def on_pay_option(query: CallbackQuery, callback_data: PayOptCb, hub: Hub) -> None:
    """Coins and networks of an invoice that is already made; a network gets the address."""
    v = view(hub)
    try:
        record = hub.payments.get(callback_data.pay)
        data = hub.load(record.account_id)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    acc = record.account_id
    payment = _payment_of(record)
    if not callback_data.o:
        await answer(query)
        options = hub.topup_options.get(record.id)
        if options is None:

            async def read(plugin: Plugin) -> list[PayOption]:
                if not isinstance(plugin, SupportsPayOptions):
                    raise NotImplementedError
                return await plugin.pay_options(payment)

            try:
                options = tuple(await hub.poller.runner.call(acc, read, track_status=False))
            except Exception as error:
                log.warning("coins of top-up %s: %s", record.id, describe(error))
                reason = escape(explain(describe(error), v.lang))
                await show(
                    hub,
                    query,
                    _choice_failed(v, record, v.t("topup.options_failed", reason=reason)),
                )
                return
            remember_options(hub, record.id, options)
        screen = options_screen(v, data.title, record, options, callback_data.c, callback_data.p)
        await show(hub, query, screen)
        return
    option_id = callback_data.o
    await answer(query, v.t("topup.getting_address"))
    await show(hub, query, Screen(v.t("topup.getting_address")))

    async def choose(plugin: Plugin) -> Payment:
        if not isinstance(plugin, SupportsPayOptions):
            raise NotImplementedError
        return await plugin.pay_with(payment, option_id)

    params = {"invoice": record.external_id, "option": option_id}
    try:
        paid = await hub.poller.runner.call(acc, choose, track_status=False)
    except Exception as error:
        log.warning("address of top-up %s failed: %s", record.id, describe(error))
        _journal(hub, acc, params, describe(error), ok=False)
        reason = escape(explain(describe(error), v.lang))
        screen = _choice_failed(v, record, v.t("topup.address_failed", reason=reason))
        await show(hub, query, screen)
        return
    _journal(hub, acc, params, "address received", ok=True)
    method = TopupMethod(
        id=record.method, name=_option_name(record, option_id, hub), kind=TopupKind.CRYPTO
    )
    made = Invoice(
        invoice_screen(v, data.title, method, paid, record.id), record.id, paid.expires_at
    )
    await show(hub, query, made.screen, made.tag)
    made.start_expiry(hub)


def _option_name(record: PaymentRecord, option_id: str, hub: Hub) -> str:
    options = hub.topup_options.get(record.id, ())
    option = next((o for o in options if o.id == option_id), None)
    return f"{option.coin} ({option.network})" if option is not None else record.method


def _choice_failed(v: View, record: PaymentRecord, text: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(button(v.t("topup.choose_coin"), PayOptCb(pay=record.id)))
    _pay_row(v, kb, record.url if record.url.startswith("https://") else None, record.id)
    kb.row(*nav(v, Nav(to="balance")))
    return Screen(text, kb.as_markup())


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
    text = "\n".join(
        [v.t("topup.invoice_title", title=escape(data.title)), v.t("topup.will_check")]
    )
    await show(hub, query, Screen(text, home_markup(v, Nav(to="balance"))), pay_tag(record.id))
    task = asyncio.create_task(watch_balance(hub, record.id), name=f"paid-{record.id}")
    hub.background.add(task)
    task.add_done_callback(hub.background.discard)


async def _tell(hub: Hub, record_id: int, text: str) -> None:
    """In the menu while it shows this invoice; else a notification of its own."""
    v = view(hub)
    if await menu(hub).update(pay_tag(record_id), Screen(text, home_markup(v, Nav(to="balance")))):
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=v.t("menu.balance"), callback_data=note(Nav(to="balance"))))
    await menu(hub).notify(text, kb.as_markup())


async def watch_balance(hub: Hub, record_id: int) -> None:
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
            lasts = money_lasts(v, hub.load(acc))
            if lasts:
                lines.append(v.t("topup.lasts", lasts=lasts))
            await _tell(hub, record_id, "\n".join(lines))
            return
    hub.payments.set_status(record_id, "not_seen")
    v = view(hub)
    minutes = int(hub.paid_checks[-1] // 60) if hub.paid_checks else 0
    await _tell(hub, record_id, v.t("topup.not_seen", title=escape(title), minutes=minutes))
