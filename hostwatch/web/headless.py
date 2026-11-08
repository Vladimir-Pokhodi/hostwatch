"""A headless tab for plugins whose payment pages need a browser (Cryptomus at Regxa).

The server affords one Chromium at a time (spec, constraints). A tab is opened only when
no login is running, and two tabs never run at once. The tab may reach only the domains
the plugin names; everything else (trackers, chat widgets) is aborted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable

from playwright.async_api import Page, Request, Route, async_playwright

from hostwatch.core.errors import BrowserBusy
from hostwatch.core.http import USER_AGENT

from .browser import Launcher, Policy

log = logging.getLogger(__name__)

VIEWPORT = {"width": 420, "height": 900}
# How long a top-up waits for another tab to close before it gives up.
WAIT = 60.0


class HeadlessPages:
    """`Pages` of the core: `busy()` says whether a login holds the browser right now."""

    def __init__(self, launcher: Launcher, busy: Callable[[], bool] = lambda: False) -> None:
        self._launcher = launcher
        self._busy = busy
        self._lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def open(self, allowed_domains: tuple[str, ...]) -> AsyncIterator[Page]:
        if self._busy():
            raise BrowserBusy
        try:
            await asyncio.wait_for(self._lock.acquire(), WAIT)
        except TimeoutError:
            raise BrowserBusy from None
        try:
            if self._busy():
                raise BrowserBusy
            policy = Policy(allowed_domains)
            async with async_playwright() as pw:
                browser = await self._launcher(pw)
                try:
                    context = await browser.new_context(
                        viewport=VIEWPORT,  # type: ignore[arg-type]
                        user_agent=USER_AGENT,
                        locale="en-US",
                        service_workers="block",
                        accept_downloads=False,
                    )

                    async def route(route: Route, request: Request) -> None:
                        if policy.allows(request.url):
                            await route.continue_()
                        else:
                            await route.abort()

                    await context.route("**/*", route)
                    yield await context.new_page()
                finally:
                    with contextlib.suppress(Exception):
                        await browser.close()
        finally:
            self._lock.release()
