"""Tips on tariffs and money (1.1, part 5, «Советы»): rules over the bot's own data.

Pure functions, no I/O: the caller gathers the load history, the order catalogs and the
money totals, this module decides. A rule says nothing without enough data: the load
must cover at least `MIN_DAYS` days and `MIN_COVERAGE` of the hours in them.

Specs (CPU, RAM, disk) come only from what the hosters' order catalogs tell; the bot
does not guess them from a tariff's name.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from hostwatch.core.catalog import HOURLY, Catalog, Plan, monthly
from hostwatch.core.forecast import Forecast
from hostwatch.core.models import Autorenew, BillingModel, Money, Server, ServerStatus

# How much load history a rule needs.
MIN_DAYS = 7
MIN_COVERAGE = 0.7
LONG_WINDOW = timedelta(days=30)
SHORT_WINDOW = timedelta(days=7)
PERCENTILE = 95
# Oversized: the peaks of 30 days stay under these.
SMALL_CPU = 10.0
SMALL_RAM = 30.0
# Cramped: the hourly averages of 7 days stay above these.
TIGHT_CPU = 85.0
TIGHT_RAM = 90.0
TIGHT_DISK = 90.0
# A smaller plan must hold the peaks at no more than this share of it.
HEADROOM = 0.6
# The disk in use must fill no more than this share of the smaller plan's disk.
DISK_HEADROOM = 0.8
# Another hoster's plan is worth a tip when it is cheaper by this share.
CHEAPER_BY = Decimal("0.15")
RENEWAL_DAYS = 14
SHORTFALL_DAYS = 30
IDLE_DAYS = 7
# Spend trend: this month's pace against last month, after this many days of the month.
TREND_GROWTH = Decimal("0.25")
TREND_MIN_DAYS = 5
METRICS = ("cpu", "ram", "disk")

Convert = Callable[[Money, str], Money | None]


# Specs


@dataclass(frozen=True)
class Specs:
    cpu: float | None = None
    ram_gb: float | None = None
    disk_gb: float | None = None


_PART = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([A-Za-zА-Яа-яЁё ]*)$")
_CPU_WORDS = ("cpu", "core", "ядр", "ядер", "vcpu", "цп")
_RAM_WORDS = ("ram", "озу", "memory", "памят")
_DISK_WORDS = ("nvme", "ssd", "hdd", "disk", "диск", "storage")
_UNITS = {
    "tb": 1024.0,
    "тб": 1024.0,
    "gb": 1.0,
    "гб": 1.0,
    "g": 1.0,
    "mb": 1 / 1024,
    "мб": 1 / 1024,
}


def parse_specs(text: str | None) -> Specs | None:
    """«2 CPU · 4 GB RAM · 50 GB NVME», «1 Core · 1 GB · 30 GB», «2 CPU, 4 GB RAM, 50 GB».

    A size without a word is RAM when it comes first, the disk after it. None when the
    text names no CPU and no size.
    """
    if not text:
        return None
    cpu: float | None = None
    ram: float | None = None
    disk: float | None = None
    for part in re.split(r"[·,;|]", text):
        found = _PART.match(part)
        if found is None:
            continue
        value = float(found.group(1).replace(",", "."))
        words = found.group(2).strip().lower().split()
        unit = next((_UNITS[w] for w in words if w in _UNITS), None)
        rest = " ".join(w for w in words if w not in _UNITS)
        if unit is None:
            if any(w in rest for w in _CPU_WORDS):
                cpu = value
            continue
        size = value * unit
        if any(w in rest for w in _RAM_WORDS):
            ram = size
        elif any(w in rest for w in _DISK_WORDS) or ram is not None:
            disk = size
        else:
            ram = size
    if cpu is None and ram is None and disk is None:
        return None
    return Specs(cpu, ram, disk)


# Load


@dataclass(frozen=True)
class LoadHour:
    """One hour of load: averages and peaks by metric (`cpu`, `ram`, `disk`), percent."""

    at: datetime
    avg: Mapping[str, float | None]
    peak: Mapping[str, float | None]


@dataclass(frozen=True)
class LoadStats:
    # Days between the first hour with data (or the window's start) and the end.
    days: float
    # Share of those hours that have data.
    coverage: float
    # 95th percentile of the hourly averages and of the hourly peaks.
    p95: Mapping[str, float | None]
    peak95: Mapping[str, float | None]
    # When the first hour with data is.
    first: datetime | None

    @property
    def enough(self) -> bool:
        return self.days >= MIN_DAYS - 1 / 24 and self.coverage >= MIN_COVERAGE


def percentile(values: Sequence[float], pct: float = PERCENTILE) -> float | None:
    """Nearest rank: the smallest value with at least `pct` percent of them at or below."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def load_stats(hours: Iterable[LoadHour], start: datetime, end: datetime) -> LoadStats:
    inside = [h for h in hours if start - timedelta(hours=1) < h.at < end]
    if not inside:
        return LoadStats(0.0, 0.0, dict.fromkeys(METRICS), dict.fromkeys(METRICS), None)
    first = min(h.at for h in inside)
    begin = max(start, first)
    span = (end - begin).total_seconds() / 3600
    filled = len({h.at for h in inside if any(h.avg.get(m) is not None for m in METRICS)})
    coverage = min(1.0, filled / span) if span > 0 else 0.0

    def p95(of: str) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for m in METRICS:
            values = [
                v for h in inside if (v := (h.avg if of == "avg" else h.peak).get(m)) is not None
            ]
            result[m] = percentile(values)
        return result

    return LoadStats(span / 24, coverage, p95("avg"), p95("peak"), first)


# What the rules look at


@dataclass(frozen=True)
class ServerFacts:
    account_id: int
    plugin: str
    hoster: str
    server: Server
    # A month of it in its own currency (hourly: 730 hours), and in the owner's.
    monthly: Money | None
    monthly_owner: Money | None
    # The autorenew is on or the bot's autopay pays for it.
    renews: bool
    # The plan of the order catalog it runs on, when the hoster tells.
    plan: Plan | None = None
    location_id: str | None = None
    specs: Specs | None = None
    usage_supported: bool = False
    load30: LoadStats | None = None
    load7: LoadStats | None = None
    # Since when it has been stopped (the bot's history: at least since then).
    stopped_since: datetime | None = None


@dataclass(frozen=True)
class Offers:
    """The order catalog of one connected account."""

    account_id: int
    plugin: str
    hoster: str
    catalog: Catalog


@dataclass(frozen=True)
class AccountFacts:
    account_id: int
    hoster: str
    forecast: Forecast | None
    shortfall_owner: Money | None = None


@dataclass(frozen=True)
class SpendPart:
    """This month and last month of a hoster or a server, in the owner's currency."""

    name: str
    this_month: Decimal
    last_month: Decimal
    account_id: int | None = None
    server_id: str | None = None


@dataclass(frozen=True)
class SpendFacts:
    currency: str
    # Share of this month gone, and its days so far.
    elapsed: float
    days: float
    # Hosters whose whole last month is in the history.
    hosters: tuple[SpendPart, ...]
    # Servers with the hoster's own charges, for the breakdown.
    servers: tuple[SpendPart, ...] = ()


# Tips


class TipKind(StrEnum):
    RENEWAL = "renewal"
    SHORTFALL = "shortfall"
    IDLE = "idle"
    CRAMPED = "cramped"
    OVERSIZED = "oversized"
    CHEAPER = "cheaper"
    TREND = "trend"
    NO_DATA = "no_data"


class Impact(StrEnum):
    # Money the owner saves a month by following the tip.
    SAVING = "saving"
    # Money it costs a month more.
    COST = "cost"
    # Money at stake if nothing is done.
    RISK = "risk"


@dataclass(frozen=True)
class Choice:
    """A plan the tip names: at this account, in this location, for this month price."""

    account_id: int
    hoster: str
    plan: Plan
    location: str
    month: Money
    specs: Specs | None


@dataclass(frozen=True)
class Tip:
    kind: TipKind
    account_id: int | None = None
    hoster: str | None = None
    server_id: str | None = None
    server: str | None = None
    # In the owner's currency, a month unless `kind` is a renewal or a shortfall.
    amount: Money | None = None
    impact: Impact | None = None
    load: LoadStats | None = None
    window_days: int = 0
    # Cramped: the metrics above the limits.
    metrics: tuple[str, ...] = ()
    current: Money | None = None
    choice: Choice | None = None
    at: datetime | None = None
    # A renewal: what it costs, in the server's currency; a shortfall: what is missing.
    money: Money | None = None
    spend: tuple[SpendPart, ...] = ()
    pace: Decimal | None = None
    last: Decimal | None = None
    names: tuple[str, ...] = ()
    ready_at: datetime | None = None


def _month_of(plan: Plan, period: int | None) -> Money:
    """A plan's month price for the period the server pays for, else its usual month."""
    for wanted in (period, 1, HOURLY):
        if wanted is None:
            continue
        found = next((p for p in plan.prices if p.period_months == wanted), None)
        if found is not None:
            return monthly(found)
    return plan.cheapest_month


def _period(server: Server) -> int | None:
    return HOURLY if server.billing is BillingModel.HOURLY else server.period_months


def _location_name(catalog: Catalog, location_id: str) -> str:
    found = catalog.location(location_id)
    return found.name if found is not None else location_id


def _covers(specs: Specs, need: Specs) -> bool:
    for have, want in ((specs.cpu, need.cpu), (specs.ram_gb, need.ram_gb)):
        if want is not None and (have is None or have < want):
            return False
    return need.disk_gb is None or (specs.disk_gb is not None and specs.disk_gb >= need.disk_gb)


def _baseline(f: ServerFacts) -> Money | None:
    """What the server's plan costs a month at its hoster: the catalog price of its plan
    (extras such as backups are not in it), else what the hoster charges."""
    if f.plan is not None:
        return _month_of(f.plan, _period(f.server))
    return f.monthly


def _same_hoster(
    f: ServerFacts,
    offers: Offers | None,
    fits: Callable[[Specs], bool],
    cheaper: bool,
) -> Choice | None:
    """The cheapest plan of the server's own location that `fits`, cheaper (or dearer)
    than its own plan. None when the catalog, the location or the specs are unknown."""
    base = _baseline(f)
    if offers is None or f.location_id is None or f.specs is None or base is None:
        return None
    best: Choice | None = None
    for plan in offers.catalog.plans_in(f.location_id):
        if f.plan is not None and plan.id == f.plan.id:
            continue
        specs = parse_specs(plan.specs)
        if specs is None or not fits(specs):
            continue
        month = _month_of(plan, _period(f.server))
        if month.currency != base.currency:
            continue
        if cheaper and month >= base:
            continue
        if best is None or month < best.month:
            location = _location_name(offers.catalog, f.location_id)
            best = Choice(offers.account_id, offers.hoster, plan, location, month, specs)
    return best


def _owner(money: Money | None, convert: Convert, currency: str) -> Money | None:
    if money is None:
        return None
    return money if money.currency == currency else convert(money, currency)


def _round(money: Money) -> Money:
    return Money(amount=money.amount.quantize(Decimal("0.01")), currency=money.currency)


def _billed(f: ServerFacts) -> bool:
    return f.server.billing is BillingModel.HOURLY or f.renews


def oversized(f: ServerFacts, offers: Offers | None, convert: Convert, currency: str) -> Tip | None:
    load = f.load30
    if load is None or not load.enough or f.server.status is not ServerStatus.RUNNING:
        return None
    cpu, ram = load.peak95["cpu"], load.peak95["ram"]
    if cpu is None or ram is None or cpu >= SMALL_CPU or ram >= SMALL_RAM:
        return None
    tip = Tip(
        TipKind.OVERSIZED,
        f.account_id,
        f.hoster,
        f.server.external_id,
        f.server.name,
        load=load,
        window_days=30,
        current=f.monthly,
    )
    specs = f.specs
    if offers is None or specs is None or f.location_id is None:
        return tip
    disk = load.peak95["disk"]
    need = Specs(
        cpu=None if specs.cpu is None else max(1.0, specs.cpu * cpu / 100 / HEADROOM),
        ram_gb=None if specs.ram_gb is None else specs.ram_gb * ram / 100 / HEADROOM,
        disk_gb=(
            specs.disk_gb
            if disk is None or specs.disk_gb is None
            else specs.disk_gb * disk / 100 / DISK_HEADROOM
        ),
    )
    choice = _same_hoster(f, offers, lambda s: _covers(s, need), cheaper=True)
    if choice is None:
        # The catalog is known and has no smaller plan that holds the peaks.
        return None
    base = _baseline(f)
    assert base is not None
    saving = _owner(base - choice.month, convert, currency)
    return replace(
        tip,
        choice=choice,
        amount=None if saving is None else _round(saving),
        impact=Impact.SAVING,
    )


def cramped(f: ServerFacts, offers: Offers | None, convert: Convert, currency: str) -> Tip | None:
    load = f.load7
    if load is None or not load.enough:
        return None
    limits = {"cpu": TIGHT_CPU, "ram": TIGHT_RAM, "disk": TIGHT_DISK}
    tight = tuple(m for m in METRICS if (v := load.p95[m]) is not None and v > limits[m])
    if not tight:
        return None
    tip = Tip(
        TipKind.CRAMPED,
        f.account_id,
        f.hoster,
        f.server.external_id,
        f.server.name,
        load=load,
        window_days=7,
        metrics=tight,
        current=f.monthly,
    )
    specs = f.specs
    if specs is None:
        return tip
    attrs = {"cpu": "cpu", "ram": "ram_gb", "disk": "disk_gb"}

    def fits(s: Specs) -> bool:
        if not _covers(s, specs):
            return False
        for m in tight:
            have, now = getattr(s, attrs[m]), getattr(specs, attrs[m])
            if have is None or now is None or have <= now:
                return False
        return True

    choice = _same_hoster(f, offers, fits, cheaper=False)
    base = _baseline(f)
    if choice is None or base is None:
        return tip
    extra = _owner(choice.month - base, convert, currency)
    return replace(
        tip,
        choice=choice,
        amount=None if extra is None else _round(extra),
        impact=None if extra is None else Impact.COST,
    )


def cheaper_elsewhere(
    f: ServerFacts, offers: Sequence[Offers], convert: Convert, currency: str
) -> Tip | None:
    specs = f.specs
    base = f.monthly_owner
    if specs is None or specs.cpu is None or specs.ram_gb is None or base is None:
        return None
    if f.server.status not in (ServerStatus.RUNNING, ServerStatus.STOPPED):
        return None
    best: tuple[Money, Choice] | None = None
    for other in offers:
        if other.plugin == f.plugin:
            continue
        for plan in other.catalog.plans:
            theirs = parse_specs(plan.specs)
            if theirs is None or not _covers(theirs, specs):
                continue
            month = _month_of(plan, _period(f.server))
            price = _owner(month, convert, currency)
            if price is None:
                continue
            if best is None or price < best[0]:
                location = _location_name(other.catalog, plan.location_id)
                best = (
                    price,
                    Choice(other.account_id, other.hoster, plan, location, month, theirs),
                )
    if best is None or best[0].amount > base.amount * (1 - CHEAPER_BY):
        return None
    return Tip(
        TipKind.CHEAPER,
        f.account_id,
        f.hoster,
        f.server.external_id,
        f.server.name,
        amount=_round(base - best[0]),
        impact=Impact.SAVING,
        current=f.monthly,
        choice=best[1],
    )


def renewal(f: ServerFacts, now: datetime) -> Tip | None:
    s = f.server
    ends = s.next_charge_at
    if s.billing is not BillingModel.PERIODIC or ends is None or f.renews:
        return None
    if s.autorenew is not Autorenew.OFF or not now <= ends <= now + timedelta(days=RENEWAL_DAYS):
        return None
    return Tip(
        TipKind.RENEWAL,
        f.account_id,
        f.hoster,
        s.external_id,
        s.name,
        amount=None if f.monthly_owner is None else _round(f.monthly_owner),
        impact=None if f.monthly_owner is None else Impact.RISK,
        at=ends,
        money=s.price,
    )


def shortfall(a: AccountFacts, now: datetime) -> Tip | None:
    f = a.forecast
    if f is None or f.shortfall is None:
        return None
    if f.shortfall.at > now + timedelta(days=SHORTFALL_DAYS):
        return None
    owner = a.shortfall_owner
    return Tip(
        TipKind.SHORTFALL,
        a.account_id,
        a.hoster,
        server=f.shortfall.server,
        amount=None if owner is None else _round(owner),
        impact=None if owner is None else Impact.RISK,
        at=f.shortfall.at,
        money=f.shortfall.missing,
    )


def idle(f: ServerFacts, now: datetime) -> Tip | None:
    since = f.stopped_since
    if f.server.status is not ServerStatus.STOPPED or since is None:
        return None
    if now - since < timedelta(days=IDLE_DAYS) or not _billed(f) or f.monthly is None:
        return None
    if f.monthly.amount <= 0:
        return None
    return Tip(
        TipKind.IDLE,
        f.account_id,
        f.hoster,
        f.server.external_id,
        f.server.name,
        amount=None if f.monthly_owner is None else _round(f.monthly_owner),
        impact=None if f.monthly_owner is None else Impact.SAVING,
        current=f.monthly,
        at=since,
    )


def trend(spend: SpendFacts | None) -> Tip | None:
    if spend is None or spend.days < TREND_MIN_DAYS or spend.elapsed <= 0 or not spend.hosters:
        return None
    scale = Decimal(1) / Decimal(str(spend.elapsed))
    pace = sum((h.this_month * scale for h in spend.hosters), Decimal(0))
    last = sum((h.last_month for h in spend.hosters), Decimal(0))
    if last <= 0 or pace < last * (1 + TREND_GROWTH):
        return None

    def grew(parts: Iterable[SpendPart]) -> list[SpendPart]:
        up = [p for p in parts if p.this_month * scale > p.last_month]
        return sorted(up, key=lambda p: p.last_month - p.this_month * scale)

    names = {h.account_id for h in spend.hosters}
    servers = [s for s in spend.servers if s.account_id in names]
    cent = Decimal("0.01")
    return Tip(
        TipKind.TREND,
        amount=Money(amount=(pace - last).quantize(cent), currency=spend.currency),
        impact=Impact.COST,
        spend=(*grew(spend.hosters), *grew(servers)),
        pace=pace.quantize(cent),
        last=last.quantize(cent),
        window_days=int(spend.days),
    )


def no_data(servers: Sequence[ServerFacts]) -> Tip | None:
    """One card for the servers whose load history is too short for the load rules."""
    short = [
        f
        for f in servers
        if f.usage_supported
        and f.server.status is ServerStatus.RUNNING
        and (f.load30 is None or not f.load30.enough)
    ]
    if not short:
        return None
    firsts = [f.load30.first for f in short if f.load30 is not None and f.load30.first is not None]
    ready = max(firsts) + timedelta(days=MIN_DAYS) if len(firsts) == len(short) else None
    return Tip(
        TipKind.NO_DATA,
        names=tuple(f"{f.hoster} · {f.server.name}" for f in short),
        ready_at=ready,
    )


ORDER = {kind: i for i, kind in enumerate(TipKind)}


def _key(tip: Tip) -> tuple[int, Decimal, int]:
    if tip.kind is TipKind.NO_DATA:
        return (2, Decimal(0), ORDER[tip.kind])
    if tip.amount is None:
        return (1, Decimal(0), ORDER[tip.kind])
    return (0, -abs(tip.amount.amount), ORDER[tip.kind])


@dataclass(frozen=True)
class Inputs:
    now: datetime
    currency: str
    servers: Sequence[ServerFacts]
    accounts: Sequence[AccountFacts] = ()
    offers: Sequence[Offers] = ()
    spend: SpendFacts | None = None
    convert: Convert = field(default=lambda money, to: None)


def tips(inputs: Inputs) -> list[Tip]:
    """Every tip, the most money first; those without a sum after them."""
    by_account = {o.account_id: o for o in inputs.offers}
    found: list[Tip] = []
    for f in inputs.servers:
        own = by_account.get(f.account_id)
        rules = (
            renewal(f, inputs.now),
            idle(f, inputs.now),
            cramped(f, own, inputs.convert, inputs.currency),
            oversized(f, own, inputs.convert, inputs.currency),
            cheaper_elsewhere(f, inputs.offers, inputs.convert, inputs.currency),
        )
        found += [tip for tip in rules if tip is not None]
    for a in inputs.accounts:
        tip = shortfall(a, inputs.now)
        if tip is not None:
            found.append(tip)
    for extra in (trend(inputs.spend), no_data(inputs.servers)):
        if extra is not None:
            found.append(extra)
    return sorted(found, key=_key)
