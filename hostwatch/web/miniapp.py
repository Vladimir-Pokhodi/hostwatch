"""The Mini App (1.1, part 5): static files under `/app/`, a read-only JSON API under `/api/`.

Every `/api/*` request carries Telegram `initData` in the `X-Telegram-Init-Data` header;
only the owner's, signed with this bot's token and fresh, gets an answer. The page has no
cookies and no session: Telegram hands it the data each time it opens.
"""

from __future__ import annotations

import collections
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Protocol

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hostwatch.core.history import MoneyKind, MoneySource
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
    # The owner's time zone (IANA name): the app shows every date in it.
    tz: str = "UTC"
    generated_at: datetime
    rates_date: str | None
    hosters_count: int
    servers_count: int
    totals: TotalsOut
    spend: SpendOut
    problems: list[ProblemOut]
    hosters: list[HosterOut]


# Servers


Level = Literal["ok", "warn", "bad", "off", "unknown"]


class HosterRef(BaseModel):
    account_id: int
    title: str
    plugin: str


class ServerOut(BaseModel):
    account_id: int
    hoster: str
    server_id: str
    name: str
    kind: str
    # running, stopped, suspended, unpaid, unknown
    status: str
    # «off»: stopped by the owner through the bot, which is not a problem.
    level: Level
    # What is wrong besides the load (that one is `high_load`): «остановлен».
    problems: list[str]
    ip: str | None
    plan: str | None
    billing: Literal["hourly", "periodic"]
    price: Amount | None
    period_months: int | None
    # A month of it, in its own currency and in the owner's (None without rates).
    monthly: Amount | None
    monthly_converted: Amount | None
    next_charge_at: datetime | None
    days_to_charge: int | None
    autorenew: Literal["on", "off", "n/a"]
    autopay: bool
    usage_supported: bool
    cpu: float | None
    ram: float | None
    disk: float | None
    net_in: float | None
    net_out: float | None
    usage_at: datetime | None
    high_load: bool


class Limits(BaseModel):
    """The owner's thresholds: load and disk in percent, days of money or to the end."""

    load: int
    disk: int
    days: int


class ServersOut(BaseModel):
    lang: str
    currency: str
    tz: str
    generated_at: datetime
    limits: Limits
    hosters: list[HosterRef]
    servers: list[ServerOut]


class ServerEventOut(BaseModel):
    at: datetime
    # appeared, removed, plan, price, period, ip, status, autorenew
    kind: str
    old: str | None
    new: str | None


class CostMonth(BaseModel):
    month: str
    amount: Sum


class CostOut(BaseModel):
    """What the server cost: the hoster's own charges for it, or an estimate from its
    price over the time the bot has known it."""

    basis: Literal["hoster", "estimate", "none"]
    since: datetime | None
    total: Sum
    months: list[CostMonth]


class ServerPage(BaseModel):
    lang: str
    currency: str
    tz: str
    limits: Limits
    server: ServerOut
    events: list[ServerEventOut]
    cost: CostOut
    collecting_since: datetime | None


Span = Literal["24h", "7d", "30d", "1y"]
METRIC_NAMES = ("cpu", "ram", "disk", "net_in", "net_out")


class SeriesOut(BaseModel):
    avg: list[float | None]
    # Peaks of each hour or day; None for raw samples (a sample is its own peak).
    max: list[float | None] | None


class UsageOut(BaseModel):
    span: Span
    resolution: Literal["samples", "hourly", "daily"]
    start: datetime
    end: datetime
    tz: str
    # Unix seconds; a None value in a series is a gap.
    t: list[int]
    series: dict[str, SeriesOut]
    collecting_since: datetime | None


# Money


class HosterMonth(BaseModel):
    account_id: int
    charge: Sum


class MoneyMonth(BaseModel):
    month: str
    # The month is under way, or the history (or a hoster) began inside it.
    partial: bool
    # The history began after this month.
    no_data: bool
    total: Sum
    hosters: list[HosterMonth]


class ServerSpend(BaseModel):
    server_id: str
    name: str
    charge: Sum
    # Split by tariff prices: the hoster does not say which server a charge was for.
    estimate: bool


class HosterMoney(BaseModel):
    account_id: int
    title: str
    plugin: str
    since: datetime | None
    charge: Sum
    topup: Sum
    servers: list[ServerSpend]
    # Charges tied to no server.
    unassigned: Sum


class MoneyOut(BaseModel):
    lang: str
    currency: str
    tz: str
    rates_date: str | None
    collecting_since: datetime | None
    months: list[MoneyMonth]
    hosters: list[HosterMoney]
    charge: Sum
    topup: Sum


class MoneyEventOut(BaseModel):
    id: int
    at: datetime
    account_id: int
    hoster: str
    kind: Literal["topup", "charge"]
    source: Literal["observed", "hoster"]
    amount: Amount
    converted: Amount | None
    server_id: str | None
    server: str | None
    description: str


class MoneyEventsOut(BaseModel):
    events: list[MoneyEventOut]
    has_more: bool


# Journal


Actor = Literal["owner", "autopay", "system"]
JournalGroup = Literal["servers", "money", "accounts", "settings", "other"]


class JournalEntryOut(BaseModel):
    id: int
    at: datetime
    actor: Actor
    account_id: int | None
    hoster: str | None
    action: str
    group: JournalGroup
    # The record as the chat journal says it, in the bot's language.
    text: str
    ok: bool


class JournalOut(BaseModel):
    entries: list[JournalEntryOut]
    has_more: bool


# Tips


class TipOut(BaseModel):
    # oversized, cramped, cheaper, renewal, shortfall, idle, trend, no_data
    kind: str
    account_id: int | None
    server_id: str | None
    hoster: str | None
    server: str | None
    # What to do, one short line; why, with the numbers behind it.
    title: str
    why: str
    # The plan the tip names, with its price.
    offer: str | None
    note: str | None
    # A breakdown: what grew, which servers have little data.
    lines: list[str]
    # In the owner's currency: a month of saving or extra cost, or the money at stake.
    amount: Amount | None
    impact: Literal["saving", "cost", "risk"] | None
    per_month: bool


class TipsOut(BaseModel):
    lang: str
    currency: str
    generated_at: datetime
    tips: list[TipOut]
    # Hosters whose order catalog could not be read now: their plans are not compared.
    no_catalog: list[str]
    collecting_since: datetime | None


# Query parameters


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UsageQuery(Query):
    span: Span = Field("24h", alias="range")


class MoneyQuery(Query):
    months: int = Field(12, ge=1, le=36)


class MoneyEventsQuery(Query):
    account: int | None = Field(None, ge=1, le=2**31)
    kind: MoneyKind | None = None
    source: MoneySource = MoneySource.OBSERVED
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=100_000)


class JournalQuery(Query):
    account: int | None = Field(None, ge=1, le=2**31)
    actor: Actor | None = None
    group: JournalGroup | None = None
    # Days in the owner's time zone, both included.
    since: date | None = None
    until: date | None = None
    limit: int = Field(30, ge=1, le=100)
    offset: int = Field(0, ge=0, le=100_000)


class Source(Protocol):
    """Where the API takes its data; the bot gives it (`hostwatch.bot.webdata`)."""

    async def overview(self) -> Overview: ...

    async def servers(self) -> ServersOut: ...

    async def server(self, account_id: int, server_id: str) -> ServerPage | None: ...

    async def usage(self, account_id: int, server_id: str, span: Span) -> UsageOut | None: ...

    async def money(self, query: MoneyQuery) -> MoneyOut: ...

    async def money_events(self, query: MoneyEventsQuery) -> MoneyEventsOut: ...

    async def journal(self, query: JournalQuery) -> JournalOut: ...

    async def tips(self) -> TipsOut: ...


# The app


@dataclass
class MiniApp:
    bot_token: str
    owner_id: int
    source: Source
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
    app.router.add_get("/api/servers", api_servers, allow_head=False)
    app.router.add_get("/api/servers/{account}/{server}", api_server, allow_head=False)
    app.router.add_get("/api/servers/{account}/{server}/usage", api_usage, allow_head=False)
    app.router.add_get("/api/money", api_money, allow_head=False)
    app.router.add_get("/api/money/events", api_money_events, allow_head=False)
    app.router.add_get("/api/journal", api_journal, allow_head=False)
    app.router.add_get("/api/tips", api_tips, allow_head=False)
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


def _json(model: BaseModel) -> web.Response:
    return web.Response(
        text=model.model_dump_json(), content_type="application/json", headers=API_HEADERS
    )


def _bad_request(what: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(
        text=json.dumps({"error": "bad request", "what": what}),
        content_type="application/json",
        headers=API_HEADERS,
    )


def _not_found() -> web.HTTPNotFound:
    return web.HTTPNotFound(
        text='{"error": "not found"}', content_type="application/json", headers=API_HEADERS
    )


def _query[Q: Query](request: web.Request, model: type[Q]) -> Q:
    """The query string as `model`; 400 on an unknown, repeated or invalid parameter."""
    if len(request.query) != len(set(request.query)):
        raise _bad_request("repeated parameter")
    try:
        return model.model_validate(dict(request.query))
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        raise _bad_request(",".join(fields) or "query") from None


MAX_SERVER_ID = 200


def _server_key(request: web.Request) -> tuple[int, str]:
    account, server = request.match_info["account"], request.match_info["server"]
    if not account.isdigit() or len(account) > 9 or not 0 < len(server) <= MAX_SERVER_ID:
        raise _not_found()
    return int(account), server


def _source(request: web.Request) -> Source:
    return request.app[MINIAPP].source


async def api_overview(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    _query(request, Query)
    return _json(await _source(request).overview())


async def api_servers(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    _query(request, Query)
    return _json(await _source(request).servers())


async def api_server(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    account, server = _server_key(request)
    _query(request, Query)
    page = await _source(request).server(account, server)
    if page is None:
        raise _not_found()
    return _json(page)


async def api_usage(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    account, server = _server_key(request)
    query = _query(request, UsageQuery)
    usage = await _source(request).usage(account, server, query.span)
    if usage is None:
        raise _not_found()
    return _json(usage)


async def api_money(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    return _json(await _source(request).money(_query(request, MoneyQuery)))


async def api_money_events(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    return _json(await _source(request).money_events(_query(request, MoneyEventsQuery)))


async def api_journal(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    query = _query(request, JournalQuery)
    if query.since is not None and query.until is not None and query.since > query.until:
        raise _bad_request("since")
    return _json(await _source(request).journal(query))


async def api_tips(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    _query(request, Query)
    return _json(await _source(request).tips())


async def api_missing(request: web.Request) -> web.StreamResponse:
    refused = check(request)
    if refused is not None:
        return refused
    return web.json_response({"error": "not found"}, status=404, headers=API_HEADERS)
