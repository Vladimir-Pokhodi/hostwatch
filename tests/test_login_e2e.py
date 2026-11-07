"""End to end: a fake hoster with a login form, logged into through our streaming page.

One Chromium plays the hoster tab (as the `browser` container does), another plays
the owner's phone that opens the link, taps the picture and types into our text field.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from aiohttp import web
from playwright.async_api import Page, async_playwright

from hostwatch.bot.login import pick_cookies
from hostwatch.core.errors import AuthExpired
from hostwatch.core.models import Balance, Server
from hostwatch.core.plugin import BrowserAuth, Plugin, PluginContext, PluginMeta, cookie_header
from hostwatch.web.browser import local_launcher
from hostwatch.web.login import LoginEvents, LoginService, LoginTarget, NotDone
from hostwatch.web.server import make_app

SESSION = "sess-0123456789"

LOGIN_FORM = """<!doctype html><html><head><style>
body {{ margin: 0; font: 16px sans-serif; }}
input, button, a {{ position: absolute; left: 20px; width: 220px; height: 36px; }}
</style></head><body>
<input id="user" name="user" form="f" style="top: 20px">
<input id="password" name="password" type="password" form="f" style="top: 80px">
<form id="f" method="post" action="/login"></form>
<button form="f" style="top: 140px">Log in</button>
<a id="away" href="{away}" style="top: 200px">elsewhere</a>
<p style="position:absolute; top: 260px; left: 20px">{error}</p>
</body></html>"""


def fake_hoster(away: str) -> web.Application:
    async def form(request: web.Request) -> web.Response:
        return web.Response(text=LOGIN_FORM.format(away=away, error=""), content_type="text/html")

    async def login(request: web.Request) -> web.Response:
        data = await request.post()
        if data.get("user") == "owner" and data.get("password") == "pw":
            response = web.HTTPFound("/account")
            response.set_cookie("session", SESSION, httponly=True)
            response.set_cookie("tracking", "not-wanted")
            raise response
        text = LOGIN_FORM.format(away=away, error="wrong password")
        return web.Response(text=text, content_type="text/html")

    async def account(request: web.Request) -> web.Response:
        return web.Response(text="<h1>Hello, owner</h1>", content_type="text/html")

    async def session(request: web.Request) -> web.Response:
        if request.cookies.get("session") == SESSION:
            return web.json_response({"user": "owner"})
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/login", form)
    app.router.add_post("/login", login)
    app.router.add_get("/account", account)
    app.router.add_get("/api/session", session)
    return app


async def start(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, port


class SitePlugin(Plugin):
    """What a real plugin does after login: check the session with its own HTTP client."""

    meta = PluginMeta(
        id="site",
        name="Fake hoster",
        site="http://127.0.0.1",
        auth=(BrowserAuth("", ("127.0.0.1",), ("session",)),),
        currency="EUR",
    )
    base = ""

    async def check_login(self) -> None:
        auth = self.meta.auth[0]
        assert isinstance(auth, BrowserAuth)
        response = await self.ctx.http.get(
            f"{self.base}/api/session", headers={"Cookie": cookie_header(self.ctx.secrets, auth)}
        )
        if not response.json().get("user"):
            raise AuthExpired

    async def servers(self) -> list[Server]:
        return []

    async def balance(self) -> Balance:
        raise NotImplementedError


class Store(LoginEvents):
    def __init__(self) -> None:
        self.saved: dict[str, str] | None = None

    async def finish(self, target: LoginTarget, cookies: list[dict[str, Any]]) -> str:
        picked = pick_cookies(cookies, target.auth)
        if target.auth.missing(picked):
            raise NotDone("missing")

        async def keep(_: object) -> None:
            return None

        async with httpx.AsyncClient() as http:
            await SitePlugin(PluginContext(picked, http, keep)).check_login()
        self.saved = picked
        return ""


async def wait_for(check: Callable[[], Awaitable[bool]], timeout: float = 15.0) -> None:
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.1)


async def tap(phone: Page, x: float, y: float) -> None:
    """Tap the picture where the hoster tab has CSS pixel (x, y)."""
    box = await phone.locator("#view").bounding_box()
    assert box is not None
    size = await phone.evaluate("[remote.w, remote.h]")
    k = min(box["width"] / size[0], box["height"] / size[1])
    left = box["x"] + (box["width"] - size[0] * k) / 2
    top = box["y"] + (box["height"] - size[1] * k) / 2
    await phone.touchscreen.tap(left + x * k, top + y * k)


@pytest.fixture
async def sites() -> AsyncIterator[tuple[str, str]]:
    away_runner, away_port = await start(web.Application())
    hoster_runner, hoster_port = await start(fake_hoster(f"http://localhost:{away_port}/"))
    yield f"http://127.0.0.1:{hoster_port}", f"localhost:{away_port}"
    await hoster_runner.cleanup()
    await away_runner.cleanup()


async def test_login_through_the_streaming_page(sites: tuple[str, str]) -> None:
    hoster, away = sites
    SitePlugin.base = hoster
    store = Store()
    service = LoginService(local_launcher, store)
    page_runner, page_port = await start(make_app(service, secure=False))
    auth = BrowserAuth(f"{hoster}/login", ("127.0.0.1",), ("session",))
    token = await service.issue(LoginTarget("site", "Fake hoster", auth, lang="en"))
    url = f"http://127.0.0.1:{page_port}/login/{token}"

    async with async_playwright() as pw:
        if not os.path.exists(pw.chromium.executable_path) and not os.environ.get("CI"):
            await page_runner.cleanup()
            pytest.skip("no Chromium for Playwright: uv run playwright install chromium")
        phone_browser = await pw.chromium.launch()
        phone = await phone_browser.new_page(
            viewport={"width": 400, "height": 760},
            has_touch=True,
            is_mobile=True,
            # The page forbids eval; the test reads its state with it.
            bypass_csp=True,
        )
        try:
            await phone.goto(url)
            assert phone.url.endswith("/login/s")
            await phone.wait_for_function("document.getElementById('view').naturalWidth > 0")
            # «Opening the site…» covers the picture until the hoster's page has loaded.
            await phone.wait_for_selector("#overlay", state="hidden")
            session = service.session
            assert session is not None
            tab = session.browser

            # A link to another domain goes nowhere.
            await tap(phone, 60, 218)
            await phone.wait_for_function(
                "document.getElementById('status').textContent.includes('blocked')"
            )
            assert away.split(":")[0] in tab.blocked_hosts
            assert tab.url == f"{hoster}/login"

            # «Done» before logging in is refused, the session goes on.
            await phone.click("#done")
            await phone.wait_for_function(
                "document.getElementById('status').textContent.includes('Not logged in')"
            )
            assert store.saved is None

            await tap(phone, 100, 38)
            await phone.fill("#text", "owner")
            await phone.click("#send")
            await tap(phone, 100, 98)
            await phone.fill("#text", "pw")
            await phone.press("#text", "Enter")  # types, then presses Enter on the site

            async def logged_in() -> bool:
                return any(c["name"] == "session" for c in await tab.cookies())

            await wait_for(logged_in)
            await phone.click("#done")
            await phone.wait_for_function(
                "document.getElementById('overlay').textContent.includes('is connected')"
            )
            assert store.saved == {"session": SESSION}
            assert service.session is None
            assert tab.closed

            again = await phone.goto(url)
            assert again is not None and again.status == 404
        finally:
            await phone_browser.close()
            await service.close()
            await page_runner.cleanup()
