"""Login links, the domain policy, cookie picking and the bot's side of browser login."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardMarkup
from aiohttp.test_utils import TestClient, TestServer

from hostwatch.bot.callbacks import AccountCb, AddCb, LabelCb, LoginCb, Nav
from hostwatch.bot.login import BotLogin, browser_name, parse_cookie_text, pick_cookies
from hostwatch.core.errors import AuthExpired
from hostwatch.core.models import AccountStatus
from hostwatch.core.plugin import BrowserAuth, PluginContext, cookie_header
from hostwatch.plugins.regxa import RegxaPlugin
from hostwatch.plugins.timeweb import TimewebPlugin
from hostwatch.storage.repositories import HISTORY_ACTIONS, AccountRepository
from hostwatch.web.browser import Policy, clamp_size, local_launcher
from hostwatch.web.login import EndReason, LoginEvents, LoginService, LoginTarget
from hostwatch.web.server import COOKIE, make_app
from tests.test_bot import Harness

AUTH = BrowserAuth(
    start_url="https://example.test/login",
    allowed_domains=("example.test",),
    cookies=("session", "WHMCS*"),
    extra_cookies=("__ddg*",),
)
TARGET = LoginTarget(plugin_id="x", hoster="Example", auth=AUTH)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# Links


async def test_link_works_once() -> None:
    service = LoginService(local_launcher)
    token = await service.issue(TARGET)
    assert len(token) >= 43
    assert service.redeem(token) == TARGET
    assert service.redeem(token) is None


async def test_link_expires_after_ten_minutes() -> None:
    clock = Clock()
    service = LoginService(local_launcher, clock=clock)
    token = await service.issue(TARGET)
    clock.now += 601
    assert service.redeem(token) is None


async def test_new_link_cancels_the_old_one() -> None:
    service = LoginService(local_launcher)
    old = await service.issue(TARGET)
    new = await service.issue(TARGET)
    assert service.redeem(old) is None
    assert service.redeem(new) == TARGET


class Recorder(LoginEvents):
    def __init__(self) -> None:
        self.opened_from: list[tuple[str, str]] = []
        self.ends: list[EndReason] = []

    async def opened(self, target: LoginTarget, ip: str, user_agent: str) -> None:
        self.opened_from.append((ip, user_agent))

    async def ended(self, target: LoginTarget, reason: EndReason) -> None:
        self.ends.append(reason)


async def test_session_ends_after_idle_time() -> None:
    events = Recorder()
    service = LoginService(local_launcher, events, idle_ttl=0.05)
    token = await service.issue(TARGET)
    sid = await service.open(token, "1.2.3.4", "UA")
    assert service.find(sid) is not None
    await asyncio.sleep(0.2)
    assert service.find(sid) is None
    assert events.ends == [EndReason.IDLE]


async def test_one_session_at_a_time() -> None:
    events = Recorder()
    service = LoginService(local_launcher, events)
    first = await service.open(await service.issue(TARGET), "1.2.3.4", "UA")
    second = await service.open(await service.issue(TARGET), "1.2.3.4", "UA")
    assert service.find(first) is None
    assert service.find(second) is not None
    assert events.ends == [EndReason.REPLACED]
    await service.close()


@pytest.fixture
async def page_client() -> AsyncIterator[tuple[TestClient[object, object], LoginService, Recorder]]:
    events = Recorder()
    service = LoginService(local_launcher, events)
    client: TestClient[object, object] = TestClient(TestServer(make_app(service, secure=False)))
    await client.start_server()
    yield client, service, events
    await service.close()
    await client.close()


async def test_page_link_opens_once_then_404(
    page_client: tuple[TestClient[object, object], LoginService, Recorder],
) -> None:
    client, service, events = page_client
    token = await service.issue(TARGET)

    head = await client.head(f"/login/{token}")
    assert head.status == 405  # a HEAD from a link preview does not burn the token

    first = await client.get(
        f"/login/{token}",
        allow_redirects=False,
        headers={"X-Forwarded-For": "203.0.113.7", "User-Agent": "phone"},
    )
    assert first.status == 303
    assert first.headers["Location"] == "/login/s"
    assert COOKIE in first.cookies
    assert events.opened_from == [("203.0.113.7", "phone")]

    page = await client.get("/login/s")
    assert page.status == 200
    assert "Example" in await page.text()
    assert page.headers["Cache-Control"] == "no-store"

    again = await client.get(f"/login/{token}", allow_redirects=False)
    assert again.status == 404


async def test_page_without_session_is_404(
    page_client: tuple[TestClient[object, object], LoginService, Recorder],
) -> None:
    client, _, _ = page_client
    assert (await client.get("/login/s")).status == 404
    assert (await client.get("/login/nonsense")).status == 404
    assert (await client.get("/login/ws")).status == 403


# Policy and cookies


def test_policy_allows_hoster_and_captcha_domains_only() -> None:
    policy = Policy(("timeweb.cloud",))
    assert policy.allows("https://timeweb.cloud/my/login")
    assert policy.allows("https://static.timeweb.cloud/app.js")
    assert policy.allows("https://www.google.com/recaptcha/api.js")
    assert policy.allows("https://challenges.cloudflare.com/turnstile/v0/api.js")
    assert policy.allows("data:image/png;base64,AAAA")
    assert not policy.allows("https://www.google.com/search?q=x")
    assert not policy.allows("https://eviltimeweb.cloud/")
    assert not policy.allows("https://mc.yandex.ru/metrika/tag.js")
    assert not policy.allows("file:///etc/passwd")
    assert not policy.allows("chrome://settings")


def test_viewport_size_is_clamped() -> None:
    assert clamp_size(390, 700) == (390, 700)
    assert clamp_size(10, 99999) == (320, 1600)
    assert clamp_size("x", None) == (400, 760)


def test_pick_cookies_takes_wanted_names_of_allowed_domains() -> None:
    cookies = [
        {"name": "session", "value": "s1", "domain": ".example.test"},
        {"name": "WHMCSabc", "value": "w1", "domain": "my.example.test"},
        {"name": "__ddg1_", "value": "d1", "domain": ".example.test"},
        {"name": "_ga", "value": "ga", "domain": ".example.test"},
        {"name": "session", "value": "evil", "domain": "accounts.google.com"},
    ]
    picked = pick_cookies(cookies, AUTH)
    assert picked == {"session": "s1", "WHMCSabc": "w1", "__ddg1_": "d1"}
    assert AUTH.missing(picked) == []
    assert AUTH.missing({"session": "s1"}) == ["WHMCS*"]
    assert cookie_header(picked | {"api_token": "t"}, AUTH) == "session=s1; WHMCSabc=w1; __ddg1_=d1"


def test_parse_cookie_text_formats() -> None:
    assert parse_cookie_text("Cookie: a=1; b=2=3") == {"a": "1", "b": "2=3"}
    assert parse_cookie_text('[{"name": "a", "value": "1", "domain": "x"}]') == {"a": "1"}
    assert parse_cookie_text('{"a": "1"}') == {"a": "1"}
    assert parse_cookie_text("nothing here") is None
    assert parse_cookie_text("[broken") is None


def test_browser_name() -> None:
    android = (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36"
    )
    iphone = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    )
    assert browser_name(android) == "Chrome, Android"
    assert browser_name(iphone) == "Safari, iPhone"
    assert browser_name("") == "?, ?"


@respx.mock
async def test_regxa_login_check() -> None:
    route = respx.get("https://regxa.com/api/auth/session")
    secrets = {"rgx.session-token": "tok-123456"}

    async def keep(_: object) -> None:
        return None

    async with httpx.AsyncClient() as http:
        plugin = RegxaPlugin(PluginContext(secrets, http, keep))
        route.respond(json={"user": {"email": "x"}, "expires": "2026-11-08"})
        await plugin.check_login()
        assert route.calls.last.request.headers["Cookie"] == "rgx.session-token=tok-123456"
        route.respond(json={})
        with pytest.raises(AuthExpired):
            await plugin.check_login()


def test_written_plugins_are_not_login_only() -> None:
    assert not TimewebPlugin.login_only and not RegxaPlugin.login_only


# The bot's side


class Sent:
    """The owner's chat as `BotLogin` sees it: posted, deleted and edited messages."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.by_id: dict[int, str] = {}
        self.deleted: list[int] = []
        self.edited: dict[int, str] = {}
        # Telegram refuses to delete messages older than 48 hours.
        self.can_delete = True

    async def post(self, text: str, markup: object = None) -> int:
        self.texts.append(text)
        message_id = 100 + len(self.texts)
        self.by_id[message_id] = text
        return message_id

    async def delete(self, message_id: int) -> bool:
        if not self.can_delete:
            return False
        self.deleted.append(message_id)
        return True

    async def edit(self, message_id: int, text: str) -> bool:
        self.edited[message_id] = text
        return True

    def visible(self) -> list[str]:
        """Texts still in the chat as they read now."""
        return [
            self.edited.get(message_id, text)
            for message_id, text in self.by_id.items()
            if message_id not in self.deleted
        ]


@pytest.fixture
async def login_harness(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> AsyncIterator[tuple[Harness, Sent]]:
    h = Harness(conn, accounts)
    h.hub.registry._plugins["regxa"] = RegxaPlugin  # type: ignore[attr-defined]
    sent = Sent()
    h.hub.login = BotLogin(
        h.hub,
        lambda events: LoginService(local_launcher, events),
        sent,
        lambda _: httpx.AsyncClient(),
        domain="hw.example",
    )
    yield h, sent
    await h.hub.login.close()
    await h.poller.stop()
    await h.bot.session.close()


async def test_relogin_button_sends_a_link(login_harness: tuple[Harness, Sent]) -> None:
    h, sent = login_harness
    h.onboard("ru")
    account = h.hub.accounts.add("timeweb", "main", {"api_token": "tw-token-1"})

    await h.press(AccountCb(acc=account, op="relogin"))

    # The link is a button in the menu itself, not a message of its own.
    assert isinstance(h.session.calls[-1], EditMessageText)
    assert "https://hw.example/login/" in h.session.last_text()
    assert AccountCb(acc=account, op="card").pack() in h.session.buttons()
    assert any(url.startswith("https://hw.example/login/") for url in _urls(h))
    assert sent.texts == []
    assert h.hub.login is not None
    assert h.hub.login.service._links  # a link waits to be opened


async def test_add_browser_plugin_asks_label_then_sends_link(
    login_harness: tuple[Harness, Sent],
) -> None:
    h, sent = login_harness
    h.onboard("en")

    await h.press(AddCb(plugin="regxa"))
    await h.press(LabelCb())

    assert "https://hw.example/login/" in h.session.last_text()
    assert sent.texts == []
    assert h.hub.accounts.all() == []  # the account appears once the login is done


def _urls(h: Harness) -> list[str]:
    """URL buttons under the last screen."""
    for call in reversed(h.session.calls):
        if isinstance(call, SendMessage | EditMessageText):
            markup = call.reply_markup
            assert isinstance(markup, InlineKeyboardMarkup)
            return [b.url for row in markup.inline_keyboard for b in row if b.url]
    return []


async def test_finish_merges_cookies_into_the_account(login_harness: tuple[Harness, Sent]) -> None:
    h, sent = login_harness
    h.onboard("ru")
    account = h.hub.accounts.add(
        "timeweb", "main", {"api_token": "tw-token-1", "refresh_token": "old-refresh"}
    )
    h.hub.accounts.set_status(account, AccountStatus.RELOGIN, "auth_expired")
    assert h.hub.login is not None
    target = h.hub.login.target(TimewebPlugin, account)
    cookies = [
        {"name": "refresh_token", "value": "new-refresh-1", "domain": "timeweb.cloud"},
        {"name": "device_token", "value": "device-1", "domain": ".timeweb.cloud"},
        {"name": "_ym_uid", "value": "metrics", "domain": ".timeweb.cloud"},
    ]

    await h.hub.login.finish(target, cookies)

    assert h.hub.accounts.load_secrets(account) == {
        "api_token": "tw-token-1",
        "refresh_token": "new-refresh-1",
        "device_token": "device-1",
    }
    assert h.hub.accounts.get(account).status is AccountStatus.OK
    assert sent.texts[-1] == "🟢 Timeweb Cloud (main) подключён."
    assert h.hub.journal.page(exclude=HISTORY_ACTIONS)[0].action == "login"


async def test_cookie_command_saves_pasted_cookies(login_harness: tuple[Harness, Sent]) -> None:
    h, _ = login_harness
    h.onboard("en")
    account = h.hub.accounts.add("timeweb", "main", {"api_token": "tw-token-1"})

    await h.send("/cookie main")
    assert "refresh_token, device_token" in h.session.last_text()
    await h.send("refresh_token=only-refresh-1")
    assert "Cookies missing: device_token" in h.session.last_text()
    await h.send("refresh_token=refresh-2222; device_token=device-2222; junk=1")

    assert h.hub.accounts.load_secrets(account) == {
        "api_token": "tw-token-1",
        "refresh_token": "refresh-2222",
        "device_token": "device-2222",
    }
    assert "refresh-2222" not in "".join(h.session.texts())


async def test_cookie_command_lists_targets(login_harness: tuple[Harness, Sent]) -> None:
    h, _ = login_harness
    h.onboard("en")
    await h.send("/cookie")
    assert "regxa" in h.session.last_text()
    await h.send("/cookie nobody")
    assert "No account or hoster" in h.session.last_text()


async def test_not_me_ends_the_session(login_harness: tuple[Harness, Sent]) -> None:
    h, sent = login_harness
    h.onboard("en")
    assert h.hub.login is not None
    service = h.hub.login.service
    target = h.hub.login.target(RegxaPlugin, None, "Regxa")
    sid = await service.open(await service.issue(target), "198.51.100.1", "Firefox/150 Android")
    assert "198.51.100.1, Firefox, Android" in sent.texts[-1]

    await h.press(LoginCb(op="kill"))

    assert service.find(sid) is None
    assert sent.texts[-1] == "Regxa login closed with «Not me»."


async def test_plugin_with_api_key_needs_an_account_first(
    login_harness: tuple[Harness, Sent],
) -> None:
    h, _ = login_harness
    assert h.hub.login is not None
    assert h.hub.login.resolve("timeweb") is None  # Timeweb reads by API key
    assert h.hub.login.resolve("regxa") is not None  # a new browser-only account
    account = h.hub.accounts.add("timeweb", "main", {"api_token": "tw-token-1"})
    found = h.hub.login.resolve("timeweb")
    assert found is not None and found.account_id == account


# The link is shown in the menu; the screen changes by itself while the menu shows it.
# Once the menu moved on, the news comes as notifications, and «link opened» goes at the end.


async def _issue(h: Harness, target: LoginTarget) -> str:
    """Send a link the way `hostwatch login` does (a new menu below); its token."""
    assert h.hub.login is not None
    url = await h.hub.login.send_link(target)
    assert url is not None
    return url.rsplit("/", 1)[1]


CONNECTED = "🟢 Timeweb Cloud (main) подключён."
COOKIES = [
    {"name": "refresh_token", "value": "new-refresh-1", "domain": "timeweb.cloud"},
    {"name": "device_token", "value": "device-1", "domain": ".timeweb.cloud"},
]


async def test_login_screen_follows_the_login_in_the_menu(
    login_harness: tuple[Harness, Sent],
) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    account = h.hub.accounts.add("timeweb", "main", {"api_token": "tw-token-1"})
    target = h.hub.login.target(TimewebPlugin, account)
    token = await _issue(h, target)
    assert "https://hw.example/login/" in h.session.last_text()
    menu_id = h.hub.prefs.menu_id

    await h.hub.login.service.open(token, "198.51.100.1", "Firefox/150 Android")
    assert "198.51.100.1, Firefox, Android" in h.session.last_text()
    assert LoginCb(op="kill").pack() in h.session.buttons()

    await h.hub.login.finish(target, COOKIES)

    assert h.session.last_text() == CONNECTED
    assert h.hub.prefs.menu_id == menu_id  # all of it in the same message
    assert sent.texts == []


async def test_login_news_are_notifications_once_the_menu_moved_on(
    login_harness: tuple[Harness, Sent],
) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    account = h.hub.accounts.add("timeweb", "main", {"api_token": "tw-token-1"})
    target = h.hub.login.target(TimewebPlugin, account)
    token = await _issue(h, target)
    await h.press(Nav(to="servers"))
    servers = h.session.last_text()

    await h.hub.login.service.open(token, "198.51.100.1", "Firefox/150 Android")
    assert "198.51.100.1, Firefox, Android" in sent.visible()[0]
    await h.hub.login.finish(target, COOKIES)

    assert sent.visible() == [CONNECTED]  # «link opened» went
    assert h.session.last_text() == servers  # the menu was left alone


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        (EndReason.CANCELLED, "Вход в Regxa отменён."),
        (EndReason.IDLE, "Вход в Regxa закрыт: 15 минут без действий."),
        (EndReason.REJECTED, "Вход в Regxa закрыт кнопкой «Это не я»."),
    ],
)
async def test_login_end_shows_in_the_menu(
    login_harness: tuple[Harness, Sent], reason: EndReason, text: str
) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    target = h.hub.login.target(RegxaPlugin, None, "Regxa")
    service = h.hub.login.service
    await service.open(await _issue(h, target), "198.51.100.1", "Firefox/150 Android")

    await service.end(reason)

    assert h.session.last_text() == text
    assert sent.visible() == []


async def test_unopened_link_expires_in_the_menu(login_harness: tuple[Harness, Sent]) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    h.hub.login.service.link_ttl = 0.05
    await _issue(h, h.hub.login.target(RegxaPlugin, None, "Regxa"))
    assert _urls(h)

    await asyncio.sleep(0.2)

    assert "Ссылка входа в Regxa истекла" in h.session.last_text()
    assert _urls(h) == []
    assert sent.visible() == []


async def test_expired_link_leaves_a_moved_menu_alone(login_harness: tuple[Harness, Sent]) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    h.hub.login.service.link_ttl = 0.05
    await _issue(h, h.hub.login.target(RegxaPlugin, None, "Regxa"))
    await h.press(Nav(to="journal"))
    calls = len(h.session.calls)

    await asyncio.sleep(0.2)

    assert len(h.session.calls) == calls
    assert sent.visible() == []


async def test_opened_link_outlives_its_ttl(login_harness: tuple[Harness, Sent]) -> None:
    h, _ = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    h.hub.login.service.link_ttl = 0.05
    token = await _issue(h, h.hub.login.target(RegxaPlugin, None, "Regxa"))
    await h.hub.login.service.open(token, "198.51.100.1", "Firefox/150 Android")

    await asyncio.sleep(0.2)

    assert "198.51.100.1" in h.session.last_text()  # the session runs; its end cleans up


async def test_new_link_replaces_the_old_one(login_harness: tuple[Harness, Sent]) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    target = h.hub.login.target(RegxaPlugin, None, "Regxa")
    first = await _issue(h, target)

    second = await _issue(h, target)

    assert second != first
    assert second in h.session.last_text()
    assert sent.visible() == []


async def test_old_link_notice_is_edited_when_it_cannot_be_deleted(
    login_harness: tuple[Harness, Sent],
) -> None:
    h, sent = login_harness
    h.onboard("ru")
    assert h.hub.login is not None
    sent.can_delete = False
    target = h.hub.login.target(RegxaPlugin, None, "Regxa")
    token = await _issue(h, target)
    await h.press(Nav(to="menu"))
    await h.hub.login.service.open(token, "198.51.100.1", "Firefox/150 Android")

    await _issue(h, target)

    assert sent.visible() == ["Ссылка больше не действует."]
    assert "https://hw.example/login/" in h.session.last_text()
