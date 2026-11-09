"""The Mini App (1.1, part 5): static files under `/app/`, a read-only JSON API under `/api/`.

Every `/api/*` request carries Telegram `initData` in the `X-Telegram-Init-Data` header;
only the owner's, signed with this bot's token and fresh, gets an answer. The page has no
cookies and no session: Telegram hands it the data each time it opens.
"""

from __future__ import annotations

import collections
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from aiohttp import web
from pydantic import BaseModel

from hostwatch.core.webapp_auth import MAX_LENGTH, InvalidInitData, validate

log = logging.getLogger(__name__)

INIT_DATA_HEADER = "X-Telegram-Init-Data"
STATIC_DIR = Path(__file__).parent / "app"
CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
}
# Bad requests from one address within a minute before it gets 429 without any hashing.
FAILURES_PER_MINUTE = 20
# Telegram's web client shows the Mini App in a frame; the phone apps in a web view.
APP_HEADERS = {
    "Cache-Control": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; "
        "frame-ancestors https://web.telegram.org https://*.telegram.org"
    ),
}
API_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


# Responses


class Amount(BaseModel):
    # A decimal as text: JSON numbers would lose kopecks.
    amount: str
    currency: str


class Sum(BaseModel):
    """A sum of several currencies: `total` in the owner's currency when the rates allow,
    and always `parts`, one per currency."""

    total: Amount | None
    parts: list[Amount]


class ProblemOut(BaseModel):
    level: Literal["warn", "bad"]
    account_id: int
    hoster: str
    server: str | None
    # What is wrong, as the chat says it: «RAM 93%», «деньги до завтра 09:00».
    what: str


class HosterOut(BaseModel):
    account_id: int
    title: str
    plugin: str
    status: str
    level: Literal["ok", "warn", "bad"]
    balance: Amount | None
    balance_at: datetime | None
    # «до 12.11», «не тратится»; empty when there is no forecast.
    lasts: str
    # How soon the money runs out against the owner's threshold, as in «💰 Money».
    money_level: Literal["ok", "warn", "bad"]
    runs_out_at: datetime | None
    monthly: Sum
    servers: int
    spend_month: Sum


class SpendOut(BaseModel):
    # "2026-11" in the owner's time zone.
    month: str
    previous_month: str
    this_month: Sum
    last_month: Sum


class TotalsOut(BaseModel):
    balances: Sum
    monthly: Sum


class Overview(BaseModel):
    lang: str
    currency: str
    generated_at: datetime
    rates_date: str | None
    hosters_count: int
    servers_count: int
    totals: TotalsOut
    spend: SpendOut
    problems: list[ProblemOut]
    hosters: list[HosterOut]


# The app


@dataclass
class MiniApp:
    bot_token: str
    owner_id: int
    overview: Callable[[], Awaitable[Overview]]
    now: Callable[[], float] = time.monotonic
    _failures: dict[str, collections.deque[float]] = field(default_factory=dict)

    def blocked(self, ip: str) -> bool:
        seen = self._failures.get(ip)
        if seen is None:
            return False
        cutoff = self.now() - 60
        while seen and seen[0] < cutoff:
            seen.popleft()
        return len(seen) >= FAILURES_PER_MINUTE

    def failed(self, ip: str) -> None:
        if len(self._failures) > 10_000:
            self._failures.clear()
        self._failures.setdefault(ip, collections.deque(maxlen=FAILURES_PER_MINUTE)).append(
            self.now()
        )


MINIAPP: web.AppKey[MiniApp] = web.AppKey("miniapp", MiniApp)


def add_routes(app: web.Application, miniapp: MiniApp) -> None:
    app[MINIAPP] = miniapp
    app.router.add_get("/app", to_app, allow_head=False)
    app.router.add_get("/app/", index)
    app.router.add_get("/app/{path:.+}", static)
    app.router.add_get("/api/overview", api_overview, allow_head=False)
    app.router.add_route("*", "/api/{tail:.*}", api_missing)


async def to_app(request: web.Request) -> web.StreamResponse:
    raise web.HTTPMovedPermanently("/app/")


async def index(request: web.Request) -> web.StreamResponse:
    return _file(STATIC_DIR / "index.html")


async def static(request: web.Request) -> web.StreamResponse:
    root = STATIC_DIR.resolve()
    path = (root / request.match_info["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise web.HTTPNotFound(headers=API_HEADERS)
    return _file(path)


def _file(path: Path) -> web.Response:
    kind = CONTENT_TYPES.get(path.suffix)
    if path.name == "LICENSE":
        kind = "text/plain"
    if kind is None:
        raise web.HTTPNotFound(headers=API_HEADERS)
    return web.Response(
        body=path.read_bytes(), content_type=kind, charset="utf-8", headers=APP_HEADERS
    )


def _client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[-1].strip() if forwarded else ""
    return ip or request.remote or "?"


def _unauthorized() -> web.Response:
    return web.json_response({"error": "unauthorized"}, status=401, headers=API_HEADERS)


def check(request: web.Request) -> web.Response | None:
    """None for the owner; otherwise the answer to give (401, or 429 after many tries)."""
    miniapp = request.app[MINIAPP]
    ip = _client_ip(request)
    if miniapp.blocked(ip):
        return web.json_response({"error": "too many requests"}, status=429, headers=API_HEADERS)
    data = request.headers.get(INIT_DATA_HEADER, "")
    if not data or len(data) > MAX_LENGTH:
        miniapp.failed(ip)
        return _unauthorized()
    try:
        validate(data, miniapp.bot_token, miniapp.owner_id)
    except InvalidInitData as exc:
        log.info("mini app request refused: %s", exc)
        miniapp.failed(ip)
        return _unauthorized()
    return None


async def api_overview(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    overview = await request.app[MINIAPP].overview()
    return web.Response(
        text=overview.model_dump_json(), content_type="application/json", headers=API_HEADERS
    )


async def api_missing(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    return web.json_response({"error": "not found"}, status=404, headers=API_HEADERS)
