"""The login page: `/login/<token>` opens a session, `/login/s` shows it, `/login/ws` streams it.

Caddy passes only `/login/*`, `/app/*` and `/api/*` to this server (spec, decision 6;
1.1, part 5). The Mini App under `/app/` and `/api/` lives in `miniapp.py`.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from hostwatch.locales import LANGUAGES, t

from .browser import clamp_size
from .login import EndReason, LoginService, LoginTarget, NotDone, Session
from .miniapp import MiniApp, add_routes

log = logging.getLogger(__name__)

COOKIE = "hw_login"
PORT = 8080
# `hostwatch login <account>` asks the running bot for a link here; loopback of the container.
ADMIN_PORT = 8081
MAX_TEXT = 500
HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src blob: data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}
SERVICE: web.AppKey[LoginService] = web.AppKey("login", LoginService)
SECURE: web.AppKey[bool] = web.AppKey("secure", bool)


def make_app(
    service: LoginService, secure: bool = True, miniapp: MiniApp | None = None
) -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app[SERVICE] = service
    app[SECURE] = secure
    app.router.add_get("/login/s", page, allow_head=False)
    app.router.add_get("/login/ws", stream, allow_head=False)
    # HEAD must not burn the token: link checkers and previews send it.
    app.router.add_get("/login/{token}", open_link, allow_head=False)
    if miniapp is not None:
        add_routes(app, miniapp)
    return app


def page_strings(lang: str) -> dict[str, str]:
    messages = LANGUAGES.get(lang, LANGUAGES["ru"])
    return {key[4:]: value for key, value in messages.items() if key.startswith("web.")}


def not_found(lang: str = "ru") -> web.Response:
    return web.Response(status=404, text=t("web.gone", lang), headers=HEADERS)


def client_ip(request: web.Request) -> str:
    # Caddy sets X-Forwarded-For to the address it saw; port 8080 is reachable only by it.
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[-1].strip() if forwarded else ""
    return ip or request.remote or "?"


async def open_link(request: web.Request) -> web.StreamResponse:
    service = request.app[SERVICE]
    token = request.match_info["token"]
    sid = await service.open(token, client_ip(request), request.headers.get("User-Agent", ""))
    if sid is None:
        return not_found()
    response = web.HTTPSeeOther("/login/s", headers=HEADERS)
    response.set_cookie(
        COOKIE,
        sid,
        path="/login",
        httponly=True,
        secure=request.app[SECURE],
        samesite="Lax",
        max_age=int(service.idle_ttl) + 3600,
    )
    raise response


async def page(request: web.Request) -> web.StreamResponse:
    session = request.app[SERVICE].find(request.cookies.get(COOKIE))
    if session is None:
        return not_found()
    lang = session.target.lang
    strings = page_strings(lang)
    html = (
        files("hostwatch.web")
        .joinpath("page.html")
        .read_text(encoding="utf-8")
        .replace("__LANG__", lang)
        .replace("__TITLE__", _html(t("web.title", lang, hoster=session.target.hoster)))
        .replace(
            "__STRINGS__",
            json.dumps({**strings, "hoster": session.target.hoster}).replace("<", "\\u003c"),
        )
    )
    return web.Response(text=html, content_type="text/html", headers=HEADERS)


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc == request.host


class Outbox:
    """Messages to the page. Only the newest frame is kept: a slow phone skips frames."""

    def __init__(self) -> None:
        self.messages: collections.deque[dict[str, Any]] = collections.deque(maxlen=100)
        self.frame: dict[str, Any] | None = None
        self.ready = asyncio.Event()

    def push(self, message: dict[str, Any]) -> None:
        if message.get("t") == "frame":
            self.frame = message
        else:
            self.messages.append(message)
        self.ready.set()

    async def pump(self, ws: web.WebSocketResponse) -> None:
        while not ws.closed:
            await self.ready.wait()
            self.ready.clear()
            while self.messages:
                await ws.send_str(json.dumps(self.messages.popleft()))
            if self.frame is not None:
                frame, self.frame = self.frame, None
                await ws.send_str(json.dumps(frame))


async def stream(request: web.Request) -> web.StreamResponse:
    service = request.app[SERVICE]
    session = service.find(request.cookies.get(COOKIE))
    if session is None or not _same_origin(request):
        return web.Response(status=403, headers=HEADERS)
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024)
    await ws.prepare(request)
    outbox = Outbox()
    # A newer connection of the same session (the phone came back) takes over the stream.
    session.sink = outbox.push
    pump = asyncio.get_running_loop().create_task(outbox.pump(ws))
    try:
        async for msg in ws:
            if msg.type is not WSMsgType.TEXT:
                break
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            if isinstance(data, dict):
                await _handle(service, session, data, outbox)
            if session.ended is not None:
                await asyncio.sleep(0.2)
                break
    finally:
        if session.sink == outbox.push:
            session.sink = None
            session.browser.attach(None)
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError):
            await pump
        await ws.close()
    return ws


async def _handle(
    service: LoginService, session: Session, data: dict[str, Any], outbox: Outbox
) -> None:
    kind = data.get("t")
    browser = session.browser
    if kind == "size":
        size = clamp_size(data.get("w"), data.get("h"))
        if not browser.started:
            try:
                await browser.start(size)
            except Exception as exc:
                log.warning("login browser did not start: %s", type(exc).__name__)
                outbox.push({"t": "fail", "v": "browser"})
                await service.end(EndReason.FAILED, session)
                return
        browser.attach(outbox.push)
        return
    if not browser.started or session.ended is not None:
        return
    service.touch(session)
    try:
        await _input(service, session, kind, data)
    except NotDone as exc:
        outbox.push({"t": "notdone", "v": exc.code, "d": exc.detail})
    except Exception as exc:
        # The tab may be navigating; one lost tap is not worth ending the session.
        log.debug("login input %s failed: %s", kind, type(exc).__name__)


async def _input(service: LoginService, session: Session, kind: Any, data: dict[str, Any]) -> None:
    browser = session.browser
    x, y = _number(data.get("x")), _number(data.get("y"))
    if kind == "mouse":
        await browser.mouse(str(data.get("e")), x, y)
    elif kind == "wheel":
        await browser.wheel(x, y, max(-3000.0, min(3000.0, _number(data.get("dy")))))
    elif kind == "text":
        text = str(data.get("s", ""))[:MAX_TEXT]
        if text:
            await browser.text(text)
    elif kind == "key":
        await browser.key(str(data.get("k")))
    elif kind == "back":
        await browser.back()
    elif kind == "done":
        await service.finish(session)
    elif kind == "cancel":
        await service.end(EndReason.CANCELLED, session)


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


async def serve(
    service: LoginService,
    host: str = "0.0.0.0",
    port: int = PORT,
    miniapp: MiniApp | None = None,
) -> web.AppRunner:
    runner = web.AppRunner(make_app(service, miniapp=miniapp), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("login page listens on %s:%s", host, port)
    return runner


async def serve_admin(
    resolve: Callable[[str], LoginTarget | None],
    send_link: Callable[[LoginTarget], Awaitable[str | None]],
    host: str = "127.0.0.1",
    port: int = ADMIN_PORT,
) -> web.AppRunner:
    async def link(request: web.Request) -> web.Response:
        data = await request.json()
        target = resolve(str(data.get("target", "")))
        if target is None:
            error = (
                "no account with browser login by that id or label; a plugin id works when it"
                " has exactly one account, or none and needs no API key"
            )
            return web.json_response({"error": error}, status=404)
        url = await send_link(target)
        if url is None:
            return web.json_response({"error": "set HOSTWATCH_IP or HOSTWATCH_DOMAIN"}, status=409)
        return web.json_response({"url": url, "hoster": target.hoster})

    app = web.Application()
    app.router.add_post("/link", link)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
