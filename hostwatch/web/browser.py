"""One Chromium tab, streamed as JPEG frames and driven by mouse, wheel and keyboard input.

The browser runs in the `browser` container (`playwright run-server`); tests launch a
local one. Frames come from `Page.startScreencast`, input goes to `Input.*` over the same
CDP session. Requests to hosts outside the policy are aborted by `context.route`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    Request,
    Route,
    WebSocketRoute,
    async_playwright,
)

from hostwatch.core.http import USER_AGENT
from hostwatch.core.plugin import host_in

log = logging.getLogger(__name__)

# Domains of captcha and anti-bot checks hosters put on their login pages. An entry with
# a path allows only that path: www.google.com is there for reCAPTCHA, not for search.
CAPTCHA_DOMAINS = (
    "www.google.com/recaptcha/",
    "www.gstatic.com/recaptcha/",
    "recaptcha.net",
    "www.recaptcha.net",
    "hcaptcha.com",
    "challenges.cloudflare.com",
    "smartcaptcha.yandexcloud.net",
    "smartcaptcha.cloud.yandex.ru",
    "captcha-api.yandex.ru",
    "ddos-guard.net",
)
JPEG_QUALITY = 60
SCALE = 2
MIN_SIZE = (320, 400)
MAX_SIZE = (1280, 1600)
DEFAULT_SIZE = (400, 760)
KEYS = {
    "Enter": ("Enter", "Enter", 13, "\r"),
    "Backspace": ("Backspace", "Backspace", 8, ""),
    "Tab": ("Tab", "Tab", 9, ""),
    "Escape": ("Escape", "Escape", 27, ""),
}

Launcher = Callable[[Playwright], Awaitable[Browser]]
FrameSink = Callable[[dict[str, Any]], None]


def remote_launcher(ws_endpoint: str) -> Launcher:
    """Connects to `playwright run-server`; it starts Chromium for this connection only."""
    # The full Chromium build in new headless mode: the headless shell is easier to spot.
    options = json.dumps({"headless": True, "channel": "chromium"})

    async def launch(pw: Playwright) -> Browser:
        return await pw.chromium.connect(
            ws_endpoint, headers={"x-playwright-launch-options": options}, timeout=30_000
        )

    return launch


def local_launcher(pw: Playwright) -> Awaitable[Browser]:
    return pw.chromium.launch(headless=True)


@dataclass(frozen=True)
class Policy:
    """Where the tab may go: the hoster's domains plus captcha domains."""

    domains: tuple[str, ...]

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme in ("data", "blob", "about"):
            return True
        if parts.scheme not in ("http", "https", "ws", "wss") or not parts.hostname:
            return False
        if host_in(parts.hostname, self.domains):
            return True
        for entry in CAPTCHA_DOMAINS:
            host, _, path = entry.partition("/")
            if path:
                if parts.hostname == host and parts.path.startswith("/" + path):
                    return True
            elif host_in(parts.hostname, (host,)):
                return True
        return False


def clamp_size(width: object, height: object) -> tuple[int, int]:
    def num(value: object, default: int, low: int, high: int) -> int:
        if not isinstance(value, int | float):
            return default
        return max(low, min(high, int(value)))

    return (
        num(width, DEFAULT_SIZE[0], MIN_SIZE[0], MAX_SIZE[0]),
        num(height, DEFAULT_SIZE[1], MIN_SIZE[1], MAX_SIZE[1]),
    )


class BrowserSession:
    """A tab of the hoster's site. Frames go to `sink`; `notice` gets short status events."""

    def __init__(
        self,
        launcher: Launcher,
        start_url: str,
        policy: Policy,
        notice: Callable[[str, str], None],
    ) -> None:
        self._launcher = launcher
        self._start_url = start_url
        self._policy = policy
        self._notice = notice
        self._sink: FrameSink | None = None
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        self._pages: list[Page] = []
        self._last_frame: dict[str, Any] | None = None
        self._blocked: set[str] = set()
        self._lock = asyncio.Lock()
        self._pressed = False
        self._loaded = False
        self.closed = False

    @property
    def started(self) -> bool:
        return self._context is not None

    @property
    def url(self) -> str:
        return self._page.url if self._page is not None else ""

    @property
    def blocked_hosts(self) -> set[str]:
        return set(self._blocked)

    async def start(self, size: tuple[int, int]) -> None:
        width, height = size
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._launcher(self._pw)
            self._context = await self._browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=SCALE,
                user_agent=USER_AGENT,
                locale="ru-RU",
                service_workers="block",
                accept_downloads=False,
            )
            # Chromium under automation says so; hosters' anti-bot scripts read it.
            await self._context.add_init_script(
                "Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => false})"
            )
            await self._context.route("**/*", self._route)
            await self._context.route_web_socket("**/*", self._route_ws)
            self._context.on("page", self._on_page)
            page = await self._context.new_page()
            await self._watch(page)
        except BaseException:
            await self.close()
            raise
        # The first page may take long; frames start arriving while it loads.
        asyncio.get_running_loop().create_task(self._goto(page, self._start_url))

    async def _goto(self, page: Page, url: str) -> None:
        # Heavy cabinets take ~20 s to draw on one CPU; the page shows «Opening» until `load`.
        try:
            await page.goto(url, wait_until="load", timeout=90_000)
        except Exception as exc:
            if not self.closed:
                log.warning("login page did not open: %s", type(exc).__name__)
                self._notice("load_failed", urlsplit(url).hostname or "")
        if not self.closed:
            self._loaded = True
            self._notice("loaded", "")

    def attach(self, sink: FrameSink | None) -> None:
        """Where frames go; the last frame is sent at once so a reconnected page is not blank."""
        self._sink = sink
        if sink is not None and self._loaded:
            sink({"t": "loaded", "v": ""})
        if sink is not None and self._last_frame is not None:
            sink(self._last_frame)

    async def resize(self, size: tuple[int, int]) -> None:
        if self._page is not None:
            width, height = size
            await self._page.set_viewport_size({"width": width, "height": height})

    async def _route(self, route: Route, request: Request) -> None:
        if self._policy.allows(request.url):
            await route.fallback()
            return
        host = urlsplit(request.url).hostname or "?"
        if host not in self._blocked:
            self._blocked.add(host)
            log.info("login browser: blocked a request to %s", host)
        if request.is_navigation_request() and request.frame == request.frame.page.main_frame:
            self._notice("blocked", host)
            # A 204 answer cancels the navigation: the tab stays on the hoster's page
            # instead of showing Chromium's error page.
            await route.fulfill(status=204)
            return
        await route.abort("blockedbyclient")

    async def _route_ws(self, ws: WebSocketRoute) -> None:
        if self._policy.allows(ws.url):
            ws.connect_to_server()
        else:
            await ws.close()

    async def _on_page(self, page: Page) -> None:
        # Popups and new tabs: show the newest one, go back to the previous when it closes.
        if page is not self._page:
            await self._watch(page)

    async def _watch(self, page: Page) -> None:
        self._pages.append(page)
        page.on("close", lambda p: asyncio.get_running_loop().create_task(self._closed_page(p)))
        page.on("framenavigated", self._navigated)
        await self._show(page)

    async def _closed_page(self, page: Page) -> None:
        if page in self._pages:
            self._pages.remove(page)
        if page is self._page and self._pages and not self.closed:
            await self._show(self._pages[-1])

    def _navigated(self, frame: Any) -> None:
        if self._page is not None and frame == self._page.main_frame:
            self._notice("host", urlsplit(frame.url).hostname or "")

    async def _show(self, page: Page) -> None:
        async with self._lock:
            if self._cdp is not None:
                with contextlib.suppress(Exception):
                    await self._cdp.send("Page.stopScreencast")
                    await self._cdp.detach()
            assert self._context is not None
            self._page = page
            self._cdp = cdp = await self._context.new_cdp_session(page)
            cdp.on("Page.screencastFrame", lambda params: self._frame(cdp, params))
            size = page.viewport_size or {"width": DEFAULT_SIZE[0], "height": DEFAULT_SIZE[1]}
            await cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": JPEG_QUALITY,
                    "maxWidth": size["width"] * SCALE,
                    "maxHeight": size["height"] * SCALE,
                },
            )

    def _frame(self, cdp: CDPSession, params: dict[str, Any]) -> None:
        asyncio.get_running_loop().create_task(self._ack(cdp, params["sessionId"]))
        meta = params.get("metadata", {})
        frame = {
            "t": "frame",
            "d": params["data"],
            "w": meta.get("deviceWidth"),
            "h": meta.get("deviceHeight"),
        }
        self._last_frame = frame
        if self._sink is not None:
            self._sink(frame)

    @staticmethod
    async def _ack(cdp: CDPSession, session_id: int) -> None:
        with contextlib.suppress(Exception):
            await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})

    # Input, in CSS pixels of the tab.

    async def mouse(self, kind: str, x: float, y: float) -> None:
        params: dict[str, Any] = {"x": x, "y": y}
        if kind == "down":
            self._pressed = True
            params |= {"type": "mousePressed", "button": "left", "buttons": 1, "clickCount": 1}
        elif kind == "up":
            self._pressed = False
            params |= {"type": "mouseReleased", "button": "left", "buttons": 0, "clickCount": 1}
        elif kind == "move":
            pressed = self._pressed
            params |= {
                "type": "mouseMoved",
                "button": "left" if pressed else "none",
                "buttons": 1 if pressed else 0,
            }
        else:
            return
        await self._send("Input.dispatchMouseEvent", params)

    async def wheel(self, x: float, y: float, dy: float) -> None:
        await self._send(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy},
        )

    async def text(self, text: str) -> None:
        await self._send("Input.insertText", {"text": text})

    async def key(self, name: str) -> None:
        if name not in KEYS:
            return
        key, code, vk, text = KEYS[name]
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk}
        down = {"type": "keyDown", **base, **({"text": text} if text else {})}
        await self._send("Input.dispatchKeyEvent", down)
        await self._send("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    async def back(self) -> None:
        if self._page is not None:
            with contextlib.suppress(Exception):
                await self._page.go_back(wait_until="commit", timeout=15_000)

    async def _send(self, method: str, params: dict[str, Any]) -> None:
        if self._cdp is not None and not self.closed:
            await self._cdp.send(method, params)

    async def cookies(self) -> list[dict[str, Any]]:
        if self._context is None:
            return []
        return [dict(c) for c in await self._context.cookies()]

    async def close(self) -> None:
        self.closed = True
        self._sink = None
        for close in (
            self._context.close if self._context else None,
            self._browser.close if self._browser else None,
            self._pw.stop if self._pw else None,
        ):
            if close is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(close(), 15)
        self._context = self._browser = self._pw = None
        self._page = self._cdp = None
