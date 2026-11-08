"""Bot handlers fed with updates directly; Telegram is replaced by a session that records calls."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
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
from hostwatch.bot.prefs import Prefs
from hostwatch.bot.views import View, monthly_cost, server_actions, summary_text
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

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
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
            now=lambda: NOW,
            hidden_plugins=frozenset(),
        )
        self.session = RecordingSession()
        self.bot = Bot("42:TEST", session=self.session, default=bot_properties())
        self.dp: Dispatcher = build_dispatcher(self.hub, OWNER)
        self._update_id = 0

    async def _notify(self, event: PollEvent) -> None:
        self.events.append(event)

    def onboard(self, lang: str = "ru") -> None:
        self.hub.prefs.lang = lang
        self.hub.prefs.timezone = "Europe/Moscow"
        self.hub.prefs.finish_onboarding()

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

    async def press(self, data: Any, user_id: int = OWNER) -> None:
        packed = data if isinstance(data, str) else data.pack()
        message = Message(
            message_id=self._next_id(),
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

    assert harness.session.texts() == [harness.session.last_text()]
    assert "hostwatch" in harness.session.last_text()


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
        "stop",
        "restart",
        "autorenew_off",
        "renew",
        "order_same",
    ]


def test_fake_stopped_server_buttons() -> None:
    server = _server(status=ServerStatus.STOPPED, autorenew=Autorenew.OFF)
    assert server_actions(_data(FakePlugin, [server]), server) == [
        "start",
        "autorenew_on",
        "renew",
        "order_same",
    ]


def test_server_actions_limit_buttons() -> None:
    server = _server(actions=frozenset({ServerAction.RESTART}))
    assert server_actions(_data(FakePlugin, [server]), server) == [
        "restart",
        "autopay",
        "order_same",
    ]


def test_plugin_without_capabilities_shows_no_action_buttons() -> None:
    server = _server()
    assert server_actions(_data(AezaPlugin, [server]), server) == []
    assert server_actions(_data(TimewebPlugin, [server]), server) == []


async def test_server_card_of_fake_has_its_buttons(harness: Harness) -> None:
    harness.onboard()
    account = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(account)

    await harness.press(Nav(to="servers"))
    assert HostCb(acc=account).pack() in harness.session.buttons()
    await harness.press(HostCb(acc=account))
    assert "beaver" in harness.session.last_text()
    assert "Выделенные серверы" in harness.session.last_text()

    await harness.press(ServerCb(acc=account, sid="1"))
    card = harness.session.last_text()
    assert "<b>beaver</b>" in card
    assert "Нагрузка: CPU 12%, RAM 40%, диск 55%" in card
    assert "Цена: €4.50 в месяц" in card
    buttons = harness.session.buttons()
    for act in ("stop", "restart", "autorenew_off", "renew", "order_same"):
        assert ActionCb(acc=account, sid="1", act=act).pack() in buttons
    assert ActionCb(acc=account, sid="1", act="start").pack() not in buttons

    await harness.press(ActionCb(acc=account, sid="1", act="stop"))
    assert harness.alerts()[-1] == "Будет на этапе 10."


async def test_order_shows_in_menu_only_when_a_plugin_can_order(harness: Harness) -> None:
    harness.onboard()
    harness.hub.accounts.add("aeza", "main", {"api_key": "x" * 20})
    await harness.send("/menu")
    assert Nav(to="order").pack() not in harness.session.buttons()

    harness.hub.accounts.add("fake", "main", {})
    await harness.send("/menu")
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
    live_like.onboard("ru")
    hub = live_like.hub
    data = {d.account.plugin_id: d for d in hub.load_all()}

    await live_like.press(Nav(to="summary"))

    text = live_like.session.last_text()
    for plugin_id in ("aeza", "timeweb"):
        d = data[plugin_id]
        assert d.balance is not None
        assert f"<b>{d.plugin.meta.name}</b> · {d.balance.money}" in text
        assert f"Серверы: {len(d.servers)}" in text
    assert "Всего на балансах ≈" in text
    assert "Всего в месяц ≈" in text
    assert "по курсу ЦБ РФ на 07.11.2026" in text
    assert RefreshCb(acc=0, view="summary").pack() in live_like.session.buttons()


async def test_summary_in_english(live_like: Harness) -> None:
    live_like.onboard("en")

    await live_like.press(Nav(to="summary"))

    text = live_like.session.last_text()
    assert "<b>Summary</b>" in text
    assert "Total on balances ≈" in text
    assert "Сводка" not in text


async def test_balance_explains_the_forecast_method(live_like: Harness) -> None:
    live_like.onboard("ru")

    await live_like.press(Nav(to="balance"))

    text = live_like.session.last_text()
    assert "<b>Aeza</b>:" in text
    assert "<b>Timeweb Cloud</b>:" in text
    assert text.count("Метод:") == 2


async def test_servers_and_cards_of_aeza_and_timeweb(live_like: Harness) -> None:
    live_like.onboard("en")
    hub = live_like.hub

    await live_like.press(Nav(to="servers"))
    hosters = live_like.session.last_text()

    for d in hub.load_all():
        assert f"<b>{d.plugin.meta.name}</b>: {len(d.servers)} servers" in hosters
        await live_like.press(HostCb(acc=d.account.id))
        listing = live_like.session.last_text()
        for server in d.servers:
            assert server.name in listing
            await live_like.press(ServerCb(acc=d.account.id, sid=server.external_id))
            card = live_like.session.last_text()
            assert f"<b>{server.name}</b> · {d.plugin.meta.name}" in card
            assert "Data from" in card
            # Neither plugin can act on servers yet: only Refresh and Back.
            assert all(not b.startswith("a:") for b in live_like.session.buttons())


async def test_failed_account_shows_reason_and_data_age(live_like: Harness) -> None:
    live_like.onboard("ru")
    aeza = live_like.hub.accounts.find("Aeza")[0].id
    live_like.hub.accounts.set_status(aeza, AccountStatus.ERROR, "hoster_error: maintenance")

    await live_like.press(Nav(to="summary"))

    text = live_like.session.last_text()
    assert (
        "<b>Aeza</b>: не удалось получить данные (хостер отказал: maintenance),"
        " данные от 07.11 15:00" in text
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
    assert "Статус: подключено" in card
    assert "Серверов: 3" in card


async def test_typed_label_and_taken_label(harness: Harness) -> None:
    harness.onboard("en")
    harness.hub.accounts.add("fake", "work", {})

    await harness.press(AddCb(plugin="fake"))
    await harness.send("work")
    assert "is taken" in harness.session.texts()[-2]
    assert LabelCb().pack() in harness.session.buttons()
    assert len(harness.hub.accounts.all()) == 1


async def test_change_key_disable_enable_delete(harness: Harness) -> None:
    harness.onboard("en")
    account = harness.hub.accounts.add("fake", "main", {"token": "old", "balance": "5"})
    harness.hub.accounts.set_status(account, AccountStatus.RELOGIN, "auth_expired")

    await harness.press(AccountCb(acc=account, op="card"))
    assert "Error: the key or login no longer works" in harness.session.last_text()

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
    assert harness.alerts()[-1] == "Refreshing…"
    assert "Last successful poll" in harness.session.last_text()

    await harness.press(RefreshCb(acc=account, view="account"))
    assert "once a minute" in harness.alerts()[-1]


# Settings


async def test_settings_save_language_and_thresholds(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.press(SetCb(key="lang", value="en"))
    assert "<b>Settings</b>" in harness.session.last_text()

    await harness.press(SetCb(key="th", value="quiet_hours"))
    await harness.send("25:00-08:00")
    assert "did not understand" in harness.session.last_text()
    await harness.send("22:30 - 07:00")
    assert harness.hub.prefs.threshold("quiet_hours") == "22:30-07:00"

    await harness.press(SetCb(key="cur", value="USD"))
    assert harness.hub.prefs.currency == "USD"
    assert "Currency of totals: USD" in harness.session.last_text()


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
    text = summary_text(View("en", UTC), [d], None, "RUB", NOW)
    assert "No totals in RUB" in text


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
    assert "· hostwatch" in harness.session.last_text()
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

BACK, HOME = "⬅️ Назад", "🏠 Меню"
MENU = Nav(to="menu").pack()


def _assert_nav(harness: Harness, back: Any = None) -> None:
    """The last call edited the screen in place and it has «Menu» (and «Back» to `back`)."""
    assert isinstance(harness.session.calls[-1], EditMessageText)
    keyboard = harness.session.keyboard()
    assert keyboard.get(MENU) == HOME
    if back is not None:
        assert keyboard.get(back.pack()) == BACK


@pytest.mark.parametrize("to", ["summary", "servers", "balance", "integrations", "journal"])
async def test_sections_of_the_menu_lead_back_to_it(harness: Harness, to: str) -> None:
    harness.onboard("ru")
    harness.hub.accounts.add("fake", "main", {})

    await harness.press(Nav(to=to))
    _assert_nav(harness)

    await harness.press(Nav(to="menu"))
    assert isinstance(harness.session.calls[-1], EditMessageText)
    assert "Выберите раздел" in harness.session.last_text()


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
        (JournalCb(page=0), None),
    ]
    for pressed, back in screens:
        await harness.press(pressed)
        _assert_nav(harness, back)


async def test_stub_buttons_keep_the_screen(harness: Harness) -> None:
    harness.onboard("ru")
    account = harness.hub.accounts.add("fake", "main", {})
    await harness.poller.poll_account(account)
    await harness.press(ServerCb(acc=account, sid="1"))
    calls = len(harness.session.calls)

    await harness.press(ActionCb(acc=account, sid="1", act="renew"))
    await harness.press(Nav(to="order"))

    new = harness.session.calls[calls:]
    assert all(isinstance(call, AnswerCallbackQuery) for call in new)
    assert harness.alerts()[-2:] == ["Будет на этапе 10.", "Будет на этапе 13."]


async def test_menu_command_works_in_the_middle_of_a_question(harness: Harness) -> None:
    harness.onboard("en")
    await harness.press(SetCb(key="th", value="quiet_hours"))

    await harness.send("/menu")

    assert "Pick a section" in harness.session.last_text()
    await harness.send("22:00-07:00")  # no longer an answer to the question
    assert harness.hub.prefs.threshold("quiet_hours") != "22:00-07:00"


# Integrations: connected accounts, hosters to add on a screen of their own


async def test_integrations_show_connected_accounts_only(harness: Harness) -> None:
    harness.onboard("ru")

    await harness.press(Nav(to="integrations"))
    assert "Пока ничего не подключено" in harness.session.last_text()
    assert harness.session.keyboard()[Nav(to="connect").pack()] == "+ Подключить"
    assert not any(b.startswith("add:") for b in harness.session.buttons())

    account = harness.hub.accounts.add("fake", "main", {})
    await harness.press(Nav(to="integrations"))
    text = harness.session.last_text()
    assert "Fake hoster (main): подключено" in text
    assert "Aeza" not in text and "Timeweb" not in text
    assert AccountCb(acc=account, op="card").pack() in harness.session.buttons()
    assert not any(b.startswith("add:") for b in harness.session.buttons())


async def test_connect_screen_has_a_button_per_hoster(harness: Harness) -> None:
    harness.onboard("ru")
    harness.hub.accounts.add("fake", "main", {})

    await harness.press(Nav(to="connect"))

    keyboard = harness.session.keyboard()
    assert keyboard[AddCb(plugin="aeza").pack()] == "Aeza"
    assert keyboard[AddCb(plugin="timeweb").pack()] == "Timeweb Cloud"
    assert keyboard[AddCb(plugin="fake").pack()] == "+ ещё аккаунт Fake hoster"
    text = harness.session.last_text()
    assert "• Aeza — " in text
    assert "• Fake hoster" not in text

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
    assert "<b>Fake hoster (calm)</b>: серверов 2" in text
    assert "<b>Fake hoster (busy)</b>: серверов 3 · ⚠ есть проблемы" in text
    assert "srv-01" not in text  # servers are one level down
    keyboard = harness.session.keyboard()
    assert keyboard[HostCb(acc=calm).pack()] == "Fake hoster (calm) · 2"
    assert keyboard[HostCb(acc=troubled).pack()] == "Fake hoster (busy) · 3 ⚠"


async def test_servers_of_a_hoster_split_by_kind_and_turn_pages(harness: Harness) -> None:
    harness.onboard("ru")
    account = harness.hub.accounts.add("fake", "main", {})
    harness.hub.servers.replace(account, _many_servers(11), NOW)

    await harness.press(HostCb(acc=account))
    first = harness.session.last_text()
    servers = [b for b in harness.session.buttons() if b.startswith("s:")]
    assert len(servers) == 8
    assert "Страница 1 из 2" in first
    assert "Выделенные серверы" not in first  # eight VPS fill the first page
    assert HostCb(acc=account, page=1).pack() in harness.session.buttons()
    assert HostCb(acc=account, page=-1).pack() not in harness.session.buttons()

    await harness.press(HostCb(acc=account, page=1))
    second = harness.session.last_text()
    assert len([b for b in harness.session.buttons() if b.startswith("s:")]) == 3
    assert "Страница 2 из 2" in second
    assert second.index("<b>VPS</b>") < second.index("<b>Выделенные серверы</b>")
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
