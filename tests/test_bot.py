"""Bot handlers fed with updates directly; Telegram is replaced by a session that records calls."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    SendDocument,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from hostwatch.bot.app import Owner, bot_properties, build_dispatcher
from hostwatch.bot.callbacks import (
    AccountCb,
    ActionCb,
    AddCb,
    HostCb,
    JournalCb,
    LabelCb,
    Nav,
    RefreshCb,
    ServerCb,
    SetCb,
)
from hostwatch.bot.hub import AccountData, Hub
from hostwatch.bot.menu import Menu
from hostwatch.bot.order import OrderCb
from hostwatch.bot.prefs import Prefs
from hostwatch.bot.views import View, monthly_cost, server_actions, server_page, summary_text
from hostwatch.core.accounts import AccountRunner
from hostwatch.core.models import (
    Account,
    AccountStatus,
    Autorenew,
    BillingModel,
    Money,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
)
from hostwatch.core.plugin import Plugin, PluginContext, Secrets
from hostwatch.core.rates import Rates
from hostwatch.core.registry import Registry
from hostwatch.plugins.aeza import AezaPlugin
from hostwatch.plugins.fake import FakePlugin
from hostwatch.plugins.timeweb import TimewebPlugin
from hostwatch.scheduler.maintenance import Backups
from hostwatch.scheduler.polling import Poller, PollEvent
from hostwatch.storage.db import connect
from hostwatch.storage.repositories import (
    AccountRepository,
    BalanceHistoryRepository,
    JournalEntry,
    JournalRepository,
    OwnerStopRepository,
    PaymentRepository,
    ServerRepository,
    SettingsRepository,
    UsageRepository,
)
from tests.replay import load

OWNER = 1001
STRANGER = 2002
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
RATES = Rates(date(2026, 11, 7), {"EUR": Decimal("100"), "USD": Decimal("90")})


class RecordingSession(BaseSession):
    """Answers every Bot API call with a plausible result and remembers the call."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        # Telegram's refusal of a call (e.g. «message can't be deleted»), or None.
        self.refuse: Callable[[TelegramMethod[Any]], str | None] = lambda _: None

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        refusal = self.refuse(method)
        if refusal is not None:
            raise TelegramBadRequest(method=method, message=refusal)
        if method.__returning__ is bool:
            return True
        message = Message(
            message_id=500 + len(self.calls),
            date=datetime.now(UTC),
            chat=Chat(id=OWNER, type="private"),
            text=getattr(method, "text", None),
        )
        return message.as_(bot)

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        pass

    def texts(self) -> list[str]:
        return [str(c.text) for c in self.calls if isinstance(c, SendMessage | EditMessageText)]

    def last_text(self) -> str:
        return self.texts()[-1]

    def keyboard(self) -> dict[str, str]:
        """Callback data -> text of the buttons under the last message sent or edited."""
        for call in reversed(self.calls):
            if isinstance(call, SendMessage | EditMessageText):
                markup = call.reply_markup
                if markup is None:
                    return {}
                return {
                    button.callback_data: button.text
                    for row in markup.inline_keyboard  # type: ignore[union-attr]
                    for button in row
                    if button.callback_data
                }
        return {}

    def buttons(self) -> list[str]:
        """Callback data of the buttons under the last message sent or edited."""
        return list(self.keyboard())


class FixedRates:
    async def get(self) -> Rates:
        return RATES


class Harness:
    def __init__(self, conn: sqlite3.Connection, accounts: AccountRepository) -> None:
        registry = Registry({"fake": FakePlugin, "aeza": AezaPlugin, "timeweb": TimewebPlugin})
        self.events: list[PollEvent] = []
        self.poller = Poller(
            AccountRunner(accounts, registry, lambda _: httpx.AsyncClient()),
            accounts,
            ServerRepository(conn),
            UsageRepository(conn),
            BalanceHistoryRepository(conn),
            self._notify,
            call_timeout=5,
        )
        self.hub = Hub(
            registry=registry,
            accounts=accounts,
            servers=ServerRepository(conn),
            usage=UsageRepository(conn),
            balances=BalanceHistoryRepository(conn),
            journal=JournalRepository(conn),
            prefs=Prefs(SettingsRepository(conn)),
            poller=self.poller,
            rates=FixedRates(),
            payments=PaymentRepository(conn),
            owner_stops=OwnerStopRepository(conn),
            now=lambda: NOW,
            hidden_plugins=frozenset(),
        )
        self.session = RecordingSession()
        self.bot = Bot("42:TEST", session=self.session, default=bot_properties())
        self.hub.menu = Menu(self.bot, OWNER, self.hub.prefs)
        self.dp: Dispatcher = build_dispatcher(self.hub, OWNER)
        self._update_id = 0

    async def _notify(self, event: PollEvent) -> None:
        self.events.append(event)

    def onboard(self, lang: str = "ru") -> None:
        self.hub.prefs.lang = lang
        self.hub.prefs.timezone = "Europe/Moscow"
        self.hub.prefs.finish_onboarding()
        # The chat has its «☰ Menu» keyboard already (a /start long ago).
        self.hub.prefs.menu_keyboard = lang

    def _next_id(self) -> int:
        self._update_id += 1
        return self._update_id

    def _user(self, user_id: int) -> User:
        return User(id=user_id, is_bot=False, first_name="someone")

    async def send(self, text: str, user_id: int = OWNER) -> None:
        message = Message(
            message_id=self._next_id(),
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=self._user(user_id),
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next_id(), message=message))

    async def press(self, data: Any, user_id: int = OWNER, message_id: int | None = None) -> None:
        """A button in the live menu, or in the message `message_id` (an old menu or a
        notification). Before the first menu, the pressed message becomes the menu."""
        packed = data if isinstance(data, str) else data.pack()
        if message_id is None:
            message_id = self.hub.prefs.menu_id or 10_000 + self._next_id()
        message = Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=42, is_bot=True, first_name="bot"),
            text="screen",
        )
        query = CallbackQuery(
            id=str(self._next_id()),
            from_user=self._user(user_id),
            chat_instance="1",
            message=message,
            data=packed,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._next_id(), callback_query=query))

    def alerts(self) -> list[str]:
        return [
            call.text or ""
            for call in self.session.calls
            if isinstance(call, AnswerCallbackQuery) and call.text
        ]


@pytest.fixture
async def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> AsyncIterator[Harness]:
    h = Harness(conn, accounts)
    yield h
    await h.poller.stop()
    await h.bot.session.close()


# Only the owner


async def test_stranger_gets_no_answer_to_messages_or_buttons(harness: Harness) -> None:
    harness.onboard()
    harness.hub.accounts.add("fake", "main", {})

    await harness.send("/start", user_id=STRANGER)
    await harness.send("hello", user_id=STRANGER)
    await harness.press(Nav(to="summary"), user_id=STRANGER)
    await harness.press(AccountCb(acc=1, op="delete_yes"), user_id=STRANGER)

    assert harness.session.calls == []
    assert len(harness.hub.accounts.all()) == 1


async def test_owner_gets_an_answer(harness: Harness) -> None:
    harness.onboard()

    await harness.send("/start")

    # The «☰ Menu» keyboard comes with a message that goes at once; then the menu.
    assert harness.session.texts() == ["☰ Меню", harness.session.last_text()]
    assert "Хостеры не подключены" in harness.session.last_text()


# First run


async def test_first_run_asks_language_timezone_currency_then_integrations(
    harness: Harness,
) -> None:
    await harness.send("/start")
    assert "Выберите язык" in harness.session.last_text()
    assert "Choose a language" in harness.session.last_text()

    await harness.press(SetCb(key="lang", value="en", onb=True))
    assert "I suggest UTC" in harness.session.last_text()

    await harness.press(SetCb(key="tz", value="Asia/Yekaterinburg", onb=True))
    assert "currency" in harness.session.last_text()

    await harness.press(SetCb(key="cur", value="EUR", onb=True))
    text = harness.session.last_text()
    assert "Connect your first hoster" in text
    assert "Connect a hoster" in text
    prefs = harness.hub.prefs
    assert (prefs.lang, prefs.timezone, prefs.currency, prefs.onboarded) == (
        "en",
        "Asia/Yekaterinburg",
        "EUR",
        True,
    )
    assert AddCb(plugin="aeza").pack() in harness.session.buttons()


async def test_first_run_accepts_a_typed_timezone(harness: Harness) -> None:
    await harness.send("/start")
    await harness.press(SetCb(key="lang", value="ru", onb=True))
    await harness.press(SetCb(key="tz", value="other", onb=True))

    await harness.send("Mars/Olympus")
    assert "Не знаю такого пояса" in harness.session.last_text()
    await harness.send("Asia/Omsk")

    assert harness.hub.prefs.timezone == "Asia/Omsk"
    assert "валюте" in harness.session.last_text()


# Buttons follow plugin capabilities and Server.actions


def _data(plugin: type[Plugin], servers: list[Server]) -> AccountData:
    account = Account(1, plugin.meta.id, plugin.meta.name, AccountStatus.OK, None, NOW)
    return AccountData(account, plugin, servers, NOW, None, None, None)


def _server(**changes: Any) -> Server:
    base = Server(
        external_id="1",
        name="beaver",
        kind=ServerKind.VPS,
        status=ServerStatus.RUNNING,
        raw_status="active",
        price=Money.of("4.50", "EUR"),
        period_months=1,
        next_charge_at=NOW + timedelta(days=8),
        autorenew=Autorenew.ON,
        billing=BillingModel.PERIODIC,
        actions=frozenset(ServerAction),
    )
    return base.model_copy(update=changes)


def test_fake_running_server_buttons() -> None:
    server = _server()
    assert server_actions(_data(FakePlugin, [server]), server) == [
        "restart",
        "stop",
        "renew",
        "autorenew_off",
        "order_same",
    ]


def test_fake_stopped_server_buttons() -> None:
    server = _server(status=ServerStatus.STOPPED, autorenew=Autorenew.OFF)
    assert server_actions(_data(FakePlugin, [server]), server) == [
        "start",
        "renew",
        "autorenew_on",
        "order_same",
    ]


def test_server_actions_limit_buttons() -> None:
    server = _server(actions=frozenset({ServerAction.RESTART}))
    assert server_actions(_data(FakePlugin, [server]), server) == [
        "restart",
        "order_same",
    ]
    # Bot autopay only where the hoster has no autorenewal of its own (stage 11).
    no_native = server.model_copy(update={"autorenew": Autorenew.NOT_AVAILABLE})
    assert server_actions(_data(FakePlugin, [no_native]), no_native) == [
        "restart",
        "autopay",
        "order_same",
    ]


def test_plugin_without_capabilities_shows_no_action_buttons() -> None:
    class ReadOnly(FakePlugin):
        power = set_autorenew = renew = renew_price = None  # type: ignore[assignment]
        pay_invoice = order = catalog = None  # type: ignore[assignment]

    server = _server()
    assert server_actions(_data(ReadOnly, [server]), server) == []
    # Timeweb bills by the hour: power and orders only.
    assert server_actions(_data(TimewebPlugin, [server]), server) == [
        "restart",
        "stop",
        "order_same",
    ]


async def test_server_card_of_fake_has_its_buttons(harness: Harness) -> None:
    harness.onboard()
    account = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(account)

    await harness.press(Nav(to="servers"))
    assert HostCb(acc=account).pack() in harness.session.buttons()
    await harness.press(HostCb(acc=account))
    assert "VPS: 2 · выделенных: 1" in harness.session.last_text()
    label = harness.session.keyboard()[ServerCb(acc=account, sid="1").pack()]
    assert re.fullmatch(r"🟢 beaver · \d+ дн\.", label)

    await harness.press(ServerCb(acc=account, sid="1"))
    card = harness.session.last_text()
    assert card.startswith("<b>🟢 beaver · Fake hoster (main)</b>\n")
    assert "\nCPU 12%  RAM 40%  Диск 55%\n" in card
    assert "💳 €4.50/мес · списание " in card
    assert "🔁 Автопродление: вкл" in card
    buttons = harness.session.buttons()
    for act in ("stop", "restart", "autorenew_off", "renew", "order_same"):
        assert ActionCb(acc=account, sid="1", act=act).pack() in buttons
    assert ActionCb(acc=account, sid="1", act="start").pack() not in buttons

    assert ActionCb(acc=account, sid="1", act="password").pack() in buttons

    await harness.press(ActionCb(acc=account, sid="1", act="order_same"))
    # The plan, location and OS are copied; the term is left to choose.
    assert "Выберите срок" in harness.session.last_text()


async def test_order_shows_in_menu_only_when_a_plugin_can_order(harness: Harness) -> None:
    harness.onboard()
    acc = harness.hub.accounts.add("fake", "main", {})
    harness.hub.accounts.set_status(acc, AccountStatus.DISABLED)
    await harness.press(Nav(to="servers"))
    assert Nav(to="order").pack() not in harness.session.buttons()

    harness.hub.accounts.set_status(acc, AccountStatus.OK)
    await harness.press(Nav(to="servers"))
    assert Nav(to="order").pack() in harness.session.buttons()


# Aeza and Timeweb from recorded answers


def _repeating(records: list[dict[str, Any]]) -> respx.MockRouter:
    """Recorded answers in order; the last answer for a path repeats after that."""
    router = respx.mock(assert_all_called=False)
    by_url: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        by_url.setdefault((record["method"], record["url"]), []).append(record)
    for (method, url), items in by_url.items():
        parsed = httpx.URL(url)
        queue = list(items)

        def answer(request: httpx.Request, queue: list[dict[str, Any]] = queue) -> httpx.Response:
            record = queue.pop(0) if len(queue) > 1 else queue[0]
            if "json" in record:
                return httpx.Response(record["status"], json=record["json"])
            return httpx.Response(record["status"], text=record["text"])

        router.route(method=method, host=parsed.host, path=parsed.path).mock(side_effect=answer)
    return router


async def _store_from_fixtures(harness: Harness, plugin: type[Plugin], secrets: Secrets) -> int:
    """Read servers and balance through the plugin from its fixtures and store them."""
    account = harness.hub.accounts.add(plugin.meta.id, plugin.meta.name, secrets)

    async def keep(_: Secrets) -> None:
        pass

    with _repeating(load(plugin.meta.id)):
        async with httpx.AsyncClient() as http:
            instance = plugin(PluginContext(secrets, http, keep))
            servers = await instance.servers()
            balance = await instance.balance()
    harness.hub.servers.replace(account, servers, NOW)
    harness.hub.balances.add(account, balance.money, NOW, balance.hours_left)
    harness.hub.accounts.mark_ok(account, NOW)
    return account


@pytest.fixture
async def live_like(harness: Harness) -> Harness:
    await _store_from_fixtures(harness, AezaPlugin, {"api_key": "a" * 32})
    await _store_from_fixtures(harness, TimewebPlugin, {"api_token": "t" * 32})
    return harness


async def test_summary_shows_aeza_and_timeweb(live_like: Harness) -> None:
    """Home: the state first and bold alone, then the totals. The daily summary: a money
    line per hoster."""
    live_like.onboard("ru")
    hub = live_like.hub
    accounts = hub.load_all()
    v = View("ru", hub.prefs.tz, NOW)
    servers = sum(len(d.servers) for d in accounts)

    await live_like.press(Nav(to="menu"))

    text = live_like.session.last_text()
    first = text.split("\n")[0]
    assert first.startswith("<b>") and first.endswith("</b>")
    assert text.count("<b>") == 1
    assert f"2 хостера · {v.count(servers, 'server')}" in first
    assert (
        "\n💰 На балансах: ≈ " in text
        and text.split("💰 На балансах: ")[1].split("\n")[0][-1] == "₽"
    )
    assert "\n💳 В месяц: ≈ " in text
    keyboard = live_like.session.keyboard()
    assert keyboard[Nav(to="servers").pack()] == "🖥 Серверы"
    assert keyboard[Nav(to="balance").pack()] == "💰 Деньги"
    assert keyboard[Nav(to="integrations").pack()] == "🔌 Хостеры"
    assert keyboard[Nav(to="settings").pack()] == "⚙️ Настройки"

    summary = summary_text(v, accounts, RATES, "RUB", NOW)
    assert summary.startswith("<b>📰 Сводка · 07.11 15:00</b>\n")
    for d in accounts:
        assert d.balance is not None
        assert f"{d.title} · {d.balance.money}" in summary


async def test_summary_in_english(live_like: Harness) -> None:
    live_like.onboard("en")

    # «Summary» buttons of the messages sent before 1.1 lead to the home screen.
    await live_like.press(Nav(to="summary"))

    text = live_like.session.last_text()
    assert "· 2 hosters ·" in text.split("\n")[0]
    assert "💰 On balances: ≈" in text
    assert "хостер" not in text and "Сводка" not in text


async def test_balance_explains_the_forecast_method(live_like: Harness) -> None:
    """Money: a line per hoster; the forecast in full is behind «ℹ️»."""
    live_like.onboard("ru")
    hub = live_like.hub

    await live_like.press(Nav(to="balance"))

    text = live_like.session.last_text()
    assert text.startswith("<b>💰 Деньги · ")
    for d in hub.load_all():
        assert d.balance is not None
        assert f"{d.title} · {d.balance.money} · " in text
    assert "Метод:" not in text
    assert "≈ по курсу ЦБ РФ на 07.11" in text
    keyboard = live_like.session.keyboard()
    assert keyboard[Nav(to="money_info").pack()] == "ℹ️ Как считаю"

    await live_like.press(Nav(to="money_info"))
    assert live_like.session.last_text().count("Метод:") == 2
    _assert_nav(live_like, Nav(to="balance"))


async def test_servers_and_cards_of_aeza_and_timeweb(live_like: Harness) -> None:
    live_like.onboard("en")
    hub = live_like.hub

    await live_like.press(Nav(to="servers"))
    hosters = live_like.session.keyboard()

    for d in hub.load_all():
        assert hosters[HostCb(acc=d.account.id).pack()].endswith(f" {d.title} · {len(d.servers)}")
        await live_like.press(HostCb(acc=d.account.id))
        listing = live_like.session.last_text()
        assert f"{d.title} · " in listing.split("\n")[0]
        labels = live_like.session.keyboard()
        for server in d.servers:
            if server_page(d, server) == 0:
                label = labels[ServerCb(acc=d.account.id, sid=server.external_id).pack()]
                assert server.name in label
            await live_like.press(ServerCb(acc=d.account.id, sid=server.external_id))
            card = live_like.session.last_text()
            assert f" {server.name} · {d.plugin.meta.name}" in card.split("\n")[0]
            assert "🕓 Data from" in card
            shown = [
                b
                for b in live_like.session.buttons()
                if b.startswith("a:") and not b.endswith(":password")
            ]
            assert len(shown) == len(server_actions(d, server))


async def test_failed_account_shows_reason_and_data_age(live_like: Harness) -> None:
    live_like.onboard("ru")
    aeza = live_like.hub.accounts.find("Aeza")[0].id
    live_like.hub.accounts.set_status(aeza, AccountStatus.ERROR, "hoster_error: maintenance")

    await live_like.press(Nav(to="problems"))
    text = live_like.session.last_text()
    assert "\n🔴 Aeza — нет данных: хостер отказал: maintenance" in text
    assert live_like.session.keyboard()[AccountCb(acc=aeza, op="card").pack()] == "🔌 Aeza"

    await live_like.press(Nav(to="balance"))
    assert "данные от 07.11 15:00" in live_like.session.last_text()

    await live_like.press(Nav(to="servers"))
    assert (
        "🔴 Aeza — нет связи (хостер отказал: maintenance), данные от 07.11 15:00"
        in live_like.session.last_text()
    )


# Integrations


async def test_add_account_deletes_the_key_message(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.press(AddCb(plugin="fake"))
    await harness.press(LabelCb())
    await harness.send("secret-token-value")

    assert any(isinstance(call, DeleteMessage) for call in harness.session.calls)
    added = harness.hub.accounts.all()
    assert [(a.plugin_id, a.label) for a in added] == [("fake", "Fake hoster")]
    assert harness.hub.accounts.load_secrets(added[0].id) == {"token": "secret-token-value"}
    assert "secret-token-value" not in "".join(harness.session.texts())
    card = harness.session.last_text()
    assert card.startswith("<b>🟢 Fake hoster · подключён</b>")
    assert "🖥 3 сервера" in card


async def test_typed_label_and_taken_label(harness: Harness) -> None:
    harness.onboard("en")
    harness.hub.accounts.add("fake", "work", {})

    await harness.press(AddCb(plugin="fake"))
    await harness.send("work")
    assert "is taken" in harness.session.last_text()
    assert LabelCb().pack() in harness.session.buttons()
    assert len(harness.hub.accounts.all()) == 1


async def test_change_key_disable_enable_delete(harness: Harness) -> None:
    harness.onboard("en")
    account = harness.hub.accounts.add("fake", "main", {"token": "old", "balance": "5"})
    harness.hub.accounts.set_status(account, AccountStatus.RELOGIN, "auth_expired")

    await harness.press(AccountCb(acc=account, op="card"))
    assert "⚠️ the key or login no longer works" in harness.session.last_text()

    await harness.press(AccountCb(acc=account, op="key"))
    await harness.send("new-token-123")
    assert harness.hub.accounts.load_secrets(account) == {"token": "new-token-123", "balance": "5"}
    assert harness.hub.accounts.get(account).status is AccountStatus.OK

    await harness.press(AccountCb(acc=account, op="disable"))
    assert harness.hub.accounts.get(account).status is AccountStatus.DISABLED
    assert AccountCb(acc=account, op="enable").pack() in harness.session.buttons()

    await harness.press(AccountCb(acc=account, op="enable"))
    assert harness.hub.accounts.get(account).status is AccountStatus.OK

    await harness.press(AccountCb(acc=account, op="delete"))
    assert harness.hub.accounts.all()
    await harness.press(AccountCb(acc=account, op="delete_yes"))
    assert harness.hub.accounts.all() == []


async def test_refresh_once_a_minute_per_account(harness: Harness) -> None:
    harness.onboard("en")
    account = harness.hub.accounts.add("fake", "main", {})
    first = harness.poller.poll_done(account)
    harness.poller.start_account(account)
    await asyncio.wait_for(first, 5)

    await harness.press(RefreshCb(acc=account, view="account"))
    assert harness.alerts()[-1] == "🔃 Refreshing…"
    assert "🕓 Last poll" in harness.session.last_text()

    await harness.press(RefreshCb(acc=account, view="account"))
    assert "once a minute" in harness.alerts()[-1]


# Settings


async def test_settings_save_language_and_thresholds(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.press(SetCb(key="lang", value="en"))
    assert "<b>⚙️ Settings</b>" in harness.session.last_text()

    await harness.press(SetCb(key="th", value="quiet_hours"))
    await harness.send("25:00-08:00")
    assert "did not understand" in harness.session.last_text()
    await harness.send("22:30 - 07:00")
    assert harness.hub.prefs.threshold("quiet_hours") == "22:30-07:00"

    await harness.press(SetCb(key="cur", value="USD"))
    assert harness.hub.prefs.currency == "USD"
    assert "🌐 English · Europe/Moscow · USD" in harness.session.last_text()


async def test_journal_is_empty(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.press(Nav(to="journal"))

    assert "Пока пусто" in harness.session.last_text()


# Pure views


def test_monthly_cost_normalises_periods_and_hours() -> None:
    servers = [
        _server(price=Money.of("12", "EUR"), period_months=3),
        _server(price=Money.of("1", "RUB"), period_months=None, billing=BillingModel.HOURLY),
    ]
    assert sorted(monthly_cost(servers), key=lambda m: m.currency) == [
        Money.of("4", "EUR"),
        Money.of("730", "RUB"),
    ]


def test_summary_without_rates_says_so() -> None:
    server = _server()
    d = AccountData(
        Account(1, "fake", "main", AccountStatus.OK, None, NOW),
        FakePlugin,
        [server],
        NOW,
        None,
        None,
        None,
    )
    # Without rates nothing is converted: a sum per currency.
    text = summary_text(View("en", UTC, NOW), [d], None, "RUB", NOW)
    assert "💳 A month: ≈ €5" in text
    assert "₽" not in text


async def test_send_backup_button_sends_the_database(harness: Harness, tmp_path: Path) -> None:
    harness.onboard("ru")
    conn = connect(tmp_path / "db.sqlite3")
    harness.hub.backups = Backups(
        conn, tmp_path / "backups", SettingsRepository(conn), lambda: harness.hub.prefs.tz
    )

    await harness.press(Nav(to="settings"))
    assert SetCb(key="backup").pack() in harness.session.buttons()
    await harness.press(SetCb(key="backup"))

    documents = [c for c in harness.session.calls if isinstance(c, SendDocument)]
    assert len(documents) == 1
    assert "HOSTWATCH_KEY" in str(documents[0].caption)
    conn.close()


async def test_own_server_is_marked(harness: Harness) -> None:
    harness.onboard("ru")
    account = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(account)
    server = harness.hub.load(account).servers[0]
    harness.hub.own_ip = server.ip

    await harness.press(HostCb(acc=account))
    label = harness.session.keyboard()[ServerCb(acc=account, sid=server.external_id).pack()]
    assert label.endswith("· hostwatch")
    await harness.press(ServerCb(acc=account, sid=server.external_id))
    assert "Это сервер hostwatch" in harness.session.last_text()


async def test_alert_views_carry_usage_and_settings(harness: Harness) -> None:
    harness.onboard("ru")
    account = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(account)
    harness.hub.prefs.set_threshold("balance_days", "7")

    [alert_view] = harness.hub.alert_views()
    assert alert_view.usage
    assert alert_view.failures == 0
    config = harness.hub.prefs.alert_settings("203.0.113.7")
    assert config.thresholds.balance_days == 7
    assert config.quiet is not None and config.own_ip == "203.0.113.7"


# Navigation: «Back» one level up and «Menu» on every screen, by editing the same message

BACK, HOME = "‹ Назад", "🏠 Главная"
MENU = Nav(to="menu").pack()


def _assert_nav(harness: Harness, back: Any = None) -> None:
    """The last call edited the screen in place and it has «Menu» (and «Back» to `back`)."""
    assert isinstance(harness.session.calls[-1], EditMessageText)
    keyboard = harness.session.keyboard()
    assert keyboard.get(MENU) == HOME
    if back is not None:
        assert keyboard.get(back.pack()) == BACK


@pytest.mark.parametrize("to", ["problems", "servers", "balance", "integrations", "settings"])
async def test_sections_of_the_menu_lead_back_to_it(harness: Harness, to: str) -> None:
    harness.onboard("ru")
    harness.hub.accounts.add("fake", "main", {})

    await harness.press(Nav(to=to))
    _assert_nav(harness)

    await harness.press(Nav(to="menu"))
    assert isinstance(harness.session.calls[-1], EditMessageText)
    assert "<b>🟢 Всё в порядке · 1 хостер · 0 серверов</b>" in harness.session.last_text()


async def test_back_and_menu_on_every_screen(harness: Harness) -> None:
    harness.onboard("ru")
    account = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(account)
    harness.hub.journal.add(
        JournalEntry(
            at=NOW, actor="owner", account_id=account, action="x", params={}, response="", ok=True
        )
    )
    screens: list[tuple[Any, Any]] = [
        (Nav(to="connect"), Nav(to="integrations")),
        (HostCb(acc=account), Nav(to="servers")),
        (ServerCb(acc=account, sid="1"), HostCb(acc=account, page=0)),
        (AccountCb(acc=account, op="card"), Nav(to="integrations")),
        (AccountCb(acc=account, op="delete"), AccountCb(acc=account, op="card")),
        (AccountCb(acc=account, op="key"), AccountCb(acc=account, op="card")),
        (AddCb(plugin="fake"), Nav(to="connect")),
        (LabelCb(), Nav(to="connect")),
        (Nav(to="settings"), None),
        (SetCb(key="lang"), Nav(to="settings")),
        (SetCb(key="tz"), Nav(to="settings")),
        (SetCb(key="tz", value="other"), SetCb(key="tz")),
        (SetCb(key="cur"), Nav(to="settings")),
        (SetCb(key="th", value="balance_days"), Nav(to="settings")),
        (Nav(to="journal"), Nav(to="settings")),
        (JournalCb(page=0), Nav(to="settings")),
        (Nav(to="money_info"), Nav(to="balance")),
    ]
    for pressed, back in screens:
        await harness.press(pressed)
        _assert_nav(harness, back)


async def test_order_in_the_menu_lists_the_accounts_that_can_order(harness: Harness) -> None:
    harness.onboard("ru")
    acc = harness.hub.accounts.add("fake", "main", {})

    await harness.press(Nav(to="order"))

    assert "Выберите хостера." in harness.session.last_text()
    assert OrderCb(acc=acc).pack() in harness.session.buttons()
    _assert_nav(harness, Nav(to="servers"))


async def test_menu_command_works_in_the_middle_of_a_question(harness: Harness) -> None:
    harness.onboard("en")
    await harness.press(SetCb(key="th", value="quiet_hours"))

    await harness.send("/menu")

    assert "No hosters connected" in harness.session.last_text()
    await harness.send("22:00-07:00")  # no longer an answer to the question
    assert harness.hub.prefs.threshold("quiet_hours") != "22:00-07:00"


# Integrations: connected accounts, hosters to add on a screen of their own


async def test_integrations_show_connected_accounts_only(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.press(Nav(to="integrations"))
    text = harness.session.last_text()
    assert text.startswith("<b>🔌 Хостеры не подключены</b>")
    assert "＋ Можно подключить: Aeza, Fake hoster, Timeweb Cloud" in text
    assert harness.session.keyboard()[Nav(to="connect").pack()] == "＋ Подключить"
    assert not any(b.startswith("add:") for b in harness.session.buttons())

    account = harness.hub.accounts.add("fake", "main", {})
    await harness.press(Nav(to="integrations"))
    text = harness.session.last_text()
    assert text.startswith("<b>🔌 1 хостер · 🟢 все на связи</b>")
    assert "\n🟢 Fake hoster (main) · 0 серверов" in text
    assert "＋ Можно подключить: Aeza, Timeweb Cloud" in text
    keyboard = harness.session.keyboard()
    assert keyboard[AccountCb(acc=account, op="card").pack()] == "🟢 Fake hoster (main)"
    assert not any(b.startswith("add:") for b in harness.session.buttons())


async def test_connect_screen_has_a_button_per_hoster(harness: Harness) -> None:
    harness.onboard("ru")
    harness.hub.accounts.add("fake", "main", {})

    await harness.press(Nav(to="connect"))

    keyboard = harness.session.keyboard()
    assert keyboard[AddCb(plugin="aeza").pack()] == "Aeza"
    assert keyboard[AddCb(plugin="timeweb").pack()] == "Timeweb Cloud"
    assert keyboard[AddCb(plugin="fake").pack()] == "＋ ещё Fake hoster"
    assert harness.session.last_text().startswith("<b>＋ Подключить хостера</b>\nВыберите хостера.")

    await harness.press(AddCb(plugin="aeza"))
    assert "Aeza" in harness.session.last_text()


# Servers: hosters first, then the servers of one, page by page


def _many_servers(count: int) -> list[Server]:
    return [
        _server(
            external_id=str(n),
            name=f"srv-{n:02}",
            kind=ServerKind.DEDICATED if n % 4 == 0 else ServerKind.VPS,
        )
        for n in range(1, count + 1)
    ]


async def test_servers_list_hosters_with_counts_and_problems(harness: Harness) -> None:
    harness.onboard("ru")
    calm = harness.hub.accounts.add("fake", "calm", {})
    harness.hub.servers.replace(calm, _many_servers(2), NOW)
    troubled = harness.hub.accounts.add("fake", "busy", {})
    stopped = _server(external_id="9", name="down", status=ServerStatus.STOPPED)
    harness.hub.servers.replace(troubled, [*_many_servers(2), stopped], NOW)

    await harness.press(Nav(to="servers"))

    text = harness.session.last_text()
    assert text.startswith("<b>🖥 5 серверов · 🔴 1 проблема</b>\nВыберите хостера.")
    assert "srv-01" not in text  # servers are one level down
    keyboard = harness.session.keyboard()
    assert keyboard[HostCb(acc=calm).pack()] == "🟢 Fake hoster (calm) · 2"
    assert keyboard[HostCb(acc=troubled).pack()] == "🔴 Fake hoster (busy) · 3"

    await harness.press(HostCb(acc=troubled))
    listing = harness.session.last_text()
    assert listing.startswith("<b>🔴 Fake hoster (busy) · 3 сервера</b>")
    assert "\n🔴 down — остановлен" in listing
    assert harness.session.keyboard()[ServerCb(acc=troubled, sid="9").pack()] == "🔴 down · 8 дн."


async def test_servers_of_a_hoster_split_by_kind_and_turn_pages(harness: Harness) -> None:
    harness.onboard("ru")
    account = harness.hub.accounts.add("fake", "main", {})
    harness.hub.servers.replace(account, _many_servers(11), NOW)

    await harness.press(HostCb(acc=account))
    first = harness.session.last_text()
    servers = [b for b in harness.session.buttons() if b.startswith("s:")]
    assert len(servers) == 8
    assert "Стр. 1 из 2" in first
    assert "VPS: 9 · выделенных: 2" in first
    assert HostCb(acc=account, page=1).pack() in harness.session.buttons()
    assert HostCb(acc=account, page=-1).pack() not in harness.session.buttons()

    await harness.press(HostCb(acc=account, page=1))
    second = harness.session.last_text()
    servers = [b for b in harness.session.buttons() if b.startswith("s:")]
    # VPS first, dedicated servers after them.
    assert servers == [ServerCb(acc=account, sid=sid).pack() for sid in ("11", "4", "8")]
    assert "Стр. 2 из 2" in second
    assert HostCb(acc=account, page=0).pack() in harness.session.buttons()
    _assert_nav(harness, Nav(to="servers"))

    # The last dedicated server sits on the second page; «Back» from its card goes there.
    await harness.press(ServerCb(acc=account, sid="8"))
    _assert_nav(harness, HostCb(acc=account, page=1))


async def test_few_servers_need_no_pages(harness: Harness) -> None:
    harness.onboard("en")
    account = harness.hub.accounts.add("fake", "main", {})
    harness.hub.servers.replace(account, _many_servers(8), NOW)

    await harness.press(HostCb(acc=account))

    assert "Page" not in harness.session.last_text()
    assert not any(b.startswith("h:") for b in harness.session.buttons())


async def test_owner_chat_posts_deletes_and_edits(harness: Harness) -> None:
    owner = Owner(harness.bot, OWNER)

    message_id = await owner.post("hello")

    assert message_id is not None
    assert await owner.delete(message_id)
    assert await owner.edit(message_id, "gone")
    kinds = [type(call) for call in harness.session.calls]
    assert kinds == [SendMessage, DeleteMessage, EditMessageText]
