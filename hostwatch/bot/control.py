"""Server card actions and paying invoices: what, where, how much → «Yes / Cancel» → the hoster.

«Yes» carries a one-time nonce (`core.control.Confirmations`), so a second press does
nothing. Every attempt goes to the journal with the hoster's answer, secrets masked.
An action with money always sends a message of its own, whatever its result.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from html import escape

from aiogram import F
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.control import Control, Request, journal_text, perform
from hostwatch.core.errors import AuthExpired, describe, explain
from hostwatch.core.models import ActionResult, Invoice, InvoiceStatus, Money, Server
from hostwatch.core.plugin import Plugin, SupportsInvoices, SupportsRenew, browser_auth
from hostwatch.storage.repositories import JournalEntry, NotFound

from .callbacks import AccountCb, ActionCb, ConfirmCb, InvoicesCb, Nav, PayCb, ServerCb
from .handlers import answer, on, server_screen, show, view
from .hub import AccountData, Hub
from .views import Screen, View, server_actions
from .views import _button as button
from .views import _nav as nav

log = logging.getLogger(__name__)

# Card buttons that belong to later stages.
LATER: dict[str, int] = {}
# How long the bot waits for the hoster before it says the result is unknown.
ACTION_TIMEOUT = 90.0


# Screens


def confirm_screen(v: View, data: AccountData, request: Request, nonce: str, what: str) -> Screen:
    lines = [v.t("control.confirm_title"), v.t("control.what", what=what)]
    lines.append(v.t("control.where", where=escape(_where(data, request))))
    lines.append(v.t("control.how_much", amount=_how_much(v, data, request)))
    kb = InlineKeyboardBuilder()
    kb.row(
        button(v.t("control.yes"), ConfirmCb(n=nonce, yes=True)),
        button(v.t("button.cancel"), ConfirmCb(n=nonce, yes=False)),
    )
    return Screen("\n".join(lines), kb.as_markup())


def result_screen(v: View, text: str, back: ServerCb | InvoicesCb) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(*nav(v, back))
    return Screen(text, kb.as_markup())


def failed_screen(
    v: View, data: AccountData, error: BaseException, back: ServerCb | InvoicesCb
) -> Screen:
    kb = InlineKeyboardBuilder()
    if isinstance(error, AuthExpired):
        text = v.t("control.need_login", title=escape(data.title))
        if browser_auth(data.plugin) is not None:
            kb.row(button(v.t("account.relogin"), AccountCb(acc=data.account.id, op="relogin")))
    else:
        text = v.t("control.cannot", reason=escape(explain(describe(error), v.lang)))
    kb.row(*nav(v, back))
    return Screen(text, kb.as_markup())


def invoices_screen(v: View, data: AccountData, invoices: list[Invoice]) -> Screen:
    acc = data.account.id
    unpaid = [i for i in invoices if i.status is InvoiceStatus.UNPAID]
    lines = [v.t("invoices.title", title=escape(data.title))]
    if data.balance is not None:
        lines.append(v.t("topup.balance", amount=data.balance.money))
    kb = InlineKeyboardBuilder()
    if not unpaid:
        lines += ["", v.t("invoices.none")]
    names = {s.external_id: s.name for s in data.servers}
    for invoice in unpaid:
        server = names.get(invoice.server_id or "")
        due = v.day(invoice.due_at) if invoice.due_at is not None else "—"
        lines.append("")
        lines.append(v.t("invoices.line", id=escape(invoice.id), amount=invoice.amount, due=due))
        if server is not None:
            lines.append(v.t("invoices.for_server", server=escape(server)))
            label = v.t("invoices.pay", id=invoice.id, amount=invoice.amount)
            kb.row(button(label, PayCb(acc=acc, inv=invoice.id)))
        else:
            # Top-up invoices and upgrades: the balance cannot pay a top-up, and the rest
            # is better looked at in the cabinet.
            lines.append(v.t("invoices.not_service"))
    kb.row(*nav(v, Nav(to="balance")))
    return Screen("\n".join(lines), kb.as_markup())


def _where(data: AccountData, request: Request) -> str:
    server = _server(data, request.server_id)
    if server is None:
        return data.title
    ip = f", {server.ip}" if server.ip else ""
    return f"{server.name} · {data.title}{ip}"


def _how_much(v: View, data: AccountData, request: Request) -> str:
    balance = data.balance.money if data.balance is not None else None
    if request.amount is not None:
        if balance is not None and balance.currency == request.amount.currency:
            return v.t("control.charge_with_balance", amount=request.amount, balance=balance)
        return v.t("control.charge", amount=request.amount)
    server = _server(data, request.server_id)
    if request.action is Control.AUTORENEW_ON and server is not None and server.price:
        return v.t("control.later_charges", price=_price(v, server))
    return v.t("control.no_charge")


def _price(v: View, server: Server) -> str:
    assert server.price is not None
    if server.period_months in (None, 1):
        return v.t("price.month", amount=server.price)
    return v.t("price.months", amount=server.price, months=server.period_months)


def _server(data: AccountData, server_id: str) -> Server | None:
    return next((s for s in data.servers if s.external_id == server_id), None)


def _what(v: View, request: Request) -> str:
    if request.action is Control.PAY_INVOICE:
        return v.t("control.what.pay_invoice", id=escape(request.invoice_id))
    return v.t(f"control.what.{request.action.value}")


# Handlers


# Autopay marks have their own handler in `autopay.py`; orders and passwords in `order.py`.
@on("callback_query", ActionCb.filter(F.act.not_in({"autopay", "order_same", "password"})))
async def on_action(query: CallbackQuery, callback_data: ActionCb, hub: Hub) -> None:
    v = view(hub)
    if callback_data.act in LATER:
        await answer(query, v.t("stub", stage=LATER[callback_data.act]), alert=True)
        return
    acc, sid = callback_data.acc, callback_data.sid
    back = ServerCb(acc=acc, sid=sid)
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    server = _server(data, sid)
    if server is None or callback_data.act not in server_actions(data, server):
        # A button from an old screen: the server or its state changed since.
        await answer(query, v.t("control.unavailable"), alert=True)
        screen = server_screen(hub, acc, sid)
        if screen is not None:
            await show(query, screen)
        return
    action = Control(callback_data.act)
    amount = None
    if action is Control.RENEW:
        await answer(query, v.t("control.asking_price"))
        try:
            amount = await _call(hub, acc, lambda p: _renew_price(p, sid))
        except Exception as error:
            log.warning("renewal price of account %s: %s", acc, describe(error))
            await show(query, failed_screen(v, data, error, back))
            return
    else:
        await answer(query)
    request = Request(action=action, account_id=acc, server_id=sid, amount=amount)
    nonce = hub.confirmations.issue(request)
    await show(query, confirm_screen(v, data, request, nonce, _what(v, request)))


@on("callback_query", InvoicesCb.filter())
async def on_invoices(query: CallbackQuery, callback_data: InvoicesCb, hub: Hub) -> None:
    v = view(hub)
    acc = callback_data.acc
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    await answer(query, v.t("invoices.loading"))
    try:
        invoices = await _call(hub, acc, _invoices)
    except Exception as error:
        await show(query, failed_screen(v, data, error, InvoicesCb(acc=acc)))
        return
    await show(query, invoices_screen(v, data, invoices))


@on("callback_query", PayCb.filter())
async def on_pay(query: CallbackQuery, callback_data: PayCb, hub: Hub) -> None:
    """Read the invoice again right before asking: the owner confirms the current sum."""
    v = view(hub)
    acc = callback_data.acc
    back = InvoicesCb(acc=acc)
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    await answer(query)
    try:
        invoices = await _call(hub, acc, _invoices)
    except Exception as error:
        await show(query, failed_screen(v, data, error, back))
        return
    invoice = next((i for i in invoices if i.id == callback_data.inv), None)
    if invoice is None or invoice.status is not InvoiceStatus.UNPAID or not invoice.server_id:
        await show(query, result_screen(v, v.t("invoices.not_payable"), back))
        return
    request = Request(
        action=Control.PAY_INVOICE,
        account_id=acc,
        server_id=invoice.server_id,
        invoice_id=invoice.id,
        amount=invoice.amount,
    )
    nonce = hub.confirmations.issue(request)
    await show(query, confirm_screen(v, data, request, nonce, _what(v, request)))


@on("callback_query", ConfirmCb.filter())
async def on_confirm(query: CallbackQuery, callback_data: ConfirmCb, hub: Hub) -> None:
    v = view(hub)
    request = hub.confirmations.take(callback_data.n)
    if request is None:
        # Pressed twice, too late, or after a restart: nothing is done.
        await answer(query, v.t("control.expired"), alert=True)
        return
    back = _back(request)
    if not callback_data.yes:
        await answer(query, v.t("control.cancelled"))
        screen = server_screen(hub, request.account_id, request.server_id)
        if request.action is Control.PAY_INVOICE or screen is None:
            screen = result_screen(v, v.t("control.cancelled"), back)
        await show(query, screen)
        return
    try:
        data = hub.load(request.account_id)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    what = _what(v, request)
    await answer(query, v.t("control.running"))
    await show(query, Screen(v.t("control.running_what", what=what)))
    text = await execute(hub, data, request, what)
    await show(query, result_screen(v, text, back))
    message = query.message
    # A message of its own for every action with money (spec, decision 5): it cannot be
    # switched off and stays in the chat when the screen above is edited away.
    if request.action.spends_money and isinstance(message, Message):
        await message.answer(v.t("control.money_notice", text=text))


async def execute(hub: Hub, data: AccountData, request: Request, what: str) -> str:
    """Run a confirmed action, write the journal and mark own stops.
    Returns the text of the result for the owner."""
    v = view(hub)
    acc = request.account_id
    where = escape(_where(data, request))
    try:
        result: ActionResult = await asyncio.wait_for(
            hub.poller.runner.call(acc, lambda p: perform(p, request), track_status=False),
            ACTION_TIMEOUT,
        )
    except TimeoutError:
        _journal(hub, request, "timeout", ok=False)
        return v.t("control.timeout", what=what, where=where)
    except Exception as error:
        log.warning("%s on account %s failed: %s", request.action, acc, describe(error))
        _journal(hub, request, describe(error), ok=False)
        reason = escape(explain(describe(error), v.lang))
        return v.t("control.failed", what=what, where=where, reason=reason)
    _journal(hub, request, result.detail, ok=result.ok)
    if result.ok and request.action.stops_server:
        hub.owner_stops.mark(acc, request.server_id, hub.now())
    elif result.ok and request.action is Control.START:
        hub.owner_stops.clear(acc, request.server_id)
    hub.poller.refresh(acc)
    detail = escape(journal_text(result.detail)) or "—"
    key = "control.done" if result.ok else "control.unclear"
    return v.t(key, what=what, where=where, detail=detail)


def _journal(hub: Hub, request: Request, response: str, ok: bool) -> None:
    hub.journal.add(
        JournalEntry(
            at=hub.now(),
            actor="owner",
            account_id=request.account_id,
            action=request.action.value,
            params=request.params(),
            response=journal_text(response),
            ok=ok,
        )
    )


def _back(request: Request) -> ServerCb | InvoicesCb:
    if request.action is Control.PAY_INVOICE:
        return InvoicesCb(acc=request.account_id)
    return ServerCb(acc=request.account_id, sid=request.server_id)


async def _call[T](hub: Hub, acc: int, action: Callable[[Plugin], Awaitable[T]]) -> T:
    return await asyncio.wait_for(
        hub.poller.runner.call(acc, action, track_status=False), ACTION_TIMEOUT
    )


async def _renew_price(plugin: Plugin, server_id: str) -> Money:
    if not isinstance(plugin, SupportsRenew):
        raise NotImplementedError
    return await plugin.renew_price(server_id)


async def _invoices(plugin: Plugin) -> list[Invoice]:
    if not isinstance(plugin, SupportsInvoices):
        raise NotImplementedError
    return await plugin.invoices()
