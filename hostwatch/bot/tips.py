"""The «Советы» tab of the Mini App (1.1, part 5): gathers what `hostwatch.core.tips`
looks at and words what it finds.

Specs of a server come from its hoster's order catalog: the plan with the server's tariff
name, or the plan the hoster names for «Order the same». Catalogs are cached for an hour
(`CatalogCache`, shared with ordering), those answers for `TEMPLATE_TTL`.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from hostwatch.core.catalog import Catalog, OrderTemplate, Plan
from hostwatch.core.errors import describe
from hostwatch.core.history import MoneySource, ServerEventKind
from hostwatch.core.models import AccountStatus, Money, Server, ServerStatus
from hostwatch.core.plugin import Capability, Plugin, SupportsOrder
from hostwatch.core.rates import Rates
from hostwatch.core.tips import (
    LONG_WINDOW,
    MIN_DAYS,
    SHORT_WINDOW,
    AccountFacts,
    Impact,
    Inputs,
    LoadHour,
    LoadStats,
    Offers,
    ServerFacts,
    SpendFacts,
    SpendPart,
    Tip,
    TipKind,
    load_stats,
    parse_specs,
    tips,
)
from hostwatch.storage.history import HistoryRepository
from hostwatch.web.miniapp import Amount, TipOut, TipsOut

from . import views
from .hub import AccountData, Hub
from .views import View

log = logging.getLogger(__name__)

TEMPLATE_TTL = 12 * 3600.0
# A failed answer is not asked again for this long.
FAILED_TTL = 15 * 60.0
# How long the tab waits for one hoster's catalog.
CATALOG_TIMEOUT = 25.0
NAMES_SHOWN = 6


# Where each server sits in its hoster's catalog


@dataclass(frozen=True)
class Placed:
    plan: Plan | None
    location_id: str | None


@dataclass(frozen=True)
class HosterPlans:
    catalog: Catalog
    servers: Mapping[str, Placed]


@dataclass(frozen=True)
class Catalogs:
    found: Mapping[int, HosterPlans] = field(default_factory=dict)
    # Accounts that sell servers but whose catalog was not read now.
    failed: tuple[int, ...] = ()


async def no_catalogs(accounts: list[AccountData]) -> Catalogs:
    return Catalogs()


def match_plan(catalog: Catalog, server: Server) -> Plan | None:
    """The catalog plan named like the server's tariff, if only one is: «X1» or «X1 · Group»."""
    name = (server.plan or "").strip()
    if not name:
        return None
    exact = [p for p in catalog.plans if p.name.strip() == name]
    if not exact:
        exact = [p for p in catalog.plans if p.name.startswith(f"{name} · ")]
    return exact[0] if len(exact) == 1 else None


@dataclass
class PlanReader:
    """Reads catalogs and «Order the same» answers through the bot's plugins."""

    catalog: Callable[[int], Awaitable[Catalog]]
    template: Callable[[int, str], Awaitable[OrderTemplate]]
    clock: Callable[[], float] = time.monotonic
    _templates: dict[tuple[int, str], tuple[float, OrderTemplate | None]] = field(
        default_factory=dict
    )

    @classmethod
    def of(cls, hub: Hub) -> PlanReader:
        from .order import _call, _catalog

        async def template(acc: int, server_id: str) -> OrderTemplate:
            async def ask(plugin: Plugin) -> OrderTemplate:
                if not isinstance(plugin, SupportsOrder):
                    raise NotImplementedError
                return await plugin.order_template(server_id)

            return await _call(hub, acc, ask)

        return cls(catalog=lambda acc: _catalog(hub, acc), template=template)

    async def _template(self, acc: int, server_id: str) -> OrderTemplate | None:
        key = (acc, server_id)
        cached = self._templates.get(key)
        now = self.clock()
        if cached is not None:
            at, found = cached
            if now - at < (TEMPLATE_TTL if found is not None else FAILED_TTL):
                return found
        try:
            answer: OrderTemplate | None = await self.template(acc, server_id)
        except Exception as error:
            log.info("order template of %s/%s: %s", acc, server_id, describe(error))
            answer = None
        self._templates[key] = (now, answer)
        return answer

    async def _account(self, data: AccountData) -> HosterPlans:
        acc = data.account.id
        catalog = await self.catalog(acc)
        placed: dict[str, Placed] = {}
        for server in data.servers:
            plan = match_plan(catalog, server)
            location = None if plan is None else plan.location_id
            if plan is None:
                template = await self._template(acc, server.external_id)
                if template is not None:
                    plan = catalog.plan(template.plan_id) if template.plan_id else None
                    location = plan.location_id if plan is not None else template.location_id
            placed[server.external_id] = Placed(plan, location)
        return HosterPlans(catalog, placed)

    async def __call__(self, accounts: list[AccountData]) -> Catalogs:
        wanted = [
            d
            for d in accounts
            if Capability.ORDER in d.caps and d.account.status is AccountStatus.OK
        ]
        answers = await asyncio.gather(
            *(asyncio.wait_for(self._account(d), CATALOG_TIMEOUT) for d in wanted),
            return_exceptions=True,
        )
        found: dict[int, HosterPlans] = {}
        failed: list[int] = []
        for data, answer in zip(wanted, answers, strict=True):
            if isinstance(answer, BaseException):
                if not isinstance(answer, Exception):
                    raise answer
                log.info("catalog of account %s: %s", data.account.id, describe(answer))
                failed.append(data.account.id)
            else:
                found[data.account.id] = answer
        return Catalogs(found, tuple(failed))


# Facts from the history


def _hours(history: HistoryRepository, acc: int, sid: str, now: datetime) -> list[LoadHour]:
    series = history.usage_series(acc, sid, now - LONG_WINDOW, now)
    return [
        LoadHour(
            at=p.at,
            avg={m: p.avg.get(m) for m in ("cpu", "ram", "disk")},
            peak={m: p.max.get(m) for m in ("cpu", "ram", "disk")},
        )
        for p in series.points
    ]


def _stopped_since(history: HistoryRepository, acc: int, server: Server) -> datetime | None:
    """When the server was last seen to stop; without such a record, its oldest record
    (it was stopped all along)."""
    if server.status is not ServerStatus.STOPPED:
        return None
    sid = server.external_id
    events = history.server_events(acc, sid, kinds=[ServerEventKind.STATUS], limit=1)
    if events:
        return events[0].at if events[0].new == ServerStatus.STOPPED.value else None
    return history.first_seen(acc, sid)


def _convert(rates: Rates | None) -> Callable[[Money, str], Money | None]:
    def convert(money: Money, to: str) -> Money | None:
        if money.currency == to:
            return money
        return None if rates is None else rates.convert(money, to)

    return convert


def _month_start(day: date, v: View) -> datetime:
    return datetime(day.year, day.month, 1, tzinfo=v.tz)


def _spend(
    v: View, live: list[AccountData], history: HistoryRepository, rates: Rates | None, cur: str
) -> SpendFacts | None:
    local = v.now.astimezone(v.tz)
    this_start = _month_start(local.date(), v)
    last_day = this_start.date() - timedelta(days=1)
    last_start = _month_start(last_day, v)
    days_in = calendar.monthrange(local.year, local.month)[1]
    elapsed_days = (v.now - this_start).total_seconds() / 86400
    this_key, last_key = local.strftime("%Y-%m"), last_day.strftime("%Y-%m")
    started = history.started()
    if started is None or started > last_start:
        return None
    convert = _convert(rates)

    def owner(amount: Decimal, currency: str) -> Decimal | None:
        found = convert(Money(amount=amount, currency=currency), cur)
        return None if found is None else found.amount

    observed = history.money_by_month(MoneySource.OBSERVED, v.tz, since=last_start)
    hoster = history.money_by_month(MoneySource.HOSTER, v.tz, by_server=True, since=last_start)
    parts: list[SpendPart] = []
    servers: list[SpendPart] = []
    for data in live:
        acc = data.account.id
        first = history.first_balance(acc)
        if first is None or first > last_start:
            continue
        sums = {this_key: Decimal(0), last_key: Decimal(0)}
        ok = True
        for m in observed:
            if m.account_id != acc or m.month not in sums or not m.charge:
                continue
            value = owner(m.charge, m.currency)
            if value is None:
                ok = False
                break
            sums[m.month] += value
        if not ok:
            continue
        parts.append(SpendPart(data.title, sums[this_key], sums[last_key], acc))
        names = {s.external_id: s.name for s in data.servers}
        by_server: dict[str, dict[str, Decimal]] = {}
        for m in hoster:
            if m.account_id != acc or m.server_id is None or m.month not in sums or not m.charge:
                continue
            value = owner(m.charge, m.currency)
            if value is None:
                continue
            row = by_server.setdefault(m.server_id, {this_key: Decimal(0), last_key: Decimal(0)})
            row[m.month] += value
        for sid, row in by_server.items():
            name = f"{data.title} · {names.get(sid, sid)}"
            servers.append(SpendPart(name, row[this_key], row[last_key], acc, sid))
    return SpendFacts(
        currency=cur,
        elapsed=elapsed_days / days_in,
        days=elapsed_days,
        hosters=tuple(parts),
        servers=tuple(servers),
    )


def gather(
    v: View,
    accounts: list[AccountData],
    history: HistoryRepository | None,
    rates: Rates | None,
    currency: str,
    catalogs: Catalogs,
) -> Inputs:
    live = views._live(accounts)
    convert = _convert(rates)
    servers: list[ServerFacts] = []
    facts: list[AccountFacts] = []
    offers: list[Offers] = []
    for data in live:
        acc = data.account.id
        plans = catalogs.found.get(acc)
        if plans is not None:
            offers.append(Offers(acc, data.plugin.meta.id, data.title, plans.catalog))
        missing = (
            None
            if data.forecast is None or data.forecast.shortfall is None
            else (data.forecast.shortfall.missing)
        )
        facts.append(
            AccountFacts(
                acc,
                data.title,
                data.forecast,
                None if missing is None else convert(missing, currency),
            )
        )
        for server in data.servers:
            sid = server.external_id
            month = views._month_price(server)
            placed = plans.servers.get(sid) if plans is not None else None
            plan = None if placed is None else placed.plan
            specs = parse_specs(plan.specs) if plan is not None else parse_specs(server.plan)
            load30: LoadStats | None = None
            load7: LoadStats | None = None
            stopped = None
            if history is not None:
                hours = _hours(history, acc, sid, v.now)
                load30 = load_stats(hours, v.now - LONG_WINDOW, v.now)
                load7 = load_stats(hours, v.now - SHORT_WINDOW, v.now)
                stopped = _stopped_since(history, acc, server)
            servers.append(
                ServerFacts(
                    account_id=acc,
                    plugin=data.plugin.meta.id,
                    hoster=data.title,
                    server=server,
                    monthly=month,
                    monthly_owner=None if month is None else convert(month, currency),
                    renews=views._renews(server, data),
                    plan=plan,
                    location_id=None if placed is None else placed.location_id,
                    specs=specs,
                    usage_supported=Capability.USAGE in data.caps,
                    load30=load30,
                    load7=load7,
                    stopped_since=stopped,
                )
            )
    spend = None if history is None else _spend(v, live, history, rates, currency)
    return Inputs(
        now=v.now,
        currency=currency,
        servers=servers,
        accounts=facts,
        offers=offers,
        spend=spend,
        convert=convert,
    )


# Words


def money_text(money: Money) -> str:
    """«1 200 ₽», «9.40 €»: kopecks only on small sums."""
    if abs(money.amount) >= 100:
        rounded = Money(amount=money.amount.quantize(Decimal(1)), currency=money.currency)
        return str(rounded).replace(".00", "")
    return str(money)


def _amount_text(value: Decimal, currency: str) -> str:
    return money_text(Money(amount=value, currency=currency))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}%"


def _offer(v: View, tip: Tip) -> str | None:
    c = tip.choice
    if c is None:
        return None
    specs = f" ({c.plan.specs})" if c.plan.specs else ""
    return v.t(
        "tips.offer",
        plan=c.plan.name,
        specs=specs,
        location=c.location,
        price=money_text(c.month),
    )


def _days(load: LoadStats | None, window: int) -> int:
    return window if load is None else min(window, round(load.days))


def word(v: View, tip: Tip) -> TipOut:
    k = tip.kind
    note: str | None = None
    lines: list[str] = []
    offer = _offer(v, tip)
    current = "—" if tip.current is None else money_text(tip.current)
    if k is TipKind.OVERSIZED:
        load = tip.load
        assert load is not None
        title = v.t("tips.oversized")
        why = v.t(
            "tips.oversized.why",
            cpu=_pct(load.peak95["cpu"]),
            ram=_pct(load.peak95["ram"]),
            days=_days(load, tip.window_days),
        )
        if tip.choice is not None:
            note = v.t("tips.now", price=current)
        else:
            note = v.t("tips.no_catalog_plan")
    elif k is TipKind.CRAMPED:
        load = tip.load
        assert load is not None
        names = {"cpu": "CPU", "ram": "RAM", "disk": v.t("card.disk")}
        parts = ", ".join(f"{names[m]} p95 {_pct(load.p95[m])}" for m in tip.metrics)
        title = v.t("tips.cramped")
        why = v.t("tips.cramped.why", parts=parts, days=_days(load, tip.window_days))
        note = v.t("tips.now", price=current) if tip.choice else v.t("tips.cramped.generic")
    elif k is TipKind.CHEAPER:
        assert tip.choice is not None
        title = v.t("tips.cheaper", hoster=tip.choice.hoster)
        why = v.t("tips.cheaper.why", price=current)
        note = v.t("tips.cheaper.note")
    elif k is TipKind.RENEWAL:
        assert tip.at is not None
        title = v.t("tips.renewal")
        price = "—" if tip.money is None else money_text(tip.money)
        why = v.t("tips.renewal.why", date=v.when(tip.at, exact=False), price=price)
    elif k is TipKind.SHORTFALL:
        assert tip.at is not None and tip.money is not None
        title = v.t("tips.shortfall")
        why = v.t(
            "tips.shortfall.why",
            date=v.when(tip.at, exact=False),
            amount=money_text(tip.money),
            server=tip.server or "—",
        )
    elif k is TipKind.IDLE:
        assert tip.at is not None
        title = v.t("tips.idle")
        days = (v.now - tip.at).days
        why = v.t("tips.idle.why", date=v.day(tip.at), days=days, price=current)
    elif k is TipKind.TREND:
        assert tip.pace is not None and tip.last is not None and tip.amount is not None
        cur = tip.amount.currency
        title = v.t("tips.trend")
        why = v.t(
            "tips.trend.why",
            days=tip.window_days,
            pace=_amount_text(tip.pace, cur),
            last=_amount_text(tip.last, cur),
        )
        for part in tip.spend[:NAMES_SHOWN]:
            lines.append(
                v.t(
                    "tips.trend.line",
                    name=part.name,
                    now=_amount_text(part.this_month, cur),
                    last=_amount_text(part.last_month, cur),
                )
            )
        note = v.t("tips.trend.note")
    else:
        title = v.t("tips.no_data")
        why = v.t("tips.no_data.why", days=MIN_DAYS, count=v.count(len(tip.names), "server"))
        lines = list(tip.names[:NAMES_SHOWN])
        if len(tip.names) > NAMES_SHOWN:
            lines.append(v.t("home.more", count=len(tip.names) - NAMES_SHOWN))
        if tip.ready_at is not None and tip.ready_at > v.now:
            note = v.t("tips.no_data.ready", date=v.day(tip.ready_at))
    per_month = k not in (TipKind.RENEWAL, TipKind.SHORTFALL)
    return TipOut(
        kind=k.value,
        account_id=tip.account_id,
        server_id=tip.server_id,
        hoster=tip.hoster,
        server=tip.server,
        title=title,
        why=why,
        offer=offer,
        note=note,
        lines=lines,
        amount=None
        if tip.amount is None
        else Amount(amount=str(tip.amount.amount), currency=tip.amount.currency),
        impact=None if tip.impact is None else tip.impact.value,
        per_month=per_month and tip.impact is not Impact.RISK,
    )


def build_tips(
    v: View,
    accounts: list[AccountData],
    history: HistoryRepository | None,
    rates: Rates | None,
    currency: str,
    catalogs: Catalogs,
) -> TipsOut:
    inputs = gather(v, accounts, history, rates, currency, catalogs)
    titles = {d.account.id: d.title for d in accounts}
    return TipsOut(
        lang=v.lang,
        currency=currency,
        generated_at=v.now,
        tips=[word(v, tip) for tip in tips(inputs)],
        no_catalog=[titles.get(acc, "?") for acc in catalogs.failed],
        collecting_since=None if history is None else history.started(),
    )
