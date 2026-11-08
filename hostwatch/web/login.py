"""Login links and the one login session that may run at a time (spec, decision 6).

A link carries a one-time token: it works once, within LINK_TTL. Opening it burns the token
and gives the browser a session cookie. The session ends after IDLE_TTL without input,
on «Done», «Cancel», «Not me», or when a new link is issued.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hostwatch.core.plugin import BrowserAuth

from .browser import BrowserSession, Launcher, Policy

log = logging.getLogger(__name__)

LINK_TTL = 10 * 60.0
IDLE_TTL = 15 * 60.0
TOKEN_BYTES = 32


@dataclass(frozen=True)
class LoginTarget:
    """What the login is for: an existing account, or a new one when `account_id` is None."""

    plugin_id: str
    hoster: str
    auth: BrowserAuth
    account_id: int | None = None
    label: str = ""
    lang: str = "ru"


class EndReason(StrEnum):
    DONE = "done"
    CANCELLED = "cancelled"
    IDLE = "idle"
    REJECTED = "rejected"
    REPLACED = "replaced"
    FAILED = "failed"


class NotDone(Exception):
    """The login is not finished yet; the message tells the owner what is missing."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class LoginEvents:
    """What the bot does when things happen to a login. The defaults do nothing (tests)."""

    async def opened(self, target: LoginTarget, ip: str, user_agent: str) -> None:
        return None

    async def finish(self, target: LoginTarget, cookies: list[dict[str, Any]]) -> str:
        """Check and store the session; return a short note for the page. Raise NotDone."""
        return ""

    async def ended(self, target: LoginTarget, reason: EndReason) -> None:
        return None


@dataclass
class Link:
    target: LoginTarget
    created: float


@dataclass
class Session:
    id: str
    target: LoginTarget
    browser: BrowserSession
    last_input: float
    ended: EndReason | None = None
    notes: list[dict[str, str]] = field(default_factory=list)
    sink: Callable[[dict[str, Any]], None] | None = None

    def push(self, message: dict[str, Any]) -> None:
        if self.sink is not None:
            self.sink(message)


class LoginService:
    def __init__(
        self,
        launcher: Launcher,
        events: LoginEvents | None = None,
        clock: Callable[[], float] = time.monotonic,
        link_ttl: float = LINK_TTL,
        idle_ttl: float = IDLE_TTL,
    ) -> None:
        self._launcher = launcher
        self.events = events or LoginEvents()
        self._clock = clock
        self.link_ttl = link_ttl
        self.idle_ttl = idle_ttl
        self._links: dict[str, Link] = {}
        self.session: Session | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._finishing = asyncio.Lock()

    # Links

    async def issue(self, target: LoginTarget) -> str:
        """A new link. Older links and a running session end: one login at a time."""
        self._links.clear()
        await self.end(EndReason.REPLACED)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._links[token] = Link(target, self._clock())
        return token

    def busy(self) -> bool:
        """A login runs or a fresh link may be opened any moment: the browser is spoken for."""
        if self.session is not None:
            return True
        now = self._clock()
        return any(now - link.created <= self.link_ttl for link in self._links.values())

    def redeem(self, token: str) -> LoginTarget | None:
        """Burn the token. None if it is unknown, used or too old."""
        link = self._links.pop(token, None)
        if link is None or self._clock() - link.created > self.link_ttl:
            return None
        return link.target

    async def open(self, token: str, ip: str, user_agent: str) -> str | None:
        """Open a link: the id of the new session, or None (the page answers 404)."""
        target = self.redeem(token)
        if target is None:
            return None
        await self.end(EndReason.REPLACED)
        sid = secrets.token_urlsafe(TOKEN_BYTES)
        holder: list[Session] = []
        browser = BrowserSession(
            self._launcher,
            target.auth.start_url,
            Policy(target.auth.allowed_domains),
            lambda kind, value: holder[0].push({"t": kind, "v": value}) if holder else None,
        )
        session = Session(sid, target, browser, self._clock())
        holder.append(session)
        self.session = session
        self._watchdog = asyncio.get_running_loop().create_task(self._watch(session))
        await self._safe(self.events.opened(target, ip, user_agent))
        return sid

    def find(self, sid: str | None) -> Session | None:
        session = self.session
        if sid is None or session is None or session.ended is not None:
            return None
        if not secrets.compare_digest(session.id, sid):
            return None
        return session

    # Session

    def touch(self, session: Session) -> None:
        session.last_input = self._clock()

    async def finish(self, session: Session) -> str:
        """«Done»: hand the cookies to the plugin. Raises NotDone if the login is not there."""
        async with self._finishing:
            if session.ended is not None:
                raise NotDone("ended")
            cookies = await session.browser.cookies()
            note = await self.events.finish(session.target, cookies)
            session.push({"t": "saved", "v": note})
            await self.end(EndReason.DONE, session)
            return note

    async def end(self, reason: EndReason, session: Session | None = None) -> None:
        session = session or self.session
        if session is None or session.ended is not None:
            return
        session.ended = reason
        if self.session is session:
            self.session = None
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        session.push({"t": "end", "v": reason.value})
        await session.browser.close()
        log.info("login session for %s ended: %s", session.target.plugin_id, reason.value)
        if reason is not EndReason.DONE:
            await self._safe(self.events.ended(session.target, reason))

    async def close(self) -> None:
        self._links.clear()
        await self.end(EndReason.CANCELLED)

    async def _watch(self, session: Session) -> None:
        while session.ended is None:
            left = session.last_input + self.idle_ttl - self._clock()
            if left <= 0:
                await self.end(EndReason.IDLE, session)
                return
            await asyncio.sleep(min(left, 30.0))

    @staticmethod
    async def _safe(call: Awaitable[None]) -> None:
        try:
            await call
        except Exception:
            log.exception("login event handler failed")
