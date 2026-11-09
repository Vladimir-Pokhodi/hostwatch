"""Ordering a server (stage 13, spec decision 11): account → location → plan → OS → period →
summary with the price the hoster asks now → «Yes» → second «Yes» above the monthly limit →
the order. «Order the same» on a server card starts with what the plugin tells about that
server; «Show password» asks the hoster for the root password.

The catalog is cached for an hour (`CatalogCache`); the price is asked again before the
summary and once more right before the order (`core.order.place`). A root password is sent
in a message of its own that is deleted after five minutes; it never reaches the database,
the journal or the logs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import ROUND_CEILING
from html import escape

from aiogram import F
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.catalog import (
    HOURLY,
    Catalog,
    OrderRequest,
    OrderResult,
    OrderTemplate,
    Plan,
    first_charge,
)
from hostwatch.core.control import journal_text
from hostwatch.core.errors import AuthExpired, describe, explain
from hostwatch.core.logs import register_secrets
from hostwatch.core.models import AccountStatus, Money, TopupMethod
from hostwatch.core.order import (
    PASSWORD_TTL,
    Draft,
    LimitCheck,
    PriceChanged,
    check_limit,
    month_start,
    parse_limit,
    parse_ssh_key,
    place,
    server_name,
    shortfall,
)
from hostwatch.core.plugin import (
    Capability,
    Plugin,
    SupportsOrder,
    SupportsRootPassword,
    SupportsTopup,
    browser_auth,
)
from hostwatch.core.rates import RatesUnavailable
from hostwatch.storage.repositories import JournalEntry, NotFound

from .callbacks import AccountCb, ActionCb, Nav, ServerCb, SetCb, TopupAmountCb
from .handlers import answer, menu, on, server_screen, show, show_below, view, with_error
from .hub import AccountData, Hub
from .views import Screen, View
from .views import _button as button
from .views import _nav as nav

log = logging.getLogger(__name__)

# How long the bot waits for the hoster; as for server actions (`control.ACTION_TIMEOUT`).
ACTION_TIMEOUT = 90.0
# Long lists are cut: a Telegram message holds about 4000 characters.
MAX_PLANS = 40
JOURNAL_PAGE = 200
ORDER = "order"
ROOT_PASSWORD = "root_password"


class OrderCb(CallbackData, prefix="o", sep="|"):
    """One step of the order. Empty fields are the steps still to take; `same` is the server
    whose plan «Order the same» copies."""

    acc: int = 0
    loc: str = ""
    plan: str = ""
    os: str = ""
    per: int = -1
    same: str = ""


class OrderConfirmCb(CallbackData, prefix="oc"):
    """«Yes» or «Cancel» under an order; `n` is the one-time nonce of the draft."""

    n: str
    yes: bool


class OrderSettings(StatesGroup):
    limit = State()
    key = State()


# Screens


def accounts_screen(v: View, accounts: list[AccountData]) -> Screen:
    kb = InlineKeyboardBuilder()
    lines = [v.t("order.title_any")]
    if not accounts:
        lines.append(v.t("order.no_accounts"))
    else:
        lines.append(v.t("order.choose_account"))
    for data in accounts:
        kb.row(button(data.title, OrderCb(acc=data.account.id)))
    kb.row(*nav(v, Nav(to="servers")))
    return Screen("\n".join(lines), kb.as_markup())


def locations_screen(v: View, data: AccountData, catalog: Catalog, single: bool) -> Screen:
    acc = data.account.id
    lines = [_title(v, data), v.t("order.choose_location")]
    kb = InlineKeyboardBuilder()
    for location in catalog.used_locations():
        cheapest = catalog.plans_in(location.id)[0].cheapest_month
        label = v.t("order.location_button", name=location.name, amount=cheapest)
        kb.button(text=label, callback_data=OrderCb(acc=acc, loc=location.id).pack())
    kb.adjust(2)
    if not catalog.plans:
        lines.append(v.t("order.empty"))
    kb.row(*nav(v, Nav(to="servers") if single else Nav(to="order")))
    return Screen("\n".join(lines), kb.as_markup())


def plans_screen(v: View, data: AccountData, catalog: Catalog, loc: str) -> Screen:
    acc = data.account.id
    location = catalog.location(loc)
    name = location.name if location else loc
    lines = [_title(v, data), v.t("order.location", name=escape(name))]
    lines.append(v.t("order.choose_plan"))
    kb = InlineKeyboardBuilder()
    plans = catalog.plans_in(loc)
    for plan in plans[:MAX_PLANS]:
        cheapest = plan.cheapest_month
        lines.append(
            v.t(
                "order.plan_line",
                name=escape(plan.name),
                specs=escape(plan.specs or "—"),
                amount=cheapest,
            )
        )
        label = v.t("order.plan_button", name=plan.name, amount=cheapest)
        _add(kb, label, OrderCb(acc=acc, loc=loc, plan=plan.id))
    if len(plans) > MAX_PLANS:
        lines.append(v.t("order.more_plans", count=len(plans) - MAX_PLANS))
    kb.row(*nav(v, OrderCb(acc=acc)))
    return Screen("\n".join(lines), kb.as_markup())


def systems_screen(v: View, data: AccountData, catalog: Catalog, plan: Plan) -> Screen:
    acc, loc = data.account.id, plan.location_id
    lines = [*_head(v, data, catalog, plan), v.t("order.choose_os")]
    kb = InlineKeyboardBuilder()
    buttons = 0
    for system in catalog.systems_for(plan):
        cb = OrderCb(acc=acc, loc=loc, plan=plan.id, os=system.id)
        if _fits(cb):
            kb.button(text=system.name, callback_data=cb.pack())
            buttons += 1
    kb.adjust(2)
    if not buttons:
        lines.append(v.t("order.no_os"))
    kb.row(*nav(v, OrderCb(acc=acc, loc=loc)))
    return Screen("\n".join(lines), kb.as_markup())


def periods_screen(v: View, data: AccountData, catalog: Catalog, plan: Plan, os_id: str) -> Screen:
    acc, loc = data.account.id, plan.location_id
    lines = [*_head(v, data, catalog, plan, os_id), v.t("order.choose_period")]
    kb = InlineKeyboardBuilder()
    for price in sorted(plan.prices, key=lambda p: p.period_months or 10**6):
        months = price.period_months
        if months == HOURLY:
            label = v.t("order.period_hourly", amount=price.amount)
        else:
            label = v.t("order.period_months", months=months, amount=price.amount)
        kb.row(button(label, OrderCb(acc=acc, loc=loc, plan=plan.id, os=os_id, per=months)))
    kb.row(*nav(v, OrderCb(acc=acc, loc=loc, plan=plan.id)))
    return Screen("\n".join(lines), kb.as_markup())


def summary_screen(
    v: View,
    data: AccountData,
    catalog: Catalog,
    draft: Draft,
    listed: Money,
    balance: Money | None,
    check: LimitCheck,
    nonce: str,
) -> Screen:
    request = draft.request
    plan = catalog.plan(request.plan_id)
    assert plan is not None
    lines = [*_head(v, data, catalog, plan, request.os_id)]
    lines.append(v.t("order.period", period=_period(v, request.period_months)))
    lines.append(v.t("order.price", price=_price(v, request.price, request.period_months)))
    if request.price != listed:
        lines.append(v.t("order.price_changed", old=listed, new=request.price))
    if balance is not None:
        lines.append(v.t("order.balance", amount=balance))
    lines.append(_key_line(v, catalog, request))
    lines.append(_limit_line(v, check))
    if not catalog.tested:
        lines.append(v.t("order.untested"))
    lines += [v.t("order.ask", price=_price(v, request.price, request.period_months))]
    kb = InlineKeyboardBuilder()
    kb.row(
        button(v.t("control.yes"), OrderConfirmCb(n=nonce, yes=True)),
        button(v.t("button.cancel"), OrderConfirmCb(n=nonce, yes=False)),
    )
    return Screen("\n".join(lines), kb.as_markup())


def second_screen(v: View, draft: Draft, check: LimitCheck, nonce: str) -> Screen:
    request = draft.request
    lines = [
        v.t("order.second_title"),
        v.t("order.what", what=escape(draft.what)),
        v.t("order.price", price=_price(v, request.price, request.period_months)),
        _limit_line(v, check),
        v.t("order.second_ask"),
    ]
    kb = InlineKeyboardBuilder()
    kb.row(
        button(v.t("order.second_yes"), OrderConfirmCb(n=nonce, yes=True)),
        button(v.t("button.cancel"), OrderConfirmCb(n=nonce, yes=False)),
    )
    return Screen("\n".join(lines), kb.as_markup())


def shortfall_screen(
    v: View,
    data: AccountData,
    missing: Money,
    need: Money,
    balance: Money,
    methods: list[TopupMethod],
    back: OrderCb,
) -> Screen:
    acc = data.account.id
    lines = [
        _title(v, data),
        v.t("order.shortfall", need=need, balance=balance, missing=missing),
    ]
    kb = InlineKeyboardBuilder()
    for method in methods:
        amount = topup_amount(missing, method)
        if amount is None:
            continue
        label = v.t("order.topup_button", amount=amount, name=method.name)
        _add(kb, label, TopupAmountCb(acc=acc, m=method.id, amt=format(amount.amount, "f")))
    if methods:
        lines.append(v.t("order.topup_hint"))
    else:
        lines.append(v.t("order.topup_none"))
    kb.row(button(v.t("order.check_again"), back))
    kb.row(*nav(v, OrderCb(acc=acc, loc=back.loc, plan=back.plan, os=back.os)))
    return Screen("\n".join(lines), kb.as_markup())


def settings_screen(v: View, limit: Money, key: str | None) -> Screen:
    lines = [
        v.t("order.settings.title"),
        v.t("order.settings.limit", amount=limit),
        v.t("order.settings.limit_note"),
        v.t(
            "order.settings.key",
            name=escape(key_label(key)) if key else v.t("order.settings.no_key"),
        ),
    ]
    kb = InlineKeyboardBuilder()
    kb.row(button(v.t("order.settings.button.limit"), SetCb(key="order", value="limit")))
    kb.row(button(v.t("order.settings.button.key"), SetCb(key="order", value="key")))
    if key:
        kb.row(button(v.t("order.settings.button.no_key"), SetCb(key="order", value="nokey")))
    kb.row(*nav(v, Nav(to="settings")))
    return Screen("\n".join(lines), kb.as_markup())


def failed_screen(v: View, data: AccountData, error: BaseException, back: CallbackData) -> Screen:
    kb = InlineKeyboardBuilder()
    if isinstance(error, AuthExpired):
        text = v.t("control.need_login", title=escape(data.title))
        if browser_auth(data.plugin) is not None:
            kb.row(button(v.t("account.relogin"), AccountCb(acc=data.account.id, op="relogin")))
    else:
        text = v.t("control.cannot", reason=escape(explain(describe(error), v.lang)))
    kb.row(*nav(v, back))
    return Screen(text, kb.as_markup())


def key_label(key: str) -> str:
    """«ssh-ed25519 …AbCdEf12 owner@pc»: enough to recognise the key."""
    parts = key.split()
    tail = parts[1][-12:] if len(parts) > 1 else ""
    return " ".join(x for x in (parts[0], f"…{tail}", *parts[2:3]) if x)


def topup_amount(missing: Money, method: TopupMethod) -> Money | None:
    """The missing sum rounded up to whole units, at least the method's minimum."""
    amount = missing.amount.to_integral_value(rounding=ROUND_CEILING)
    if method.minimum is not None:
        if method.minimum.currency != missing.currency:
            return None
        amount = max(amount, method.minimum.amount)
    return Money(amount=amount, currency=missing.currency)


def _title(v: View, data: AccountData) -> str:
    return v.t("order.title", title=escape(data.title))


def _head(
    v: View, data: AccountData, catalog: Catalog, plan: Plan, os_id: str | None = None
) -> list[str]:
    location = catalog.location(plan.location_id)
    lines = [
        _title(v, data),
        v.t("order.location", name=escape(location.name if location else plan.location_id)),
        v.t("order.plan", name=escape(plan.name), specs=escape(plan.specs or "—")),
    ]
    if os_id is not None:
        system = catalog.system(os_id)
        lines.append(v.t("order.os", name=escape(system.name if system else os_id)))
    return lines


def _period(v: View, months: int) -> str:
    return v.t("order.hourly") if months == HOURLY else v.t("order.months", months=months)


def _price(v: View, price: Money, months: int) -> str:
    if months == HOURLY:
        return v.t("order.price_hourly", amount=price, hour=first_charge(price, HOURLY))
    if months == 1:
        return v.t("price.month", amount=price)
    return v.t("price.months", amount=price, months=months)


def _key_line(v: View, catalog: Catalog, request: OrderRequest) -> str:
    if not catalog.ssh_keys:
        return v.t("order.key_not_supported")
    if request.ssh_key:
        return v.t("order.key_set", name=escape(key_label(request.ssh_key)))
    return v.t("order.key_missing")


def _limit_line(v: View, check: LimitCheck) -> str:
    spent = check.spent if check.spent is not None else "?"
    key = "order.limit_over" if check.over else "order.limit_ok"
    return v.t(key, spent=spent, limit=check.limit)


def _fits(cb: CallbackData) -> bool:
    """Telegram takes at most 64 bytes of button data."""
    try:
        cb.pack()
    except ValueError:
        return False
    return True


def _add(kb: InlineKeyboardBuilder, label: str, cb: CallbackData) -> None:
    if _fits(cb):
        kb.row(button(label, cb))


def _describe(catalog: Catalog, request: OrderRequest) -> str:
    """«MSKs-1 · Shared, Москва, Ubuntu 24.04» for the journal and the second question."""
    plan = catalog.plan(request.plan_id)
    location = catalog.location(plan.location_id) if plan else None
    system = catalog.system(request.os_id)
    parts = [
        plan.name if plan else request.plan_id,
        location.name if location else "",
        system.name if system else request.os_id,
    ]
    return ", ".join(p for p in parts if p)


# Data


async def _call[T](hub: Hub, acc: int, action: Callable[[Plugin], Awaitable[T]]) -> T:
    return await asyncio.wait_for(
        hub.poller.runner.call(acc, action, track_status=False), ACTION_TIMEOUT
    )


def _orderable(hub: Hub) -> list[AccountData]:
    return [
        d
        for d in hub.load_all()
        if Capability.ORDER in d.caps and d.account.status is not AccountStatus.DISABLED
    ]


async def _catalog(hub: Hub, acc: int) -> Catalog:
    async def load(plugin: Plugin) -> Catalog:
        if not isinstance(plugin, SupportsOrder):
            raise NotImplementedError
        return await plugin.catalog()

    return await hub.catalogs.get(acc, lambda: _call(hub, acc, load))


def order_limit(hub: Hub) -> Money:
    return Money(amount=hub.prefs.order_limit, currency=hub.prefs.currency)


def spent_this_month(hub: Hub) -> list[Money]:
    """Orders of this calendar month from the journal, in their own currencies. An order
    that timed out counts too: it may have gone through."""
    since = month_start(hub.now(), hub.prefs.tz)
    found: list[Money] = []
    offset = 0
    while True:
        entries = hub.journal.recent(JOURNAL_PAGE, offset)
        for entry in entries:
            if entry.at < since:
                return found
            counted = entry.ok or entry.response == "timeout"
            if entry.action == ORDER and counted and "amount" in entry.params:
                with contextlib.suppress(ValueError, ArithmeticError):
                    found.append(Money.of(entry.params["amount"], entry.params["currency"]))
        if len(entries) < JOURNAL_PAGE:
            return found
        offset += JOURNAL_PAGE


async def limit_check(hub: Hub, price: Money) -> LimitCheck:
    try:
        rates = await hub.rates.get()
    except RatesUnavailable:
        rates = None

    def convert(money: Money, currency: str) -> Money | None:
        return rates.convert(money, currency) if rates is not None else None

    return check_limit(order_limit(hub), spent_this_month(hub), price, convert)


# Handlers: the steps


async def order_entry(hub: Hub) -> Screen:
    """«Order» in the menu: the accounts that can order."""
    return accounts_screen(view(hub), _orderable(hub))


@on("callback_query", OrderCb.filter())
async def on_order(query: CallbackQuery, callback_data: OrderCb, hub: Hub) -> None:
    v = view(hub)
    cb = callback_data
    accounts = _orderable(hub)
    single = len(accounts) == 1
    if not cb.acc:
        if not single:
            await answer(query)
            await show(hub, query, accounts_screen(v, accounts))
            return
        cb = OrderCb(acc=accounts[0].account.id)
    try:
        data = hub.load(cb.acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    if Capability.ORDER not in data.caps:
        await answer(query, v.t("control.unavailable"), alert=True)
        return
    await answer(query, v.t("order.loading") if cb.per >= 0 or cb.same else None)
    try:
        catalog = await _catalog(hub, cb.acc)
    except Exception as error:
        log.warning("catalog of account %s: %s", cb.acc, describe(error))
        await show(hub, query, failed_screen(v, data, error, Nav(to="order")))
        return
    if cb.same:
        cb = await _same(hub, data, catalog, cb.same)
    await step(query, hub, data, catalog, cb, single)


async def _same(hub: Hub, data: AccountData, catalog: Catalog, server_id: str) -> OrderCb:
    """The deepest step «Order the same» can fill from what the plugin tells."""
    acc = data.account.id

    async def ask(plugin: Plugin) -> OrderTemplate:
        if not isinstance(plugin, SupportsOrder):
            raise NotImplementedError
        return await plugin.order_template(server_id)

    try:
        template = await _call(hub, acc, ask)
    except Exception as error:
        log.warning("order template of account %s: %s", acc, describe(error))
        template = OrderTemplate()
    plan = catalog.plan(template.plan_id) if template.plan_id else None
    loc = plan.location_id if plan else template.location_id or ""
    if catalog.location(loc) is None:
        return OrderCb(acc=acc)
    if plan is None:
        return OrderCb(acc=acc, loc=loc)
    systems = {s.id for s in catalog.systems_for(plan)}
    os_id = template.os_id if template.os_id in systems else ""
    per = template.period_months
    if not os_id or per is None or plan.price(per) is None:
        return OrderCb(acc=acc, loc=loc, plan=plan.id, os=os_id)
    return OrderCb(acc=acc, loc=loc, plan=plan.id, os=os_id, per=per)


async def step(
    query: CallbackQuery,
    hub: Hub,
    data: AccountData,
    catalog: Catalog,
    cb: OrderCb,
    single: bool,
) -> None:
    v = view(hub)
    if not cb.loc or catalog.location(cb.loc) is None:
        await show(hub, query, locations_screen(v, data, catalog, single))
        return
    plan = catalog.plan(cb.plan) if cb.plan else None
    if plan is None or plan.location_id != cb.loc:
        await show(hub, query, plans_screen(v, data, catalog, cb.loc))
        return
    if not cb.os or cb.os not in {s.id for s in catalog.systems_for(plan)}:
        await show(hub, query, systems_screen(v, data, catalog, plan))
        return
    listed = plan.price(cb.per) if cb.per >= 0 else None
    if listed is None:
        await show(hub, query, periods_screen(v, data, catalog, plan, cb.os))
        return
    await show(hub, query, Screen(v.t("order.asking_price")))
    await summary(query, hub, data, catalog, cb, plan, listed)


async def summary(
    query: CallbackQuery,
    hub: Hub,
    data: AccountData,
    catalog: Catalog,
    cb: OrderCb,
    plan: Plan,
    listed: Money,
) -> None:
    """Ask the hoster the price and read the balance, then show «Order for …?»."""
    v = view(hub)
    acc = data.account.id
    back = OrderCb(acc=acc, loc=cb.loc, plan=cb.plan, os=cb.os)
    key = hub.prefs.ssh_key if catalog.ssh_keys else None
    request = OrderRequest(
        plan_id=plan.id,
        os_id=cb.os,
        period_months=cb.per,
        name=server_name(hub.now().astimezone(hub.prefs.tz)),
        price=listed,
        ssh_key=key,
    )

    async def ask(plugin: Plugin) -> Money:
        if not isinstance(plugin, SupportsOrder):
            raise NotImplementedError
        return await plugin.quote(request)

    try:
        quoted = await _call(hub, acc, ask)
    except Exception as error:
        log.warning("order price at account %s: %s", acc, describe(error))
        await show(hub, query, failed_screen(v, data, error, back))
        return
    if quoted != listed:
        hub.catalogs.forget(acc)  # the next steps read the new prices
    request = request.model_copy(update={"price": quoted})
    try:
        balance: Money | None = (
            await asyncio.wait_for(hub.poller.read_balance(acc), ACTION_TIMEOUT)
        ).money
    except Exception as error:
        log.warning("balance before an order at account %s: %s", acc, describe(error))
        balance = data.balance.money if data.balance is not None else None
    need = first_charge(quoted, request.period_months)
    missing = shortfall(balance, need) if balance is not None else None
    if missing is not None and balance is not None:
        methods = await _topup_methods(hub, acc)
        await show(hub, query, shortfall_screen(v, data, missing, need, balance, methods, cb))
        return
    draft = Draft(account_id=acc, request=request, what=_describe(catalog, request))
    check = await limit_check(hub, quoted)
    nonce = hub.order_confirmations.issue(draft)
    await show(hub, query, summary_screen(v, data, catalog, draft, listed, balance, check, nonce))


async def _topup_methods(hub: Hub, acc: int) -> list[TopupMethod]:
    async def methods(plugin: Plugin) -> list[TopupMethod]:
        if not isinstance(plugin, SupportsTopup):
            return []
        return await plugin.topup_methods()

    try:
        return await _call(hub, acc, methods)
    except Exception as error:
        log.warning("top-up methods of account %s: %s", acc, describe(error))
        return []


# Handlers: confirmation and the order


@on("callback_query", OrderConfirmCb.filter())
async def on_order_confirm(query: CallbackQuery, callback_data: OrderConfirmCb, hub: Hub) -> None:
    v = view(hub)
    draft = hub.order_confirmations.take(callback_data.n)
    if draft is None:
        await answer(query, v.t("control.expired"), alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.row(*nav(v))
    if not callback_data.yes:
        await answer(query, v.t("control.cancelled"))
        await show(hub, query, Screen(v.t("order.cancelled"), kb.as_markup()))
        return
    try:
        data = hub.load(draft.account_id)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    if not draft.above_limit_confirmed:
        check = await limit_check(hub, draft.request.price)
        if check.over:
            await answer(query)
            nonce = hub.order_confirmations.issue(replace(draft, above_limit_confirmed=True))
            await show(hub, query, second_screen(v, draft, check, nonce))
            return
    await answer(query, v.t("order.running"))
    await show(hub, query, Screen(v.t("order.running")))
    text, password = await execute(hub, data, draft)
    # Every order is an action with money: a message of its own, which cannot be switched off.
    await menu(hub).notify(v.t("control.money_notice", text=text))
    if password:
        await send_password(hub, draft.request.name, password)
    await show_below(hub, query, Screen(text, kb.as_markup()))


async def execute(hub: Hub, data: AccountData, draft: Draft) -> tuple[str, str | None]:
    """Place the order once, write the journal. Returns the text for the owner and the root
    password if the hoster told it."""
    v = view(hub)
    acc = draft.account_id
    request = draft.request
    what = escape(f"{draft.what} · {data.title}")
    price = _price(v, request.price, request.period_months)

    async def run(plugin: Plugin) -> OrderResult:
        if not isinstance(plugin, SupportsOrder):
            raise NotImplementedError
        return await place(plugin, request)

    try:
        result = await asyncio.wait_for(
            hub.poller.runner.call(acc, run, track_status=False), ACTION_TIMEOUT
        )
    except TimeoutError:
        _journal(hub, draft, "timeout", ok=False)
        return v.t("order.timeout", what=what), None
    except PriceChanged as error:
        hub.catalogs.forget(acc)
        _journal(hub, draft, describe(error), ok=False)
        return v.t("order.price_moved", what=what, old=request.price, new=error.now), None
    except Exception as error:
        log.warning("order at account %s failed: %s", acc, describe(error))
        _journal(hub, draft, describe(error), ok=False)
        reason = escape(explain(describe(error), v.lang))
        return v.t("order.failed", what=what, reason=reason), None
    extra = {}
    if result.server_id:
        extra["server"] = result.server_id
    if result.invoice_id:
        extra["invoice"] = result.invoice_id
    _journal(hub, draft, result.detail, ok=result.ok, extra=extra)
    hub.poller.refresh(acc)
    detail = escape(journal_text(result.detail)) or "—"
    if not result.ok:
        return v.t("order.unclear", what=what, detail=detail), result.password
    if result.invoice_id:
        text = v.t("order.invoice", what=what, invoice=escape(result.invoice_id), detail=detail)
        return text, result.password
    return v.t("order.done", what=what, price=price, detail=detail), result.password


def _journal(
    hub: Hub, draft: Draft, response: str, ok: bool, extra: dict[str, str] | None = None
) -> None:
    request = draft.request
    params = {
        "plan": request.plan_id,
        "os": request.os_id,
        "period": str(request.period_months),
        "name": request.name,
        "what": draft.what,
        "amount": str(request.price.amount),
        "currency": request.price.currency,
        **(extra or {}),
    }
    if draft.above_limit_confirmed:
        params["above_limit"] = "yes"
    hub.journal.add(
        JournalEntry(
            at=hub.now(),
            actor="owner",
            account_id=draft.account_id,
            action=ORDER,
            params=params,
            response=journal_text(response),
            ok=ok,
        )
    )


# Root password


async def send_password(hub: Hub, server: str, password: str) -> None:
    """The password in a message of its own (not the menu), deleted after PASSWORD_TTL."""
    register_secrets([password])
    v = view(hub)
    minutes = max(int(PASSWORD_TTL // 60), 1)
    sent = await menu(hub).notify(
        v.t(
            "order.password",
            server=escape(server),
            password=escape(password),
            minutes=minutes,
        )
    )
    if sent is None:
        return
    task = asyncio.create_task(_delete_later(hub, sent), name=f"password-{sent}")
    hub.timers.add(task)
    task.add_done_callback(hub.timers.discard)


async def _delete_later(hub: Hub, message_id: int) -> None:
    await asyncio.sleep(PASSWORD_TTL)
    await menu(hub).delete(message_id)


@on("callback_query", ActionCb.filter(F.act == "password"))
async def on_password(query: CallbackQuery, callback_data: ActionCb, hub: Hub) -> None:
    v = view(hub)
    acc, sid = callback_data.acc, callback_data.sid
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        return
    server = next((s for s in data.servers if s.external_id == sid), None)
    if server is None or Capability.ROOT_PASSWORD not in data.caps:
        await answer(query, v.t("control.unavailable"), alert=True)
        return
    await answer(query, v.t("order.password_asking"))

    async def ask(plugin: Plugin) -> str:
        if not isinstance(plugin, SupportsRootPassword):
            raise NotImplementedError
        return await plugin.root_password(sid)

    try:
        password = await _call(hub, acc, ask)
    except Exception as error:
        log.warning("root password at account %s: %s", acc, describe(error))
        _password_journal(hub, acc, sid, describe(error), ok=False)
        await show(hub, query, failed_screen(v, data, error, ServerCb(acc=acc, sid=sid)))
        return
    _password_journal(hub, acc, sid, "shown", ok=True)
    await send_password(hub, server.name, password)
    card = server_screen(hub, acc, sid)
    if card is not None:
        await show_below(hub, query, card)


def _password_journal(hub: Hub, acc: int, sid: str, response: str, ok: bool) -> None:
    """That the password was asked for; the password itself never goes here."""
    hub.journal.add(
        JournalEntry(
            at=hub.now(),
            actor="owner",
            account_id=acc,
            action=ROOT_PASSWORD,
            params={"server": sid},
            response=journal_text(response),
            ok=ok,
        )
    )


@on("callback_query", ActionCb.filter(F.act == "order_same"))
async def on_order_same(query: CallbackQuery, callback_data: ActionCb, hub: Hub) -> None:
    await on_order(query, OrderCb(acc=callback_data.acc, same=callback_data.sid), hub)


# Settings: the monthly limit and the SSH key


def _ask(v: View, text: str) -> Screen:
    back = InlineKeyboardBuilder()
    back.row(*nav(v, SetCb(key="order")))
    return Screen(text, back.as_markup())


def _settings(hub: Hub) -> Screen:
    return settings_screen(view(hub), order_limit(hub), hub.prefs.ssh_key)


@on("callback_query", SetCb.filter(F.key == "order"))
async def on_order_settings(
    query: CallbackQuery, callback_data: SetCb, hub: Hub, state: FSMContext
) -> None:
    v = view(hub)
    await answer(query)
    back = InlineKeyboardBuilder()
    back.row(*nav(v, SetCb(key="order")))
    if callback_data.value == "limit":
        await state.set_state(OrderSettings.limit)
        text = v.t("order.settings.ask_limit", currency=hub.prefs.currency)
        await show(hub, query, Screen(text, back.as_markup()))
        return
    if callback_data.value == "key":
        await state.set_state(OrderSettings.key)
        await show(hub, query, Screen(v.t("order.settings.ask_key"), back.as_markup()))
        return
    if callback_data.value == "nokey":
        hub.prefs.ssh_key = None
    await state.clear()
    await show(hub, query, _settings(hub))


@on("message", OrderSettings.limit, F.text & ~F.text.startswith("/"))
async def on_limit_text(message: Message, hub: Hub, state: FSMContext) -> None:
    amount = parse_limit(message.text or "")
    if amount is None:
        v = view(hub)
        ask = _ask(v, v.t("order.settings.ask_limit", currency=hub.prefs.currency))
        await show(hub, message, with_error(v.t("order.settings.bad_limit"), ask))
        return
    await state.clear()
    hub.prefs.order_limit = amount
    await show(hub, message, _settings(hub))


@on("message", OrderSettings.key, F.text & ~F.text.startswith("/"))
async def on_key_text(message: Message, hub: Hub, state: FSMContext) -> None:
    v = view(hub)
    text = message.text or ""
    ask = _ask(v, v.t("order.settings.ask_key"))
    if "PRIVATE KEY" in text:
        # A private key must not stay in the chat: its message is gone already
        # (`menu.DeleteOwnerMessages`), like every message of the owner.
        await show(hub, message, with_error(v.t("order.settings.private_key"), ask))
        return
    key = parse_ssh_key(text)
    if key is None:
        await show(hub, message, with_error(v.t("order.settings.bad_key"), ask))
        return
    await state.clear()
    hub.prefs.ssh_key = key
    await show(hub, message, _settings(hub))
