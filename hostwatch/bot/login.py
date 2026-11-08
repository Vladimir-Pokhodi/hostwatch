"""Browser login from the bot's side: links, «Not me», storing the session, `/cookie`."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.errors import AuthExpired, describe, explain
from hostwatch.core.logs import register_secrets
from hostwatch.core.models import AccountStatus
from hostwatch.core.plugin import (
    BrowserAuth,
    Plugin,
    PluginContext,
    Secrets,
    SupportsLoginCheck,
    browser_auth,
)
from hostwatch.locales import t
from hostwatch.storage.repositories import JournalEntry, NotFound
from hostwatch.web.login import EndReason, LoginEvents, LoginService, LoginTarget, NotDone

from .callbacks import LoginCb, Nav
from .hub import key_fields

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


class OwnerChat(Protocol):
    """The owner's chat: messages the bot can later delete or edit by id."""

    async def post(self, text: str, markup: InlineKeyboardMarkup | None = None) -> int | None:
        """Send a message; its id, or None if it did not go through."""
        ...

    async def delete(self, message_id: int) -> bool: ...

    async def edit(self, message_id: int, text: str) -> bool: ...


def pick_cookies(cookies: list[dict[str, Any]], auth: BrowserAuth) -> dict[str, str]:
    """The cookies the plugin asked for, from the hoster's own domains only."""
    picked: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".")
        name, value = str(cookie.get("name", "")), str(cookie.get("value", ""))
        if name and value and auth.allows_host(domain) and auth.wants(name):
            picked[name] = value
    return picked


def parse_cookie_text(text: str) -> dict[str, str] | None:
    """`a=b; c=d` (a Cookie header, maybe with the `Cookie:` prefix), or JSON: a list of
    `{"name", "value"}` as cookie editors export it, or a `{name: value}` object."""
    text = text.strip()
    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str | int)}
        if isinstance(data, list):
            return {
                str(c["name"]): str(c["value"])
                for c in data
                if isinstance(c, dict) and "name" in c and "value" in c
            }
        return None
    text = re.sub(r"^cookie:\s*", "", text, flags=re.IGNORECASE)
    pairs = [part.strip().partition("=") for part in re.split(r";|\n", text)]
    found = {name.strip(): value.strip() for name, sep, value in pairs if sep and name.strip()}
    return found or None


def browser_name(user_agent: str) -> str:
    """«Chrome, Android» from a User-Agent; good enough to tell one's own phone."""
    ua = user_agent
    browser = next(
        (
            name
            for token, name in (
                ("YaBrowser", "Yandex Browser"),
                ("Edg/", "Edge"),
                ("OPR/", "Opera"),
                ("Firefox/", "Firefox"),
                ("CriOS", "Chrome"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if token in ua
        ),
        "?",
    )
    system = next(
        (
            name
            for token, name in (
                ("Android", "Android"),
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Windows", "Windows"),
                ("Mac OS X", "macOS"),
                ("Linux", "Linux"),
            )
            if token in ua
        ),
        "?",
    )
    return f"{browser}, {system}"


class BotLogin(LoginEvents):
    def __init__(
        self,
        hub: Hub,
        service_factory: Callable[[LoginEvents], LoginService],
        chat: OwnerChat,
        make_http: Callable[[str], httpx.AsyncClient],
        domain: str | None = None,
    ) -> None:
        self.hub = hub
        self.service = service_factory(self)
        self._chat = chat
        self._make_http = make_http
        self._domain = domain
        # The link message and «link opened» of the current login: they go once it is over.
        self._shown: list[int] = []
        self._opened = False
        self._expiry: asyncio.Task[None] | None = None

    # Links

    def base_url(self) -> str | None:
        host = self._domain or self.hub.own_ip
        if not host:
            return None
        return f"https://{host}" if ":" not in host else f"https://[{host}]"

    def target(self, plugin: type[Plugin], account_id: int | None, label: str = "") -> LoginTarget:
        auth = browser_auth(plugin)
        assert auth is not None
        title = plugin.meta.name
        if account_id is not None:
            account = self.hub.accounts.get(account_id)
            if account.label not in ("", title):
                title = f"{title} ({account.label})"
        return LoginTarget(
            plugin_id=plugin.meta.id,
            hoster=title,
            auth=auth,
            account_id=account_id,
            label=label,
            lang=self.hub.prefs.lang,
        )

    def resolve(self, text: str) -> LoginTarget | None:
        """An account by id or label, or a plugin id for a new account; browser login only."""
        text = text.strip()
        registry = self.hub.registry
        candidates = []
        if text.isdigit():
            try:
                candidates = [self.hub.accounts.get(int(text))]
            except NotFound:
                candidates = []
        if not candidates:
            candidates = self.hub.accounts.find(text)
        if len(candidates) == 1 and candidates[0].plugin_id in registry:
            plugin = registry.get(candidates[0].plugin_id)
            if browser_auth(plugin) is not None:
                return self.target(plugin, candidates[0].id)
            return None
        if text in registry and browser_auth(registry.get(text)) is not None:
            plugin = registry.get(text)
            own = [a for a in self.hub.accounts.all() if a.plugin_id == text]
            if len(own) == 1:
                return self.target(plugin, own[0].id)
            if own or key_fields(plugin):
                # Several accounts: name one. An API key is needed: add the account first.
                return None
            return self.target(plugin, None, plugin.meta.name)
        return None

    async def link(self, target: LoginTarget) -> str | None:
        base = self.base_url()
        if base is None:
            return None
        token = await self.service.issue(target)
        log.info("login link issued for %s", target.plugin_id)
        return f"{base}/login/{token}"

    def link_message(self, target: LoginTarget, url: str) -> tuple[str, InlineKeyboardMarkup]:
        kb = InlineKeyboardBuilder()
        kb.button(text=t("login.open", target.lang), url=url)
        text = t("login.link", target.lang, hoster=escape(target.hoster), url=escape(url))
        return text, kb.as_markup()

    async def send_link(self, target: LoginTarget) -> str | None:
        """Issue a link and message it to the owner; the URL, or None without an address.

        The new link replaces the old one, so the old link's messages go."""
        if self.base_url() is None:
            return None
        await self._drop(target.lang)
        url = await self.link(target)
        if url is None:
            return None
        text, markup = self.link_message(target, url)
        await self._show(text, markup)
        self._opened = False
        self._expiry = asyncio.get_running_loop().create_task(self._expire(target.lang))
        return url

    async def _expire(self, lang: str) -> None:
        """A link nobody opened dies after LINK_TTL; its message goes with it."""
        await asyncio.sleep(self.service.link_ttl)
        if not self._opened:
            self._expiry = None
            await self._drop(lang)

    async def _show(self, text: str, markup: InlineKeyboardMarkup | None) -> None:
        message_id = await self._chat.post(text, markup)
        if message_id is not None:
            self._shown.append(message_id)

    async def _drop(self, lang: str) -> None:
        """Delete the messages of the current login. Telegram does not delete messages
        older than 48 hours; those are edited to say the link is dead."""
        expiry, self._expiry = self._expiry, None
        if expiry is not None and expiry is not asyncio.current_task():
            expiry.cancel()
        shown, self._shown = self._shown, []
        for message_id in shown:
            if not await self._chat.delete(message_id):
                await self._chat.edit(message_id, t("bot.login.link_gone", lang))

    def _home(self, lang: str) -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        kb.button(text=t("bot.button.home", lang), callback_data=Nav(to="menu"))
        return kb.as_markup()

    async def close(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        await self.service.close()

    # Events of the login page

    async def opened(self, target: LoginTarget, ip: str, user_agent: str) -> None:
        kb = InlineKeyboardBuilder()
        kb.button(text=t("login.not_me", target.lang), callback_data=LoginCb(op="kill"))
        text = t(
            "login.opened",
            target.lang,
            hoster=escape(target.hoster),
            ip=escape(ip),
            browser=escape(browser_name(user_agent)),
        )
        log.info("login link for %s opened from %s", target.plugin_id, ip)
        self._opened = True
        await self._show(text, kb.as_markup())

    async def ended(self, target: LoginTarget, reason: EndReason) -> None:
        # A new link cleans up after the old one itself; «Done» does it in `store`.
        if reason in (EndReason.REPLACED, EndReason.DONE):
            return
        await self._drop(target.lang)
        text = t(f"login.ended.{reason.value}", target.lang, hoster=escape(target.hoster))
        await self._chat.post(text, self._home(target.lang))

    async def finish(self, target: LoginTarget, cookies: list[dict[str, Any]]) -> str:
        picked = pick_cookies(cookies, target.auth)
        register_secrets(picked.values())
        missing = target.auth.missing(picked)
        if missing:
            # Names only, never values: they tell which cookie a plugin waits for in vain.
            names = sorted({str(c.get("name")) for c in cookies})
            log.info("login to %s not done: no %s; cookies: %s", target.plugin_id, missing, names)
            raise NotDone("missing")
        return await self.store(target, picked, via="browser")

    async def store(self, target: LoginTarget, cookies: dict[str, str], via: str) -> str:
        """Check the session from this server and save it. Raises NotDone("check")."""
        hub = self.hub
        plugin = hub.registry.get(target.plugin_id)
        old: Secrets = {}
        if target.account_id is not None:
            old = hub.accounts.load_secrets(target.account_id)
        secrets = {k: v for k, v in old.items() if not target.auth.wants(k)} | cookies
        unchecked = ""

        async def keep(new: Secrets) -> None:
            register_secrets(new.values())

        async with self._make_http(target.plugin_id) as http:
            instance = plugin(PluginContext(secrets, http, keep))
            if isinstance(instance, SupportsLoginCheck):
                try:
                    await instance.check_login()
                except AuthExpired:
                    raise NotDone("check") from None
                except Exception as exc:
                    # Anti-bot pages and timeouts: the cookies are there, keep them.
                    unchecked = describe(exc)
                    log.warning("login check of %s failed: %s", target.plugin_id, unchecked)
            secrets = dict(instance.ctx.secrets)

        account_id = target.account_id
        if account_id is None:
            account_id = hub.accounts.add(plugin.meta.id, self._free_label(target), secrets)
        else:
            hub.accounts.save_secrets(account_id, secrets)
            hub.accounts.set_status(account_id, AccountStatus.OK)
        hub.journal.add(
            JournalEntry(
                at=hub.now(),
                actor="owner",
                account_id=account_id,
                action="login",
                params={"via": via},
                response=unchecked,
                ok=True,
            )
        )
        hub.poller.start_account(account_id)
        lang = target.lang
        reason = explain(unchecked, lang) if unchecked else ""
        if unchecked:
            text = t("login.connected_unchecked", lang, hoster=escape(target.hoster), reason=reason)
        else:
            text = t("login.connected", lang, hoster=escape(target.hoster))
        await self._chat.post(text, self._home(lang))
        await self._drop(lang)
        return reason

    def _free_label(self, target: LoginTarget) -> str:
        taken = {a.label for a in self.hub.accounts.all() if a.plugin_id == target.plugin_id}
        label = target.label or target.hoster
        n = 2
        candidate = label
        while candidate in taken:
            candidate = f"{label} {n}"
            n += 1
        return candidate
