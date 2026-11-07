"""Builds the bot and runs it together with the polling scheduler in one process."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from hostwatch.core.accounts import AccountRunner
from hostwatch.core.config import Settings
from hostwatch.core.http import make_client
from hostwatch.core.rates import CbrRates
from hostwatch.core.registry import discover
from hostwatch.scheduler.polling import Poller, PollEvent
from hostwatch.storage.db import connect
from hostwatch.storage.repositories import (
    AccountRepository,
    BalanceHistoryRepository,
    JournalRepository,
    ServerRepository,
    SettingsRepository,
    UsageRepository,
)

from .handlers import make_router
from .hub import Hub
from .middleware import OwnerOnly
from .prefs import Prefs

log = logging.getLogger(__name__)

DB_NAME = "hostwatch.sqlite3"


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
    # Owner notifications arrive at stage 05; until then events go to the log.
    log.warning("account %s: %s (%s)", event.account_id, event.kind.value, event.detail)


async def run(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.data_dir / DB_NAME)
    registry = discover(settings.plugins_dir)
    accounts = AccountRepository(conn, settings.encryption_key)
    runner = AccountRunner(accounts, registry, make_client)
    poller = Poller(
        runner,
        accounts,
        ServerRepository(conn),
        UsageRepository(conn),
        BalanceHistoryRepository(conn),
        _log_event,
    )
    async with make_client("cbr") as rates_http:
        hub = Hub(
            registry=registry,
            accounts=accounts,
            servers=ServerRepository(conn),
            usage=UsageRepository(conn),
            balances=BalanceHistoryRepository(conn),
            journal=JournalRepository(conn),
            prefs=Prefs(SettingsRepository(conn)),
            poller=poller,
            rates=CbrRates(rates_http),
        )
        bot = await make_bot(settings.bot_token.get_secret_value(), settings.tg_proxy)
        dp = build_dispatcher(hub, settings.owner_id)
        poller.start()
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            await poller.stop()
            await bot.session.close()
            conn.close()
