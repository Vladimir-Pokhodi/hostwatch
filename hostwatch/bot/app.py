"""Builds the bot and runs it together with the polling scheduler in one process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.alerts import Message as AlertMessage
from hostwatch.core.alerts import Reason, render
from hostwatch.core.config import Settings
from hostwatch.core.http import make_client
from hostwatch.core.logs import register_secrets
from hostwatch.core.rates import CbrRates, RatesUnavailable
from hostwatch.core.registry import Registry, discover
from hostwatch.locales import t
from hostwatch.scheduler.alerts import AlertService, DailySummary
from hostwatch.scheduler.autopay import AutopayJob
from hostwatch.scheduler.maintenance import Backups, ReleaseCheck, every, ping, public_ip
from hostwatch.scheduler.polling import Poller, PollEvent
from hostwatch.storage.autopay import AutopayStore
from hostwatch.storage.db import connect
from hostwatch.storage.history import HistoryRepository
from hostwatch.storage.repositories import (
    AccountRepository,
    AlertRepository,
    BalanceHistoryRepository,
    JournalRepository,
    OwnerStopRepository,
    PaymentRepository,
    ServerRepository,
    SettingsRepository,
    UsageRepository,
)
from hostwatch.web.browser import remote_launcher
from hostwatch.web.headless import HeadlessPages
from hostwatch.web.login import LoginEvents, LoginService
from hostwatch.web.miniapp import MiniApp
from hostwatch.web.server import serve, serve_admin

from . import views, webapp
from .autopay import Reporter
from .handlers import home, make_router
from .hub import Hub
from .login import BotLogin
from .menu import DeleteOwnerMessages, Menu, MenuGuard
from .middleware import OwnerOnly
from .prefs import Prefs
from .views import View

log = logging.getLogger(__name__)

DB_NAME = "hostwatch.sqlite3"
MINUTE = 60.0
DAY = 24 * 60 * MINUTE
HEARTBEAT_EVERY = 5 * MINUTE
AUTOPAY_EVERY = 60 * MINUTE
MESSAGE_LIMIT = 4000
ALIVE_FILE = Path("/tmp/hostwatch.alive")


def current_version() -> str:
    try:
        return version("hostwatch")
    except PackageNotFoundError:
        return "0"


def build_dispatcher(hub: Hub, owner_id: int) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), hub=hub)
    dp.update.outer_middleware(OwnerOnly(owner_id))
    # The one menu (`menu.py`): the owner's messages go, old menus answer «closed».
    dp.message.outer_middleware(DeleteOwnerMessages())
    dp.callback_query.outer_middleware(MenuGuard(home))
    dp.include_router(make_router())
    return dp


def bot_properties() -> DefaultBotProperties:
    return DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True)


async def make_bot(token: str, proxy: str | None) -> Bot:
    """Through TG_PROXY when it is set and works; otherwise straight to Telegram."""
    if proxy:
        bot = Bot(token, session=AiohttpSession(proxy=proxy), default=bot_properties())
        try:
            me = await bot.get_me()
        except Exception as exc:
            log.warning("TG_PROXY does not work (%s); connecting directly", type(exc).__name__)
            await bot.session.close()
        else:
            log.info("connected to Telegram through TG_PROXY as @%s", me.username)
            return bot
    bot = Bot(token, default=bot_properties())
    try:
        me = await bot.get_me()
    except BaseException:
        await bot.session.close()
        raise
    log.info("connected to Telegram as @%s", me.username)
    return bot


async def _log_event(event: PollEvent) -> None:
    # The owner learns about these from the alert rules, which read the account status.
    log.warning("account %s: %s (%s)", event.account_id, event.kind.value, event.detail)


class Owner:
    """Sends messages to the owner. Until the owner presses /start, Telegram answers
    «chat not found»; that is logged once, not every minute."""

    def __init__(self, bot: Bot, owner_id: int) -> None:
        self._bot = bot
        self._owner_id = owner_id
        self._failing = False

    async def send(self, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
        return await self.post(text, markup) is not None

    async def post(self, text: str, markup: InlineKeyboardMarkup | None = None) -> int | None:
        """Send a message; the id of its last part, or None if it did not go through."""
        try:
            chunks = split_text(text)
            message_id = None
            for n, chunk in enumerate(chunks):
                last = n == len(chunks) - 1
                message = await self._bot.send_message(
                    self._owner_id, chunk, reply_markup=markup if last else None
                )
                message_id = message.message_id
        except TelegramAPIError as exc:
            if not self._failing:
                log.warning("cannot message the owner: %s", exc.message)
            self._failing = True
            return None
        if self._failing:
            log.info("messages reach the owner again")
        self._failing = False
        return message_id

    async def delete(self, message_id: int) -> bool:
        """False when Telegram refuses, e.g. for messages older than 48 hours."""
        try:
            return await self._bot.delete_message(self._owner_id, message_id)
        except TelegramAPIError:
            return False

    async def edit(self, message_id: int, text: str) -> bool:
        try:
            await self._bot.edit_message_text(text, chat_id=self._owner_id, message_id=message_id)
        except TelegramAPIError:
            return False
        return True


def split_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Telegram takes up to 4096 characters a message; split between lines."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        while len(line) > limit:
            line, rest = line[:limit], line[limit:]
            chunks.extend(["\n".join(current), line] if current else [line])
            current, size, line = [], 0, rest
        if current and size + 1 + len(line) > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        size += len(line) + (1 if current else 0)
        current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def autopay_job(hub: Hub, runner: AccountRunner, owner: Owner, registry: Registry) -> AutopayJob:
    """The hourly autopay run (stage 11); it reports to the owner through `owner`."""
    assert hub.autopay is not None
    return AutopayJob(
        runner,
        hub.accounts,
        hub.servers,
        hub.autopay,
        registry,
        Reporter(hub, owner.send),
        lambda: hub.prefs.tz,
        refresh=hub.poller.refresh,
    )


def alert_markup(hub: Hub, keys: list[str]) -> InlineKeyboardMarkup | None:
    """Buttons under an alert message: each opens a new menu on its screen; a message
    without one (a «resolved») gets «🏠 Open the menu»."""
    accounts = hub.load_all()
    titles = {d.account.id: d.title for d in accounts}
    servers = {(d.account.id, s.external_id): s.name for d in accounts for s in d.servers}
    v = View(hub.prefs.lang, hub.prefs.tz, hub.now())
    return views.alert_buttons(v, keys, titles, servers)


def touch_alive() -> None:
    """The container health check looks at this file's age."""
    ALIVE_FILE.touch()


async def run(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.data_dir / DB_NAME)
    registry = discover(settings.plugins_dir)
    accounts = AccountRepository(conn, settings.encryption_key)
    launcher = remote_launcher(settings.browser_url)
    login: list[BotLogin] = []

    def browser_busy() -> bool:
        return bool(login) and login[0].service.busy()

    runner = AccountRunner(
        accounts, registry, make_client, pages=HeadlessPages(launcher, browser_busy)
    )
    history = HistoryRepository(conn)
    poller = Poller(
        runner,
        accounts,
        ServerRepository(conn),
        UsageRepository(conn),
        BalanceHistoryRepository(conn),
        _log_event,
        history=history,
    )
    settings_repo = SettingsRepository(conn)
    prefs = Prefs(settings_repo)
    if settings.heartbeat_url:
        register_secrets([settings.heartbeat_url])
    async with make_client("cbr") as rates_http, make_client("hostwatch") as http:
        hub = Hub(
            registry=registry,
            accounts=accounts,
            servers=ServerRepository(conn),
            usage=UsageRepository(conn),
            balances=BalanceHistoryRepository(conn),
            journal=JournalRepository(conn),
            prefs=prefs,
            poller=poller,
            rates=CbrRates(rates_http),
            payments=PaymentRepository(conn),
            owner_stops=OwnerStopRepository(conn),
            autopay=AutopayStore(conn),
            own_ip=settings.hostwatch_ip,
            backups=Backups(conn, settings.data_dir / "backups", settings_repo, lambda: prefs.tz),
            history=history,
            app_url=webapp.app_url(settings.hostwatch_domain),
        )
        bot = await make_bot(settings.bot_token.get_secret_value(), settings.tg_proxy)
        hub.menu = Menu(bot, settings.owner_id, prefs)
        dp = build_dispatcher(hub, settings.owner_id)
        owner = Owner(bot, settings.owner_id)

        def login_service(events: LoginEvents) -> LoginService:
            return LoginService(launcher, events)

        hub.login = BotLogin(hub, login_service, owner, make_client, settings.hostwatch_domain)
        login.append(hub.login)
        hub.autopay_job = autopay_job(hub, runner, owner, registry)
        miniapp = None
        if hub.app_url is not None:
            miniapp = MiniApp(
                settings.bot_token.get_secret_value(),
                settings.owner_id,
                lambda: webapp.overview(hub),
            )
            await webapp.set_menu_button(bot, settings.owner_id, hub.app_url, prefs.lang)
        web = await serve(hub.login.service, miniapp=miniapp)
        admin = await serve_admin(hub.login.resolve, hub.login.send_link)
        jobs = _jobs(hub, owner, settings, http, AlertRepository(conn), settings_repo)
        poller.start()
        tasks = [asyncio.create_task(job, name=name) for name, job in jobs.items()]
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for task in [*hub.background, *hub.timers]:
                task.cancel()
            await hub.login.close()
            await web.cleanup()
            await admin.cleanup()
            await poller.stop()
            await bot.session.close()
            conn.close()


def _jobs(
    hub: Hub,
    owner: Owner,
    settings: Settings,
    http: httpx.AsyncClient,
    alert_store: AlertRepository,
    settings_repo: SettingsRepository,
) -> dict[str, Coroutine[Any, Any, None]]:
    prefs = hub.prefs

    async def deliver(batch: Sequence[AlertMessage]) -> bool:
        keys = [m.key for m in batch if m.reason is not Reason.RESOLVED]
        return await owner.send(render(batch), alert_markup(hub, keys))

    alerts = AlertService(
        hub.alert_views,
        lambda: prefs.alert_settings(hub.own_ip),
        alert_store,
        owner.send,
        deliver=deliver,
    )

    async def alerts_tick() -> None:
        await alerts.tick()
        touch_alive()

    # The buttons of the summary being sent: «Open the menu», «Problems» if there are any.
    summary_buttons: list[InlineKeyboardMarkup] = []

    async def summary_text() -> str:
        v = View(prefs.lang, prefs.tz, hub.now())
        try:
            rates = await hub.rates.get()
        except RatesUnavailable:
            rates = None
        accounts = hub.load_all()
        th = prefs.alert_settings(hub.own_ip).thresholds
        summary_buttons[:] = [views.summary_markup(v, bool(views.problems(v, accounts, th)))]
        return views.summary_text(v, accounts, rates, prefs.currency, hub.now(), th)

    async def send_summary(text: str) -> bool:
        return await owner.send(text, summary_buttons[0] if summary_buttons else None)

    summary = DailySummary(
        summary_text, lambda: (prefs.summary_at, prefs.tz), settings_repo, send_summary
    )

    async def announce(version: str, url: str) -> bool:
        return await owner.send(t("release.new", prefs.lang, version=version, url=url))

    releases = ReleaseCheck(http, current_version(), settings_repo, announce)

    async def find_own_ip() -> None:
        if settings.hostwatch_ip:
            return
        found = await public_ip(http)
        if found is not None and found != hub.own_ip:
            log.info("this server's public IP is %s", found)
            hub.own_ip = found

    jobs: dict[str, Coroutine[Any, Any, None]] = {
        "own-ip": every(DAY, find_own_ip, "own-ip"),
        "alerts": every(MINUTE, alerts_tick, "alerts"),
        "summary": every(MINUTE, summary.tick, "summary"),
        "releases": every(DAY, releases.tick, "releases"),
    }
    if hub.backups is not None:
        jobs["backups"] = every(MINUTE, hub.backups.tick, "backups")
    if hub.autopay_job is not None:
        jobs["autopay"] = every(AUTOPAY_EVERY, hub.autopay_job.tick, "autopay")
    url = settings.heartbeat_url
    if url:
        jobs["heartbeat"] = every(HEARTBEAT_EVERY, lambda: ping(http, url), "heartbeat")
    return jobs
