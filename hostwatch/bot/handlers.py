"""Bot handlers. Every update reaches them only after the owner check in `middleware.py`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.alerts import Thresholds
from hostwatch.core.logs import register_secrets
from hostwatch.core.models import AccountStatus
from hostwatch.core.plugin import Capability, browser_auth
from hostwatch.core.rates import Rates, RatesUnavailable
from hostwatch.storage.repositories import NotFound
from hostwatch.web.login import EndReason, LoginTarget, NotDone

from . import views
from .callbacks import (
    AccountCb,
    AddCb,
    HostCb,
    JournalCb,
    LabelCb,
    LoginCb,
    Nav,
    RefreshCb,
    ServerCb,
    SetCb,
)
from .hub import AccountData, Hub, key_fields
from .login import LOGIN_TAG, parse_cookie_text
from .menu import Menu, menu_buttons
from .prefs import THRESHOLD_BY_KEY, THRESHOLDS, valid_timezone
from .views import Screen, View

log = logging.getLogger(__name__)
Handler = Callable[..., Awaitable[Any]]
_ROUTES: list[tuple[str, Handler, tuple[Any, ...]]] = []


def on(event: str, *filters: Any) -> Callable[[Handler], Handler]:
    """Like `@on("message", ...)`, but each dispatcher gets a fresh router (tests build many)."""

    def register(handler: Handler) -> Handler:
        _ROUTES.append((event, handler, filters))
        return handler

    return register


def make_router() -> Router:
    router = Router(name="hostwatch")
    # «☰ Menu» goes first, before the questions that wait for text; the catch-all for text
    # goes last, whichever module registered its handlers first.
    order = {on_menu_button: 0, on_other_text: 2}
    routes = sorted(_ROUTES, key=lambda route: order.get(route[1], 1))
    for event, handler, filters in routes:
        getattr(router, event).register(handler, *filters)
    return router


# How long the bot waits for a poll it started before it shows what it has.
POLL_WAIT = 45.0


class AddAccount(StatesGroup):
    label = State()
    key = State()


class ChangeKey(StatesGroup):
    key = State()


class PasteCookies(StatesGroup):
    cookies = State()


class EditSetting(StatesGroup):
    timezone = State()
    threshold = State()


def view(hub: Hub) -> View:
    return View(hub.prefs.lang, hub.prefs.tz, hub.now())


def thresholds(hub: Hub) -> Thresholds:
    return hub.prefs.alert_settings(hub.own_ip).thresholds


def menu(hub: Hub) -> Menu:
    assert hub.menu is not None, "the menu middleware makes it with the first update"
    return hub.menu


async def show(
    hub: Hub, target: CallbackQuery | Message | None, screen: Screen, tag: str | None = None
) -> None:
    """Show a screen in the one menu (`menu.py`), whatever the owner pressed or typed.

    After a button press: unless the owner opened another menu since (a slow answer from a
    hoster does not take over the new menu). `tag` marks a screen that changes by itself."""
    if isinstance(target, CallbackQuery):
        message = target.message
        if message is not None and not menu(hub).follows(message.message_id):
            return
    await menu(hub).show(screen, tag)


async def show_below(hub: Hub, target: CallbackQuery | Message | None, screen: Screen) -> None:
    """The menu goes below a message the bot just sent (a money notice, a password, a file),
    showing `screen`; unless the owner opened another menu since."""
    if isinstance(target, CallbackQuery):
        message = target.message
        if message is not None and not menu(hub).follows(message.message_id):
            return
    await menu(hub).move_down(screen)


def with_error(text: str, screen: Screen) -> Screen:
    """The question again, with what was wrong with the answer above it."""
    return Screen(f"{text}\n\n{screen.text}", screen.markup, screen.photo)


def home_markup(v: View, back: CallbackData | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(*views._nav(v, back))
    return kb.as_markup()


async def answer(query: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    with contextlib.suppress(TelegramBadRequest):
        await query.answer(text, show_alert=alert)


# Screens


async def _rates(hub: Hub) -> Rates | None:
    try:
        return await hub.rates.get()
    except RatesUnavailable:
        return None


def _can_order(hub: Hub, accounts: list[AccountData]) -> bool:
    return any(
        Capability.ORDER in d.caps
        for d in accounts
        if d.account.status is not AccountStatus.DISABLED
    )


async def screen_for(hub: Hub, to: str) -> Screen:
    v = view(hub)
    th = thresholds(hub)
    if to == "problems":
        return views.problems_screen(v, hub.load_all(), th)
    if to == "servers":
        accounts = hub.load_all()
        return views.servers(v, accounts, th, _can_order(hub, accounts))
    if to == "balance":
        accounts = hub.load_all()
        rates = await _rates(hub)
        return views.balance(v, accounts, rates, hub.prefs.currency, th)
    if to == "money_info":
        return views.money_info(v, hub.load_all())
    if to == "integrations":
        return views.integrations(v, hub.load_all(), hub.plugins())
    if to == "connect":
        return views.connect(v, hub.plugins(), hub.load_all())
    if to == "journal":
        return journal_screen(hub, 0)
    if to == "settings":
        return settings_screen(hub)
    if to == "order":
        return await order.order_entry(hub)
    # «menu», and «summary» of the buttons sent before 1.1: the home screen.
    accounts = hub.load_all()
    return views.menu(v, accounts, await _rates(hub), hub.prefs.currency, th)


def journal_screen(hub: Hub, page: int) -> Screen:
    entries = hub.journal.recent(views.JOURNAL_PAGE + 1, page * views.JOURNAL_PAGE)
    has_more = len(entries) > views.JOURNAL_PAGE
    return views.journal(
        view(hub), entries[: views.JOURNAL_PAGE], page, hub.account_titles(), has_more
    )


def settings_screen(hub: Hub) -> Screen:
    thresholds = {item.key: hub.prefs.threshold(item.key) for item in THRESHOLDS}
    return views.settings(view(hub), hub.prefs.timezone, hub.prefs.currency, thresholds)


def host_screen(hub: Hub, account_id: int, page: int = 0) -> Screen | None:
    try:
        data = hub.load(account_id)
    except NotFound:
        return None
    return views.host_servers(view(hub), data, page, hub.own_ip, thresholds(hub))


def server_screen(hub: Hub, account_id: int, server_id: str) -> Screen | None:
    try:
        data = hub.load(account_id)
    except NotFound:
        return None
    server = next((s for s in data.servers if s.external_id == server_id), None)
    if server is None:
        return None
    own_days = Thresholds().own_extra_days if hub.is_own(server) else None
    usage = hub.usage_of(account_id, server_id)
    return views.server_card(view(hub), data, server, usage, own_days, thresholds(hub))


def account_screen(hub: Hub, account_id: int) -> Screen | None:
    try:
        return views.account_card(view(hub), hub.load(account_id))
    except NotFound:
        return None


# Start, menu and first run


async def home(hub: Hub) -> Screen:
    """The first screen of a new menu: the main menu, or the first-run questions."""
    if not hub.prefs.onboarded:
        return views.choose_language(view(hub), onb=True)
    return await screen_for(hub, "menu")


@on("message", CommandStart())
async def on_start(message: Message, hub: Hub, state: FSMContext) -> None:
    """A new menu at the bottom, and the «☰ Menu» keyboard again."""
    await state.clear()
    await menu(hub).open(await home(hub), keyboard=True)


@on("message", Command("menu"))
async def on_menu(message: Message, hub: Hub, state: FSMContext) -> None:
    await state.clear()
    await menu(hub).open(await home(hub))


@on("message", F.text.in_(menu_buttons()))
async def on_menu_button(message: Message, hub: Hub, state: FSMContext) -> None:
    """«☰ Menu» under the input field; a question the old menu asked is over."""
    await state.clear()
    await menu(hub).open(await home(hub))


@on("callback_query", Nav.filter())
async def on_nav(query: CallbackQuery, callback_data: Nav, hub: Hub, state: FSMContext) -> None:
    await state.clear()
    await answer(query)
    await show(hub, query, await screen_for(hub, callback_data.to))


@on("callback_query", SetCb.filter(F.key == "lang"))
async def on_language(query: CallbackQuery, callback_data: SetCb, hub: Hub) -> None:
    await answer(query)
    if not callback_data.value:
        await show(hub, query, views.choose_language(view(hub), onb=False))
        return
    hub.prefs.lang = callback_data.value
    if callback_data.onb:
        await show(hub, query, views.choose_timezone(view(hub), onb=True))
    else:
        await show(hub, query, settings_screen(hub))


@on("callback_query", SetCb.filter(F.key == "tz"))
async def on_timezone(
    query: CallbackQuery, callback_data: SetCb, hub: Hub, state: FSMContext
) -> None:
    await answer(query)
    v = view(hub)
    if not callback_data.value:
        await show(hub, query, views.choose_timezone(v, onb=False))
        return
    if callback_data.value == "other":
        await state.set_state(EditSetting.timezone)
        await state.update_data(onb=callback_data.onb)
        await show(hub, query, views.ask_timezone(v, callback_data.onb))
        return
    hub.prefs.timezone = callback_data.value
    await _after_timezone(query, hub, callback_data.onb)


async def _after_timezone(target: Message | CallbackQuery, hub: Hub, onb: bool) -> None:
    if onb:
        await show(hub, target, views.choose_currency(view(hub), onb=True))
    else:
        await show(hub, target, settings_screen(hub))


@on("message", EditSetting.timezone, F.text)
async def on_timezone_text(message: Message, hub: Hub, state: FSMContext) -> None:
    name = (message.text or "").strip()
    onb = bool((await state.get_data()).get("onb"))
    if not valid_timezone(name):
        v = view(hub)
        ask = views.ask_timezone(v, onb)
        await show(hub, message, with_error(v.t("settings.timezone_bad"), ask))
        return
    await state.clear()
    hub.prefs.timezone = name
    await _after_timezone(message, hub, onb)


@on("callback_query", SetCb.filter(F.key == "cur"))
async def on_currency(query: CallbackQuery, callback_data: SetCb, hub: Hub) -> None:
    await answer(query)
    v = view(hub)
    if not callback_data.value:
        await show(hub, query, views.choose_currency(v, onb=False))
        return
    hub.prefs.currency = callback_data.value
    if not callback_data.onb:
        await show(hub, query, settings_screen(hub))
        return
    hub.prefs.finish_onboarding()
    screen = views.connect(v, hub.plugins(), hub.load_all())
    await show(hub, query, Screen(f"{v.t('onboarding.done')}\n\n{screen.text}", screen.markup))


@on("callback_query", SetCb.filter(F.key == "th"))
async def on_threshold(
    query: CallbackQuery, callback_data: SetCb, hub: Hub, state: FSMContext
) -> None:
    await answer(query)
    key = callback_data.value
    if key not in THRESHOLD_BY_KEY:
        return
    await state.set_state(EditSetting.threshold)
    await state.update_data(key=key)
    await show(hub, query, views.ask_threshold(view(hub), key, hub.prefs.threshold(key)))


@on("message", EditSetting.threshold, F.text)
async def on_threshold_text(message: Message, hub: Hub, state: FSMContext) -> None:
    key = (await state.get_data()).get("key", "")
    if key not in THRESHOLD_BY_KEY:
        await state.clear()
        return
    if not hub.prefs.set_threshold(key, message.text or ""):
        v = view(hub)
        ask = views.ask_threshold(v, key, hub.prefs.threshold(key))
        await show(hub, message, with_error(v.t("threshold.bad"), ask))
        return
    await state.clear()
    await show(hub, message, settings_screen(hub))


@on("callback_query", SetCb.filter(F.key == "backup"))
async def on_backup(query: CallbackQuery, hub: Hub) -> None:
    v = view(hub)
    message = query.message
    if message is None or isinstance(message, InaccessibleMessage):
        await answer(query)
        return
    if hub.backups is None:
        await answer(query, v.t("backup.failed"), alert=True)
        return
    try:
        # The connection belongs to this thread; the database is small, so no executor.
        data = hub.backups.snapshot()
    except Exception:
        log.exception("backup for the owner failed")
        await answer(query, v.t("backup.failed"), alert=True)
        return
    await answer(query)
    now = hub.now()
    name = f"hostwatch-{now.astimezone(hub.prefs.tz):%Y%m%d-%H%M}.sqlite3"
    await message.answer_document(
        BufferedInputFile(data, filename=name), caption=v.t("backup.caption", date=v.moment(now))
    )
    # The file is not the menu: the menu goes below it.
    await show_below(hub, query, settings_screen(hub))


# Sections


@on("callback_query", HostCb.filter())
async def on_host(query: CallbackQuery, callback_data: HostCb, hub: Hub) -> None:
    screen = host_screen(hub, callback_data.acc, callback_data.page)
    if screen is None:
        await answer(query, view(hub).t("account.gone"), alert=True)
        await show(hub, query, await screen_for(hub, "servers"))
        return
    await answer(query)
    await show(hub, query, screen)


@on("callback_query", ServerCb.filter())
async def on_server(query: CallbackQuery, callback_data: ServerCb, hub: Hub) -> None:
    screen = server_screen(hub, callback_data.acc, callback_data.sid)
    if screen is None:
        await answer(query, view(hub).t("servers.gone"), alert=True)
        back = host_screen(hub, callback_data.acc)
        await show(hub, query, back or await screen_for(hub, "servers"))
        return
    await answer(query)
    await show(hub, query, screen)


@on("callback_query", JournalCb.filter())
async def on_journal_page(query: CallbackQuery, callback_data: JournalCb, hub: Hub) -> None:
    await answer(query)
    await show(hub, query, journal_screen(hub, max(callback_data.page, 0)))


@on("callback_query", RefreshCb.filter())
async def on_refresh(query: CallbackQuery, callback_data: RefreshCb, hub: Hub) -> None:
    v = view(hub)
    if callback_data.acc:
        targets = [callback_data.acc]
    else:
        targets = [a.id for a in hub.accounts.all() if a.status is not AccountStatus.DISABLED]
    done = {acc: hub.poller.poll_done(acc) for acc in targets}
    started = [acc for acc in targets if hub.poller.refresh(acc)]
    for acc in set(targets) - set(started):
        done[acc].cancel()
    if not started:
        await answer(query, v.t("refresh.too_often"), alert=True)
        return
    await answer(query, v.t("refresh.started"))
    await _wait(*(done[acc] for acc in started))
    if callback_data.view == "server":
        screen = server_screen(hub, callback_data.acc, callback_data.sid)
    elif callback_data.view == "host":
        page = int(callback_data.sid) if callback_data.sid.isdigit() else 0
        screen = host_screen(hub, callback_data.acc, page)
    elif callback_data.view == "account":
        screen = account_screen(hub, callback_data.acc)
    else:
        screen = await screen_for(hub, callback_data.view)
    await show(hub, query, screen or await screen_for(hub, "menu"))


async def _wait(*polls: asyncio.Future[None]) -> None:
    """Wait for polls to end, but not longer than POLL_WAIT: then show what there is."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.gather(*polls), POLL_WAIT)


# Integrations


@on("callback_query", AccountCb.filter())
async def on_account(
    query: CallbackQuery, callback_data: AccountCb, hub: Hub, state: FSMContext
) -> None:
    v = view(hub)
    acc, op = callback_data.acc, callback_data.op
    try:
        data = hub.load(acc)
    except NotFound:
        await answer(query, v.t("account.gone"), alert=True)
        await show(hub, query, await screen_for(hub, "integrations"))
        return
    if op == "relogin":
        if hub.login is None or browser_auth(data.plugin) is None:
            await answer(query)
            return
        await answer(query)
        login = hub.login.target(data.plugin, acc)
        await _send_link(query, hub, login, AccountCb(acc=acc, op="card"))
        return
    await answer(query)
    if op == "key":
        await state.set_state(ChangeKey.key)
        await state.update_data(acc=acc, fields=list(key_fields(data.plugin)), values={})
        field = key_fields(data.plugin)[0]
        await show(hub, query, views.ask_key(v, data.plugin.meta.name, field, _card(acc)))
        return
    if op == "disable":
        hub.poller.stop_account(acc)
        hub.accounts.set_status(acc, AccountStatus.DISABLED, actor="owner")
    elif op == "enable":
        hub.accounts.set_status(acc, AccountStatus.OK, actor="owner")
        hub.poller.start_account(acc)
    elif op == "delete":
        await show(hub, query, views.confirm_delete(v, data))
        return
    elif op == "delete_yes":
        hub.poller.stop_account(acc)
        hub.accounts.delete(acc)
        await show(hub, query, await screen_for(hub, "integrations"))
        return
    screen = account_screen(hub, acc)
    if screen is not None:
        await show(hub, query, screen)


@on("callback_query", AddCb.filter())
async def on_add(query: CallbackQuery, callback_data: AddCb, hub: Hub, state: FSMContext) -> None:
    v = view(hub)
    if callback_data.plugin not in hub.registry:
        await answer(query)
        return
    plugin = hub.registry.get(callback_data.plugin)
    if not key_fields(plugin) and (hub.login is None or browser_auth(plugin) is None):
        await answer(query, v.t("stub", stage=6), alert=True)
        return
    await answer(query)
    await state.set_state(AddAccount.label)
    await state.update_data(plugin=plugin.meta.id)
    await show(hub, query, views.ask_label(v, plugin.meta.name))


@on("callback_query", AddAccount.label, LabelCb.filter())
async def on_default_label(query: CallbackQuery, hub: Hub, state: FSMContext) -> None:
    await answer(query)
    plugin = hub.registry.get((await state.get_data())["plugin"])
    await _ask_first_key(query, hub, state, plugin.meta.name)


@on("message", AddAccount.label, F.text)
async def on_label(message: Message, hub: Hub, state: FSMContext) -> None:
    label = (message.text or "").strip()[:64]
    plugin = hub.registry.get((await state.get_data())["plugin"])
    await _ask_first_key(message, hub, state, label or plugin.meta.name)


async def _ask_first_key(
    target: Message | CallbackQuery, hub: Hub, state: FSMContext, label: str
) -> None:
    v = view(hub)
    plugin = hub.registry.get((await state.get_data())["plugin"])
    taken = {a.label for a in hub.accounts.all() if a.plugin_id == plugin.meta.id}
    if label in taken:
        ask = views.ask_label(v, plugin.meta.name)
        await show(hub, target, with_error(v.t("integrations.label_taken", label=label), ask))
        return
    fields = list(key_fields(plugin))
    if not fields:
        # No key to type: the owner logs in on the hoster's site instead.
        await state.clear()
        if hub.login is not None:
            await _send_link(target, hub, hub.login.target(plugin, None, label), Nav(to="connect"))
        return
    await state.set_state(AddAccount.key)
    await state.update_data(label=label, fields=fields, values={})
    await show(hub, target, views.ask_key(v, plugin.meta.name, fields[0]))


async def _take_secret(message: Message) -> str:
    """The key text. Its message is gone from the chat already (`menu.DeleteOwnerMessages`)."""
    value = (message.text or "").strip()
    register_secrets([value])
    return value


@on("message", AddAccount.key, F.text)
async def on_key(message: Message, hub: Hub, state: FSMContext) -> None:
    value = await _take_secret(message)
    data = await state.get_data()
    plugin = hub.registry.get(data["plugin"])
    values: dict[str, str] = {**data["values"], data["fields"][len(data["values"])]: value}
    if len(values) < len(data["fields"]):
        await state.update_data(values=values)
        await show(
            hub,
            message,
            views.ask_key(view(hub), plugin.meta.name, data["fields"][len(values)]),
        )
        return
    await state.clear()
    try:
        account_id = hub.accounts.add(plugin.meta.id, data["label"], values)
    except sqlite3.IntegrityError:
        v = view(hub)
        text = v.t("integrations.label_taken", label=data["label"])
        await show(hub, message, Screen(text, home_markup(v, Nav(to="connect"))))
        return
    await _connect(message, hub, account_id)


@on("message", ChangeKey.key, F.text)
async def on_new_key(message: Message, hub: Hub, state: FSMContext) -> None:
    value = await _take_secret(message)
    data = await state.get_data()
    acc = int(data["acc"])
    fields: list[str] = data["fields"]
    values: dict[str, str] = {**data["values"], fields[len(data["values"])]: value}
    if len(values) < len(fields):
        await state.update_data(values=values)
        plugin = hub.load(acc).plugin
        await show(
            hub,
            message,
            views.ask_key(view(hub), plugin.meta.name, fields[len(values)], _card(acc)),
        )
        return
    await state.clear()
    try:
        old = hub.accounts.load_secrets(acc)
    except NotFound:
        v = view(hub)
        await show(hub, message, Screen(v.t("account.gone"), home_markup(v)))
        return
    hub.accounts.save_secrets(acc, {**old, **values})
    hub.accounts.set_status(acc, AccountStatus.OK, actor="owner")
    hub.journal.note("owner", "login", acc, {"via": "key"})
    await _connect(message, hub, acc)


async def _connect(message: Message, hub: Hub, account_id: int) -> None:
    """Start polling the account and show its card once the first poll is done."""
    v = view(hub)
    await show(hub, message, Screen(v.t("integrations.checking")))
    done = hub.poller.poll_done(account_id)
    hub.poller.start_account(account_id)
    await _wait(done)
    screen = account_screen(hub, account_id)
    if screen is not None:
        await show(hub, message, screen)


# Browser login


def _card(acc: int) -> AccountCb:
    return AccountCb(acc=acc, op="card")


async def _send_link(
    target: Message | CallbackQuery, hub: Hub, login: LoginTarget, back: CallbackData
) -> None:
    """The link is a button in the menu; the screen changes by itself when the login is
    over or the link dies (`BotLogin`)."""
    assert hub.login is not None
    if isinstance(target, CallbackQuery):
        message = target.message
        if message is not None and not menu(hub).follows(message.message_id):
            return
    if hub.login.base_url() is None:
        v = view(hub)
        await show(hub, target, Screen(v.t("login.no_address"), home_markup(v, back)))
        return
    await hub.login.send_link(login, back, in_place=True)


@on("callback_query", LoginCb.filter(F.op == "kill"))
async def on_not_me(query: CallbackQuery, hub: Hub) -> None:
    await answer(query, view(hub).t("login.killed"))
    if hub.login is not None:
        await hub.login.service.end(EndReason.REJECTED)


@on("message", Command("cookie"))
async def on_cookie(message: Message, hub: Hub, state: FSMContext) -> None:
    await state.clear()
    v = view(hub)
    if hub.login is None:
        return
    arg = (message.text or "").partition(" ")[2].strip()
    if not arg:
        plugins = [p.meta.id for p in hub.plugins() if browser_auth(p) is not None]
        accounts = [
            f"{a.id} {a.label}"
            for a in hub.accounts.all()
            if a.plugin_id in hub.registry and browser_auth(hub.registry.get(a.plugin_id))
        ]
        text = v.t(
            "cookie.usage",
            plugins="|".join(plugins) or "-",
            accounts=escape(", ".join(accounts) or "-"),
        )
        await menu(hub).open(Screen(text, home_markup(v)))
        return
    login = hub.login.resolve(arg)
    if login is None:
        await menu(hub).open(Screen(v.t("cookie.unknown", target=escape(arg)), home_markup(v)))
        return
    await state.set_state(PasteCookies.cookies)
    await state.update_data(plugin=login.plugin_id, acc=login.account_id, label=login.label)
    # Tagged as a login: once the cookies are saved, `BotLogin` says so in this screen.
    await menu(hub).open(_cookie_ask(v, login), tag=LOGIN_TAG)


def _cookie_ask(v: View, login: LoginTarget) -> Screen:
    names = ", ".join(login.auth.cookies)
    text = v.t("cookie.ask", hoster=escape(login.hoster), names=escape(names))
    return Screen(text, home_markup(v))


@on("message", PasteCookies.cookies, F.text & ~F.text.startswith("/"))
async def on_cookie_text(message: Message, hub: Hub, state: FSMContext) -> None:
    v = view(hub)
    raw = await _take_secret(message)
    data = await state.get_data()
    if hub.login is None or data.get("plugin") not in hub.registry:
        await state.clear()
        return
    plugin = hub.registry.get(data["plugin"])
    login = hub.login.target(plugin, data.get("acc"), data.get("label", ""))
    parsed = parse_cookie_text(raw)
    ask = _cookie_ask(v, login)
    if not parsed:
        await menu(hub).show(with_error(v.t("cookie.bad"), ask), LOGIN_TAG)
        return
    register_secrets(parsed.values())
    cookies = {k: val for k, val in parsed.items() if login.auth.wants(k)}
    missing = login.auth.missing(cookies)
    if missing:
        text = v.t("cookie.missing", names=escape(", ".join(missing)))
        await menu(hub).show(with_error(text, ask), LOGIN_TAG)
        return
    await state.clear()
    try:
        await hub.login.store(login, cookies, via="cookie")
    except NotDone:
        await show(hub, message, Screen(v.t("web.notdone_check"), home_markup(v)))


@on("message")
async def on_other_text(message: Message, hub: Hub, state: FSMContext) -> None:
    """Anything else the owner sends: it is deleted, and a new menu comes at the bottom."""
    await state.clear()
    await menu(hub).open(await home(hub))


# Top-up and control handlers live in their own modules; importing them registers them.
from . import autopay, control, order, topup  # noqa: E402, F401
