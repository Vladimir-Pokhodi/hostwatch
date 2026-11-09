"""The data behind the Mini App tabs (1.1, part 5): servers, a server's page with its load,
money by month and the journal. Counted the way the chat counts; read-only.

Money comes from two sources that overlap (see `hostwatch.storage.history`): totals are
summed from `observed` only, the split by server from `hoster` only. A hoster that ties
no charge to a server gets its charges split by tariff prices, marked as an estimate.
"""

from __future__ import annotations

import calendar
import itertools
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from html import unescape

from hostwatch.core.alerts import Thresholds
from hostwatch.core.history import MoneyKind, MoneySource, ServerEventKind
from hostwatch.core.models import BillingModel, Money, Server, ServerStatus
from hostwatch.core.plugin import Capability
from hostwatch.core.rates import Rates, RatesUnavailable
from hostwatch.storage.history import (
    METRICS,
    HistoryRepository,
    MonthTotal,
    UsagePoint,
)
from hostwatch.storage.repositories import JournalEntry, JournalRepository
from hostwatch.web.miniapp import (
    METRIC_NAMES,
    CostMonth,
    CostOut,
    HosterMoney,
    HosterMonth,
    HosterRef,
    JournalEntryOut,
    JournalGroup,
    JournalOut,
    JournalQuery,
    Limits,
    MoneyEventOut,
    MoneyEventsOut,
    MoneyEventsQuery,
    MoneyMonth,
    MoneyOut,
    MoneyQuery,
    Overview,
    SeriesOut,
    ServerEventOut,
    ServerOut,
    ServerPage,
    ServersOut,
    ServerSpend,
    Span,
    UsageOut,
)

from . import views
from .hub import AccountData, Hub
from .views import View
from .webapp import LEVEL_NAME, _amount, _sum, build_overview

SPANS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "1y": timedelta(days=365),
}
# `usage_samples` column -> name in the API; the traffic counter is not charted.
METRIC_OUT = dict(
    zip(("cpu", "ram", "disk", "net_in_mbits", "net_out_mbits"), METRIC_NAMES, strict=True)
)
METRICS_IN = tuple(METRIC_OUT)
CENT = Decimal("0.01")
# A gap longer than this between two samples breaks the line; polls come every 15 min.
SAMPLE_GAP = timedelta(minutes=45)
EVENTS_SHOWN = 100
# Months of a server's estimated cost.
COST_MONTHS = 24

JOURNAL_GROUPS: dict[str, JournalGroup] = {
    **dict.fromkeys(
        (
            "start",
            "stop",
            "restart",
            "autorenew_on",
            "autorenew_off",
            "renew",
            "order",
            "root_password",
        ),
        "servers",
    ),
    **dict.fromkeys(
        (
            "topup",
            "topup_paid",
            "topup_result",
            "pay_invoice",
            "autopay_mark",
            "autopay_hold",
            "autopay_answer",
        ),
        "money",
    ),
    **dict.fromkeys(("login", "connect", "disconnect", "account_status"), "accounts"),
    "settings": "settings",
}


def _tz_name(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or str(tz)


def _month(at: datetime, tz: tzinfo) -> str:
    return at.astimezone(tz).strftime("%Y-%m")


def _months_back(month: str, count: int) -> list[str]:
    """`count` months ending with `month`, oldest first."""
    year, number = (int(part) for part in month.split("-"))
    found = []
    for _ in range(count):
        found.append(f"{year}-{number:02d}")
        year, number = (year - 1, 12) if number == 1 else (year, number - 1)
    return found[::-1]


def _month_start(month: str, tz: tzinfo) -> datetime:
    year, number = (int(part) for part in month.split("-"))
    return datetime.combine(date(year, number, 1), time(), tz)


def _next_month(start: datetime) -> datetime:
    days = calendar.monthrange(start.year, start.month)[1]
    return (start.replace(day=1) + timedelta(days=days + 1)).replace(day=1)


def _convert(money: Money, rates: Rates | None, currency: str) -> Money | None:
    if money.currency == currency:
        return money
    return None if rates is None else rates.convert(money, currency)


def _cents(money: Money) -> Money:
    return Money(amount=money.amount.quantize(CENT), currency=money.currency)


def _days_to(v: View, at: datetime) -> int:
    return (at.astimezone(v.tz).date() - v.now.astimezone(v.tz).date()).days


# Servers


def server_out(
    v: View,
    data: AccountData,
    server: Server,
    found: list[views.Problem],
    rates: Rates | None,
    currency: str,
) -> ServerOut:
    acc, sid = data.account.id, server.external_id
    mine = [p for p in found if p.account_id == acc and p.server_id == sid]
    level = LEVEL_NAME[views._level_of(mine)]
    owner_stop = server.status is ServerStatus.STOPPED and sid in data.owner_stopped
    if owner_stop and not mine:
        level = "off"
    elif server.status is ServerStatus.UNKNOWN and not mine:
        level = "unknown"
    usage = data.usage.get(sid)
    month = views._month_price(server)
    converted = None if month is None else _convert(month, rates, currency)
    charge = server.next_charge_at if server.billing is BillingModel.PERIODIC else None
    return ServerOut(
        account_id=acc,
        hoster=data.title,
        server_id=sid,
        name=server.name,
        kind=server.kind.value,
        status=server.status.value,
        level=level,
        problems=[unescape(p.what) for p in mine if not p.load],
        ip=server.ip,
        plan=server.plan,
        billing=server.billing.value,
        price=None if server.price is None else _amount(server.price),
        period_months=server.period_months,
        monthly=None if month is None else _amount(_cents(month)),
        monthly_converted=None if converted is None else _amount(_cents(converted)),
        next_charge_at=charge,
        days_to_charge=None if charge is None else _days_to(v, charge),
        autorenew=server.autorenew.value,
        autopay=sid in data.autopay,
        usage_supported=Capability.USAGE in data.caps,
        cpu=None if usage is None else usage.cpu_percent,
        ram=None if usage is None else usage.ram_percent,
        disk=None if usage is None else usage.disk_percent,
        net_in=None if usage is None else usage.net_in_mbits,
        net_out=None if usage is None else usage.net_out_mbits,
        usage_at=None if usage is None else usage.measured_at,
        high_load=any(p.load for p in mine),
    )


def _limits(th: Thresholds | None) -> Limits:
    th = th or Thresholds()
    return Limits(load=th.load_percent, disk=th.disk_percent, days=th.balance_days)


def _hoster_ref(data: AccountData) -> HosterRef:
    return HosterRef(account_id=data.account.id, title=data.title, plugin=data.plugin.meta.id)


def build_servers(
    v: View,
    accounts: list[AccountData],
    rates: Rates | None,
    currency: str,
    th: Thresholds | None = None,
) -> ServersOut:
    live = views._live(accounts)
    found = views.problems(v, live, th)
    return ServersOut(
        lang=v.lang,
        currency=currency,
        tz=_tz_name(v.tz),
        generated_at=v.now,
        limits=_limits(th),
        hosters=[_hoster_ref(d) for d in live],
        servers=[
            server_out(v, d, s, found, rates, currency)
            for d in live
            for s in views._ordered(d.servers)
        ],
    )


def _find(
    accounts: list[AccountData], account_id: int, server_id: str
) -> tuple[AccountData, Server] | None:
    for data in views._live(accounts):
        if data.account.id != account_id:
            continue
        for server in data.servers:
            if server.external_id == server_id:
                return data, server
    return None


def server_cost(
    v: View,
    history: HistoryRepository | None,
    data: AccountData,
    server: Server,
    rates: Rates | None,
    currency: str,
) -> CostOut:
    """The hoster's charges for the server when it reports them; else its month price
    for each month since the bot first saw it, the current month up to today."""
    none = CostOut(basis="none", since=None, total=_sum([], rates, currency), months=[])
    if history is None:
        return none
    acc, sid = data.account.id, server.external_id
    charged = [
        m
        for m in history.money_by_month(MoneySource.HOSTER, v.tz, account_id=acc, server_id=sid)
        if m.charge
    ]
    if charged:
        by_month: dict[str, list[Money]] = {}
        for m in charged:
            by_month.setdefault(m.month, []).append(Money(amount=m.charge, currency=m.currency))
        return CostOut(
            basis="hoster",
            since=None,
            total=_sum([x for xs in by_month.values() for x in xs], rates, currency),
            months=[
                CostMonth(month=k, amount=_sum(xs, rates, currency))
                for k, xs in sorted(by_month.items())
            ][-COST_MONTHS:],
        )
    month_price = views._month_price(server)
    appeared = history.server_events(acc, sid, kinds=[ServerEventKind.APPEARED], limit=1)
    if appeared:
        since: datetime | None = appeared[0].at
    else:
        # It was there when the history began, or showed up in a backfill before that.
        known = [x for x in (history.first_seen(acc, sid), history.started()) if x is not None]
        since = min(known) if known else None
    if month_price is None or since is None or since > v.now:
        return none
    months: list[CostMonth] = []
    spent: list[Money] = []
    start = _month_start(_month(since, v.tz), v.tz)
    while start <= v.now and len(months) < COST_MONTHS * 2:
        end = _next_month(start)
        lo, hi = max(start, since), min(end, v.now)
        share = Decimal((hi - lo).total_seconds()) / Decimal((end - start).total_seconds())
        if share > 0:
            amount = _cents(month_price * share)
            spent.append(amount)
            months.append(
                CostMonth(month=_month(start, v.tz), amount=_sum([amount], rates, currency))
            )
        start = end
    months = months[-COST_MONTHS:]
    return CostOut(basis="estimate", since=since, total=_sum(spent, rates, currency), months=months)


def build_server(
    v: View,
    accounts: list[AccountData],
    history: HistoryRepository | None,
    account_id: int,
    server_id: str,
    rates: Rates | None,
    currency: str,
    th: Thresholds | None = None,
) -> ServerPage | None:
    found_server = _find(accounts, account_id, server_id)
    if found_server is None:
        return None
    data, server = found_server
    found = views.problems(v, [data], th)
    events = (
        [] if history is None else history.server_events(account_id, server_id, limit=EVENTS_SHOWN)
    )
    return ServerPage(
        lang=v.lang,
        currency=currency,
        tz=_tz_name(v.tz),
        limits=_limits(th),
        server=server_out(v, data, server, found, rates, currency),
        events=[ServerEventOut(at=e.at, kind=e.kind, old=e.old, new=e.new) for e in events],
        cost=server_cost(v, history, data, server, rates, currency),
        collecting_since=None if history is None else history.started(),
    )


# Load


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _daily(points: list[UsagePoint], tz: tzinfo) -> list[UsagePoint]:
    """Hours rolled into days of `tz`: the average weighted by samples, the peak."""
    days: dict[date, list[UsagePoint]] = {}
    for point in points:
        days.setdefault(point.at.astimezone(tz).date(), []).append(point)
    result = []
    for day, hours in sorted(days.items()):
        avg: dict[str, float | None] = {}
        peak: dict[str, float | None] = {}
        for m in METRICS:
            weighted = [(h.avg[m], h.samples) for h in hours if h.avg[m] is not None]
            weight = sum(w for _, w in weighted)
            avg[m] = sum(x * w for x, w in weighted if x is not None) / weight if weight else None
            highs = [h.max[m] for h in hours if h.max[m] is not None]
            peak[m] = max(x for x in highs if x is not None) if highs else None
        at = datetime.combine(day, time(), tz)
        result.append(UsagePoint(at, avg, peak, sum(h.samples for h in hours)))
    return result


def _gap(resolution: str, points: list[UsagePoint]) -> timedelta:
    if resolution == "hourly":
        return timedelta(hours=1, minutes=1)
    if resolution == "daily":
        return timedelta(hours=36)
    steps = [b.at - a.at for a, b in itertools.pairwise(points)]
    usual = sorted(steps)[len(steps) // 2] if steps else SAMPLE_GAP
    return max(SAMPLE_GAP, usual * 3)


def build_usage(
    v: View,
    history: HistoryRepository | None,
    accounts: list[AccountData],
    account_id: int,
    server_id: str,
    span: Span,
) -> UsageOut | None:
    """Columns for the charts. A missing stretch gets one empty point, so the line breaks
    instead of crossing it."""
    if _find(accounts, account_id, server_id) is None:
        return None
    end = v.now
    start = end - SPANS[span]
    points: list[UsagePoint] = []
    resolution = "samples" if SPANS[span] <= timedelta(hours=48) else "hourly"
    if history is not None:
        series = history.usage_series(account_id, server_id, start, end)
        points, resolution = series.points, series.resolution
    if span == "1y":
        points, resolution = _daily(points, v.tz), "daily"
    gap = _gap(resolution, points)
    t: list[int] = []
    avg: dict[str, list[float | None]] = {m: [] for m in METRICS_IN}
    peak: dict[str, list[float | None]] = {m: [] for m in METRICS_IN}
    previous: datetime | None = None
    for point in points:
        if previous is not None and point.at - previous > gap:
            t.append(int((previous + (point.at - previous) / 2).timestamp()))
            for m in METRICS_IN:
                avg[m].append(None)
                peak[m].append(None)
        t.append(int(point.at.timestamp()))
        for m in METRICS_IN:
            avg[m].append(_round(point.avg[m]))
            peak[m].append(_round(point.max[m]))
        previous = point.at
    raw = resolution == "samples"
    return UsageOut(
        span=span,
        resolution=resolution,  # type: ignore[arg-type]
        start=start,
        end=end,
        tz=_tz_name(v.tz),
        t=t,
        series={
            METRIC_OUT[m]: SeriesOut(avg=avg[m], max=None if raw else peak[m]) for m in METRICS_IN
        },
        collecting_since=None if history is None else history.started(),
    )


# Money


def _money_of(totals: Iterable[MonthTotal], kind: MoneyKind) -> list[Money]:
    return [
        Money(amount=m.topup if kind is MoneyKind.TOPUP else m.charge, currency=m.currency)
        for m in totals
        if (m.topup if kind is MoneyKind.TOPUP else m.charge)
    ]


def _by_currency(amounts: Iterable[Money]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for money in amounts:
        totals[money.currency] = totals.get(money.currency, Decimal(0)) + money.amount
    return totals


def _split(
    data: AccountData,
    observed: list[Money],
    hoster: list[MonthTotal],
    rates: Rates | None,
    currency: str,
) -> tuple[list[ServerSpend], list[Money]]:
    """The hoster's charges per server and what is left unassigned; without them, the
    observed charges split by the servers' month prices (an estimate)."""
    names = {s.external_id: s.name for s in data.servers}
    if hoster:
        per_server: dict[str, list[Money]] = {}
        for m in hoster:
            if m.server_id is not None and m.charge:
                per_server.setdefault(m.server_id, []).append(
                    Money(amount=m.charge, currency=m.currency)
                )
        spends = [
            ServerSpend(
                server_id=sid,
                name=names.get(sid, sid),
                charge=_sum(xs, rates, currency),
                estimate=False,
            )
            for sid, xs in per_server.items()
        ]
        left = _by_currency(observed)
        for cur, amount in _by_currency(x for xs in per_server.values() for x in xs).items():
            if cur in left:
                left[cur] = max(Decimal(0), left[cur] - amount)
        unassigned = [Money(amount=a, currency=c) for c, a in left.items() if a > 0]
        return sorted(spends, key=lambda s: s.name), unassigned
    spends = []
    left_over: list[Money] = []
    for cur, amount in _by_currency(observed).items():
        prices = {
            s.external_id: p
            for s in data.servers
            if (p := views._month_price(s)) is not None and p.currency == cur and p.amount > 0
        }
        weight = sum((p.amount for p in prices.values()), Decimal(0))
        if not weight:
            left_over.append(Money(amount=amount, currency=cur))
            continue
        for sid, price in prices.items():
            share = _cents(Money(amount=amount * price.amount / weight, currency=cur))
            spends.append(
                ServerSpend(
                    server_id=sid,
                    name=names.get(sid, sid),
                    charge=_sum([share], rates, currency),
                    estimate=True,
                )
            )
    return sorted(spends, key=lambda s: s.name), left_over


def build_money(
    v: View,
    accounts: list[AccountData],
    history: HistoryRepository | None,
    rates: Rates | None,
    currency: str,
    months: int,
) -> MoneyOut:
    current = _month(v.now, v.tz)
    shown = _months_back(current, months)
    first = _month_start(shown[0], v.tz)
    live = views._live(accounts)
    started = None if history is None else history.started()
    observed: list[MonthTotal] = []
    hoster: list[MonthTotal] = []
    since_of: dict[int, datetime | None] = {}
    if history is not None:
        observed = history.money_by_month(MoneySource.OBSERVED, v.tz, since=first)
        hoster = history.money_by_month(MoneySource.HOSTER, v.tz, by_server=True, since=first)
        for data in live:
            balance = history.first_balance(data.account.id)
            known = [x for x in (started, balance) if x is not None]
            since_of[data.account.id] = max(known) if known else None
    started_month = None if started is None else _month(started, v.tz)
    begins = {_month(s, v.tz) for s in since_of.values() if s is not None}
    rows = []
    for month in shown:
        here = [m for m in observed if m.month == month]
        rows.append(
            MoneyMonth(
                month=month,
                partial=month in (current, started_month) or month in begins,
                no_data=started_month is None or month < started_month,
                total=_sum(_money_of(here, MoneyKind.CHARGE), rates, currency),
                hosters=[
                    HosterMonth(
                        account_id=d.account.id,
                        charge=_sum(
                            _money_of(
                                (m for m in here if m.account_id == d.account.id),
                                MoneyKind.CHARGE,
                            ),
                            rates,
                            currency,
                        ),
                    )
                    for d in live
                ],
            )
        )
    hosters = []
    for data in live:
        mine = [m for m in observed if m.account_id == data.account.id]
        charges = _money_of(mine, MoneyKind.CHARGE)
        servers, unassigned = _split(
            data,
            charges,
            [m for m in hoster if m.account_id == data.account.id],
            rates,
            currency,
        )
        hosters.append(
            HosterMoney(
                account_id=data.account.id,
                title=data.title,
                plugin=data.plugin.meta.id,
                since=since_of.get(data.account.id),
                charge=_sum(charges, rates, currency),
                topup=_sum(_money_of(mine, MoneyKind.TOPUP), rates, currency),
                servers=servers,
                unassigned=_sum(unassigned, rates, currency),
            )
        )
    return MoneyOut(
        lang=v.lang,
        currency=currency,
        tz=_tz_name(v.tz),
        rates_date=None if rates is None else rates.date.isoformat(),
        collecting_since=started,
        months=rows,
        hosters=hosters,
        charge=_sum(_money_of(observed, MoneyKind.CHARGE), rates, currency),
        topup=_sum(_money_of(observed, MoneyKind.TOPUP), rates, currency),
    )


def build_money_events(
    accounts: list[AccountData],
    history: HistoryRepository | None,
    query: MoneyEventsQuery,
    rates: Rates | None,
    currency: str,
) -> MoneyEventsOut:
    if history is None:
        return MoneyEventsOut(events=[], has_more=False)
    found = history.money_events(
        query.account,
        source=query.source,
        kind=query.kind,
        limit=query.limit + 1,
        offset=query.offset,
    )
    titles = {d.account.id: d.title for d in accounts}
    names = _server_names(accounts)
    events = []
    for e in found[: query.limit]:
        converted = _convert(e.amount, rates, currency)
        events.append(
            MoneyEventOut(
                id=e.id,
                at=e.at,
                account_id=e.account_id,
                hoster=titles.get(e.account_id, "—"),
                kind=e.kind.value,
                source=e.source.value,
                amount=_amount(e.amount),
                converted=None if converted is None else _amount(converted),
                server_id=e.server_id,
                server=None if e.server_id is None else names.get((e.account_id, e.server_id)),
                description=e.description,
            )
        )
    return MoneyEventsOut(events=events, has_more=len(found) > query.limit)


# Journal


def _server_names(accounts: list[AccountData]) -> dict[tuple[int, str], str]:
    return {(d.account.id, s.external_id): s.name for d in accounts for s in d.servers}


def _journal_hoster(entry: JournalEntry, titles: Mapping[int, str]) -> str | None:
    if entry.account_id is not None and entry.account_id in titles:
        return titles[entry.account_id]
    return entry.params.get("plugin") or None


def build_journal(
    v: View, accounts: list[AccountData], journal: JournalRepository, query: JournalQuery
) -> JournalOut:
    actions: list[str] = []
    exclude: list[str] = []
    if query.group == "other":
        exclude = sorted(JOURNAL_GROUPS)
    elif query.group is not None:
        actions = sorted(a for a, g in JOURNAL_GROUPS.items() if g == query.group)
    since = None if query.since is None else datetime.combine(query.since, time(), v.tz)
    until = None
    if query.until is not None:
        until = datetime.combine(query.until + timedelta(days=1), time(), v.tz)
    found = journal.page(
        query.limit + 1,
        query.offset,
        account_id=query.account,
        actor=query.actor,
        actions=actions,
        exclude=exclude,
        since=since,
        until=until,
    )
    titles = {d.account.id: d.title for d in accounts}
    names = _server_names(accounts)
    entries = [
        JournalEntryOut(
            id=e.id or 0,
            at=e.at,
            actor=e.actor,
            account_id=e.account_id,
            hoster=_journal_hoster(e, titles),
            action=e.action,
            group=JOURNAL_GROUPS.get(e.action, "other"),
            text=views.journal_action(v, e, names),
            ok=e.ok,
        )
        for e in found[: query.limit]
    ]
    return JournalOut(entries=entries, has_more=len(found) > query.limit)


# The source the API reads


@dataclass
class WebData:
    """`hostwatch.web.miniapp.Source` over the bot's data."""

    accounts: Callable[[], list[AccountData]]
    history: HistoryRepository | None
    journal_repo: JournalRepository
    rates: Callable[[], Awaitable[Rates | None]]
    view: Callable[[], View]
    currency: Callable[[], str]
    thresholds: Callable[[], Thresholds]

    @classmethod
    def of(cls, hub: Hub) -> WebData:
        async def rates() -> Rates | None:
            try:
                return await hub.rates.get()
            except RatesUnavailable:
                return None

        return cls(
            accounts=hub.load_all,
            history=hub.history,
            journal_repo=hub.journal,
            rates=rates,
            view=lambda: View(hub.prefs.lang, hub.prefs.tz, hub.now()),
            currency=lambda: hub.prefs.currency,
            thresholds=lambda: hub.prefs.alert_settings(hub.own_ip).thresholds,
        )

    async def overview(self) -> Overview:
        rates = await self.rates()
        return build_overview(
            self.view(), self.accounts(), rates, self.currency(), self.history, self.thresholds()
        )

    async def servers(self) -> ServersOut:
        rates = await self.rates()
        return build_servers(
            self.view(), self.accounts(), rates, self.currency(), self.thresholds()
        )

    async def server(self, account_id: int, server_id: str) -> ServerPage | None:
        rates = await self.rates()
        return build_server(
            self.view(),
            self.accounts(),
            self.history,
            account_id,
            server_id,
            rates,
            self.currency(),
            self.thresholds(),
        )

    async def usage(self, account_id: int, server_id: str, span: Span) -> UsageOut | None:
        return build_usage(self.view(), self.history, self.accounts(), account_id, server_id, span)

    async def money(self, query: MoneyQuery) -> MoneyOut:
        rates = await self.rates()
        return build_money(
            self.view(), self.accounts(), self.history, rates, self.currency(), query.months
        )

    async def money_events(self, query: MoneyEventsQuery) -> MoneyEventsOut:
        rates = await self.rates()
        return build_money_events(self.accounts(), self.history, query, rates, self.currency())

    async def journal(self, query: JournalQuery) -> JournalOut:
        return build_journal(self.view(), self.accounts(), self.journal_repo, query)
