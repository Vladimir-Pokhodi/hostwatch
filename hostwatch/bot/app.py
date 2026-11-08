"""Builds the bot and runs it together with the polling scheduler in one process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
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
from hostwatch.core.config import Settings
from hostwatch.core.http import make_client
from hostwatch.core.logs import register_secrets
from hostwatch.core.rates import CbrRates, RatesUnavailable
from hostwatch.core.registry import discover
from hostwatch.locales import t
from hostwatch.scheduler.alerts import AlertService, DailySummary
from hostwatch.scheduler.maintenance import Backups, ReleaseCheck, every, ping, public_ip
from hostwatch.scheduler.polling import Poller, PollEvent
from hostwatch.storage.db import connect
from hostwatch.storage.repositories import (
    AccountRepository,
    AlertRepository,
    BalanceHistoryRepository,
    JournalRepository,
    PaymentRepository,
    ServerRepository,
    SettingsRepository,
    UsageRepository,
)
from hostwatch.web.browser import remote_launcher
from hostwatch.web.headless import HeadlessPages
from hostwatch.web.login import LoginEvents, LoginService
from hostwatch.web.server import serve, serve_admin

from . import views
from .handlers import make_router
from .hub import Hub
from .login import BotLogin
from .middleware import OwnerOnly
from .prefs import Prefs
from .views import View

log = logging.getLogger(__name__)

DB_NAME = "hostwatch.sqlite3"
MINUTE = 60.0
DAY = 24 * 60 * MINUTE
HEARTBEAT_EVERY = 5 * MINUTE
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
    me = await bot.get_me()
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
    poller = Poller(
        runner,
        accounts,
        ServerRepository(conn),
        UsageRepository(conn),
        BalanceHistoryRepository(conn),
        _log_event,
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
            own_ip=settings.hostwatch_ip,
            backups=Backups(conn, settings.data_dir / "backups", settings_repo, lambda: prefs.tz),
        )
        bot = await make_bot(settings.bot_token.get_secret_value(), settings.tg_proxy)
        dp = build_dispatcher(hub, settings.owner_id)
        owner = Owner(bot, settings.owner_id)

        def login_service(events: LoginEvents) -> LoginService:
            return LoginService(launcher, events)

        hub.login = BotLogin(hub, login_service, owner, make_client, settings.hostwatch_domain)
        login.append(hub.login)
        web = await serve(hub.login.service)
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
            for task in list(hub.background):
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
    alerts = AlertService(
        hub.alert_views, lambda: prefs.alert_settings(hub.own_ip), alert_store, owner.send
    )

    async def alerts_tick() -> None:
        await alerts.tick()
        touch_alive()

    async def summary_text() -> str:
        v = View(prefs.lang, prefs.tz)
        try:
            rates = await hub.rates.get()
        except RatesUnavailable:
            rates = None
        return views.summary_text(v, hub.load_all(), rates, prefs.currency, hub.now())

    summary = DailySummary(
        summary_text, lambda: (prefs.summary_at, prefs.tz), settings_repo, owner.send
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
    url = settings.heartbeat_url
    if url:
        jobs["heartbeat"] = every(HEARTBEAT_EVERY, lambda: ping(http, url), "heartbeat")
    return jobs
