"""Autopay in the bot (stage 11): the mark on a server card, the rule settings, the reports
of the hourly job and the owner's answer to a held invoice.

Nothing pays by itself after a deploy: no server is marked and every account's monthly
limit is zero until the owner sets them here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.autopay import MAX_DAYS, Verdict, eligible, month_start
from hostwatch.core.control import Control, Request
from hostwatch.core.errors import explain
from hostwatch.core.models import InvoiceStatus, Money
from hostwatch.core.plugin import Capability
from hostwatch.scheduler.autopay import Event, Outcome
from hostwatch.storage.autopay import APPROVED, DECLINED
from hostwatch.storage.repositories import NotFound

from .callbacks import ActionCb, InvoicesCb, Nav, SetCb, TopupCb, note
from .control import _call, _invoices, execute, failed_screen, result_screen
from .handlers import answer, menu, on, server_screen, show, show_below, view, with_error
from .hub import AccountData, Hub
from .views import Screen, View
from .views import _button as button
from .views import _nav as nav

log = logging.getLogger(__name__)

MAX_LIMIT = Decimal(1_000_000)
ASKS = {Verdict.ASK_GROWN.value, Verdict.ASK_NEW.value}
# `app.Owner.send`: a message to the owner.
Sender = Callable[[str, InlineKeyboardMarkup | None], Awaitable[object]]


class AutopayCb(CallbackData, prefix="ap", sep="|"):
    """The owner's answer to an invoice autopay held: `op` is «pay» or «no»."""

    acc: int
    inv: str
    op: str


class AutopayInput(StatesGroup):
    days = State()
    limit = State()


# Screens


def payable_accounts(hub: Hub) -> list[AccountData]:
    """Accounts whose plugin can read and pay invoices: only they can have autopay."""
    need = {Capability.INVOICES, Capability.PAY_INVOICE}
    return [data for data in hub.load_all() if need <= data.caps]


def currency_of(data: AccountData) -> str:
    return data.balance.money.currency if data.balance is not None else data.plugin.meta.currency


def settings_screen(hub: Hub) -> Screen:
    v = view(hub)
    store = hub.autopay
    lines = [v.t("autopay.settings.title"), "", v.t("autopay.settings.about")]
    kb = InlineKeyboardBuilder()
    if store is None:
        lines += ["", v.t("autopay.settings.off")]
        kb.row(*nav(v, Nav(to="settings")))
        return Screen("\n".join(lines), kb.as_markup())
    days = store.days()
    lines += ["", v.t("autopay.settings.days", days=days)]
    accounts = payable_accounts(hub)
    if not accounts:
        lines += ["", v.t("autopay.settings.no_accounts")]
    since = month_start(hub.now(), hub.prefs.tz)
    for data in accounts:
        acc = data.account.id
        currency = currency_of(data)
        limit = store.limit(acc, currency)
        zero = Money(amount=Decimal(0), currency=currency)
        spent = store.ledger(acc, currency, since).spent or zero
        lines.append(
            v.t(
                "autopay.settings.account",
                title=escape(data.title),
                limit=limit,
                spent=spent,
                count=len(data.autopay),
            )
        )
        if limit.amount == 0:
            lines.append(v.t("autopay.settings.zero_limit"))
    kb.row(button(v.t("autopay.settings.button.days", days=days), SetCb(key="ap", value="days")))
    for data in accounts:
        label = v.t("autopay.settings.button.limit", title=data.title)
        kb.row(button(label, SetCb(key="ap", value=f"lim{data.account.id}")))
    kb.row(*nav(v, Nav(to="settings")))
    return Screen("\n".join(lines), kb.as_markup())


def report_text(v: View, data: AccountData | None, event: Event, spent: Money | None) -> str:
    decision = event.decision
    invoice = decision.invoice
    title = escape(data.title) if data is not None else "—"
    server = escape(decision.server.name) if decision.server is not None else "—"
    common = {"id": escape(invoice.id), "amount": invoice.amount, "server": server, "title": title}
    if event.outcome is Outcome.PAID:
        text = v.t("autopay.report.paid", detail=escape(event.detail) or "—", **common)
        if spent is not None:
            text += "\n" + v.t("autopay.report.limit", spent=spent, limit=event.rule.limit)
        return v.t("control.money_notice", text=text)
    if event.outcome is Outcome.UNCLEAR:
        text = v.t("autopay.report.unclear", detail=escape(event.detail) or "—", **common)
        return v.t("control.money_notice", text=text)
    if event.outcome is not None:
        reason = escape(explain(event.detail, v.lang))
        text = v.t("autopay.report.failed", reason=reason, **common)
        return v.t("control.money_notice", text=text)
    verdict = decision.verdict
    if verdict is Verdict.ASK_GROWN:
        return v.t("autopay.report.grown", reference=decision.reference, **common)
    if verdict is Verdict.ASK_NEW:
        return v.t("autopay.report.new", **common)
    if verdict is Verdict.OVER_LIMIT:
        return v.t(
            "autopay.report.over_limit",
            limit=event.rule.limit,
            spent=spent if spent is not None else "—",
            **common,
        )
    missing = invoice.amount - event.balance
    return v.t("autopay.report.low_balance", balance=event.balance, missing=missing, **common)


def _note(text: str, data: CallbackData) -> InlineKeyboardButton:
    """A button under a notification: it opens a new menu below (`menu.MenuGuard`)."""
    return InlineKeyboardButton(text=text, callback_data=note(data))


def report_markup(v: View, event: Event) -> InlineKeyboardMarkup | None:
    acc = event.account_id
    verdict = event.decision.verdict
    kb = InlineKeyboardBuilder()
    if event.outcome is None and verdict.asks:
        inv = event.decision.invoice.id
        kb.row(
            _note(v.t("autopay.button.pay"), AutopayCb(acc=acc, inv=inv, op="pay")),
            _note(v.t("autopay.button.skip"), AutopayCb(acc=acc, inv=inv, op="no")),
        )
        return kb.as_markup()
    if event.outcome is None and verdict is Verdict.OVER_LIMIT:
        kb.row(_note(v.t("autopay.settings.button"), SetCb(key="ap")))
    if event.outcome is None and verdict is Verdict.LOW_BALANCE:
        kb.row(_note(v.t("autopay.button.topup"), TopupCb(acc=acc)))
    if event.outcome is not Outcome.PAID:
        kb.row(_note(v.t("autopay.button.invoices"), InvoicesCb(acc=acc)))
        return kb.as_markup()
    return None


class Reporter:
    """Turns autopay events into messages to the owner. Money messages cannot be
    switched off (spec, decision 5)."""

    def __init__(self, hub: Hub, send: Sender) -> None:
        self._hub = hub
        self._send = send

    async def __call__(self, event: Event) -> None:
        hub = self._hub
        v = view(hub)
        try:
            data: AccountData | None = hub.load(event.account_id)
        except NotFound:
            data = None
        spent = None
        if hub.autopay is not None:
            since = month_start(hub.now(), hub.prefs.tz)
            spent = hub.autopay.ledger(event.account_id, event.balance.currency, since).spent
        await self._send(report_text(v, data, event, spent), report_markup(v, event))


# Handlers


@on("callback_query", ActionCb.filter(F.act == "autopay"))
async def on_mark(query: CallbackQuery, callback_data: ActionCb, hub: Hub) -> None:
    """Mark or unmark a server for autopay. Marking pays nothing now: the hourly job does,
    within the monthly limit, which is zero until the owner sets it."""
    v = view(hub)
    acc, sid = callback_data.acc, callback_data.sid
    store = hub.autopay
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    server = next((s for s in data.servers if s.external_id == sid), None)
    if store is None or server is None or not eligible(data.caps, server):
        await answer(query, v.t("control.unavailable"), alert=True)
    else:
        on_now = sid not in data.autopay
        store.mark(acc, sid, on_now, hub.now())
        if not on_now:
            text = v.t("autopay.marked_off", server=server.name)
        elif store.limit(acc, currency_of(data)).amount == 0:
            text = v.t("autopay.marked_on_no_limit", server=server.name)
        else:
            limit = store.limit(acc, currency_of(data))
            text = v.t("autopay.marked_on", server=server.name, days=store.days(), limit=limit)
        await answer(query, text, alert=True)
    screen = server_screen(hub, acc, sid)
    if screen is not None:
        await show(hub, query, screen)


@on("callback_query", SetCb.filter(F.key == "ap"))
async def on_settings(
    query: CallbackQuery, callback_data: SetCb, hub: Hub, state: FSMContext
) -> None:
    await answer(query)
    v = view(hub)
    value = callback_data.value
    back = SetCb(key="ap")
    kb = InlineKeyboardBuilder()
    kb.row(*nav(v, back))
    if value == "days" and hub.autopay is not None:
        await state.set_state(AutopayInput.days)
        text = v.t("autopay.ask_days", days=hub.autopay.days(), max=MAX_DAYS)
        await show(hub, query, Screen(text, kb.as_markup()))
        return
    if value.startswith("lim") and value[3:].isdigit() and hub.autopay is not None:
        acc = int(value[3:])
        data = next((d for d in payable_accounts(hub) if d.account.id == acc), None)
        if data is not None:
            await state.set_state(AutopayInput.limit)
            await state.update_data(acc=acc)
            limit = hub.autopay.limit(acc, currency_of(data))
            text = v.t("autopay.ask_limit", title=escape(data.title), limit=limit)
            await show(hub, query, Screen(text, kb.as_markup()))
            return
    await state.clear()
    await show(hub, query, settings_screen(hub))


@on("message", AutopayInput.days, F.text)
async def on_days_text(message: Message, hub: Hub, state: FSMContext) -> None:
    text = (message.text or "").strip()
    store = hub.autopay
    if store is None:
        await state.clear()
        return
    if not text.isdigit() or not 1 <= int(text) <= MAX_DAYS:
        v = view(hub)
        ask = _ask(v, v.t("autopay.ask_days", days=store.days(), max=MAX_DAYS))
        await show(hub, message, with_error(v.t("autopay.bad_days", max=MAX_DAYS), ask))
        return
    store.set_days(int(text))
    await state.clear()
    await show(hub, message, settings_screen(hub))


@on("message", AutopayInput.limit, F.text)
async def on_limit_text(message: Message, hub: Hub, state: FSMContext) -> None:
    store = hub.autopay
    acc = (await state.get_data()).get("acc")
    if store is None or not isinstance(acc, int):
        await state.clear()
        return
    amount = parse_limit(message.text or "")
    if amount is None:
        v = view(hub)
        data = next((d for d in payable_accounts(hub) if d.account.id == acc), None)
        limit = store.limit(acc, currency_of(data)) if data is not None else None
        title = escape(data.title) if data is not None else "—"
        ask = _ask(v, v.t("autopay.ask_limit", title=title, limit=limit or "—"))
        await show(hub, message, with_error(v.t("autopay.bad_limit"), ask))
        return
    store.set_limit(acc, amount)
    await state.clear()
    await show(hub, message, settings_screen(hub))


def _ask(v: View, text: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(*nav(v, SetCb(key="ap")))
    return Screen(text, kb.as_markup())


def parse_limit(text: str) -> Decimal | None:
    """«15», «15.50», «15,5»: zero or more, at most a million, in whole cents."""
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_LIMIT:
        return None
    if amount != amount.quantize(Decimal("0.01")):
        return None
    return amount


@on("callback_query", AutopayCb.filter())
async def on_owner_answer(query: CallbackQuery, callback_data: AutopayCb, hub: Hub) -> None:
    """«Pay» or «Don't pay» under an invoice autopay held. «Pay» pays the sum the owner
    saw in the message; if the hoster asks for another one now, nothing is paid."""
    v = view(hub)
    acc, inv = callback_data.acc, callback_data.inv
    back = InvoicesCb(acc=acc)
    store = hub.autopay
    if store is None:
        await answer(query, v.t("control.expired"), alert=True)
        return
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    if callback_data.op != "pay":
        if store.take_notice(acc, inv, ASKS, DECLINED):
            await answer(query, v.t("control.cancelled"))
            await show(hub, query, result_screen(v, v.t("autopay.declined", id=escape(inv)), back))
        else:
            await answer(query, v.t("control.expired"), alert=True)
        return
    if not store.take_notice(acc, inv, ASKS, APPROVED):
        # Pressed twice, or answered already.
        await answer(query, v.t("control.expired"), alert=True)
        return
    notice = store.notice(acc, inv)
    assert notice is not None
    await answer(query, v.t("control.running"))
    try:
        invoices = await _call(hub, acc, _invoices)
    except Exception as error:
        await show(hub, query, failed_screen(v, data, error, back))
        return
    invoice = next((i for i in invoices if i.id == inv), None)
    if invoice is None or invoice.status is not InvoiceStatus.UNPAID or not invoice.server_id:
        await show(hub, query, result_screen(v, v.t("invoices.not_payable"), back))
        return
    if invoice.amount != notice.amount:
        # The sum changed again: the next run asks about the new one.
        store.put_notice(acc, inv, "changed", invoice.amount, hub.now())
        await show(
            hub, query, result_screen(v, v.t("autopay.changed", amount=invoice.amount), back)
        )
        return
    request = Request(
        action=Control.PAY_INVOICE,
        account_id=acc,
        server_id=invoice.server_id,
        invoice_id=invoice.id,
        amount=notice.amount,
    )
    what = v.t("control.what.pay_invoice", id=escape(inv))
    await show(hub, query, Screen(v.t("control.running_what", what=what)))
    text = await execute(hub, data, request, what)
    await menu(hub).notify(v.t("control.money_notice", text=text))
    await show_below(hub, query, result_screen(v, text, back))
