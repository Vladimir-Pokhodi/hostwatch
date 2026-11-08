"""How long the balance lasts, inside one account and in its currency (spec, decision 9)."""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from hostwatch.core.models import Autorenew, Balance, BillingModel, Money, Server
from hostwatch.locales import t

HORIZON = timedelta(days=365)
RECONCILE_WINDOW = timedelta(days=14)
# History must cover almost the whole window, or the real spend says nothing.
RECONCILE_MIN_SPAN = timedelta(days=13)
MISMATCH = Decimal("0.2")
SHOWN_CHARGES = 3


class Method(StrEnum):
    HOSTER_HOURS = "hoster_hours"
    HOURLY_RATE = "hourly_rate"
    CHARGES = "charges"
    MIXED = "mixed"
    NO_SPENDING = "no_spending"


@dataclass(frozen=True)
class Charge:
    at: datetime
    amount: Money
    server: str


@dataclass(frozen=True)
class Shortfall:
    at: datetime
    server: str
    missing: Money


@dataclass(frozen=True)
class NotRenewing:
    server: str
    ends_at: datetime | None


@dataclass(frozen=True)
class Reconciliation:
    actual: Money
    predicted: Money

    @property
    def mismatch(self) -> bool:
        if self.predicted.amount == 0:
            return self.actual.amount > 0
        return abs(self.actual.amount - self.predicted.amount) / self.predicted.amount > MISMATCH


@dataclass(frozen=True)
class Forecast:
    method: Method
    balance: Money
    # None: the money lasts beyond the horizon, or nothing is charged.
    runs_out_at: datetime | None
    paid: tuple[Charge, ...]
    shortfall: Shortfall | None
    not_renewing: tuple[NotRenewing, ...]
    reconciliation: Reconciliation | None
    renewing_count: int

    def explain(self, lang: str, tz: tzinfo = UTC) -> str:
        return "\n".join(_explain(self, lang, tz))


def forecast(
    balance: Balance,
    servers: Sequence[Server],
    now: datetime,
    history: Iterable[tuple[datetime, Money]] = (),
    autopay: frozenset[str] = frozenset(),
) -> Forecast:
    """`autopay` holds external ids of servers that the bot's autopay rule renews."""
    currency = balance.money.currency
    hourly_rate = _hourly_rate(servers, currency)
    renewing = [s for s in servers if _renews(s, autopay) and _priced(s, currency)]
    not_renewing = tuple(
        NotRenewing(s.name, s.next_charge_at)
        for s in servers
        if s.billing is BillingModel.PERIODIC and not _renews(s, autopay)
    )
    charges = sorted(_future_charges(renewing, now), key=lambda c: c.at)
    method = _method(hourly_rate, charges, balance)

    if method is Method.HOSTER_HOURS:
        assert balance.hours_left is not None
        runs_out_at: datetime | None = now + timedelta(hours=balance.hours_left)
        paid: tuple[Charge, ...] = ()
        shortfall = None
    else:
        runs_out_at, paid, shortfall = _walk(balance.money, hourly_rate, charges, now)

    return Forecast(
        method=method,
        balance=balance.money,
        runs_out_at=runs_out_at,
        paid=paid,
        shortfall=shortfall,
        not_renewing=not_renewing,
        reconciliation=_reconcile(history, hourly_rate, renewing, now, currency),
        renewing_count=len(renewing),
    )


def needed_until(
    balance: Balance,
    servers: Sequence[Server],
    now: datetime,
    until: datetime,
    autopay: frozenset[str] = frozenset(),
) -> Money:
    """How much to add so that the money lasts until `until`; zero if it already does."""
    currency = balance.money.currency
    spend = _hourly_rate(servers, currency) * _hours(max(until - now, timedelta(0)))
    renewing = [s for s in servers if _renews(s, autopay) and _priced(s, currency)]
    for charge in _future_charges(renewing, now):
        if charge.at <= until:
            spend += charge.amount
    missing = spend - balance.money
    return missing if missing.amount > 0 else Money(amount=Decimal(0), currency=currency)


def end_of_next_month(now: datetime, tz: tzinfo) -> datetime:
    """Midnight that starts the month after next, in the owner's time zone."""
    local = now.astimezone(tz)
    first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return add_months(first, 2)


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _renews(server: Server, autopay: frozenset[str]) -> bool:
    if server.billing is BillingModel.HOURLY:
        return False
    return server.autorenew is Autorenew.ON or server.external_id in autopay


def _priced(server: Server, currency: str) -> bool:
    return (
        server.price is not None
        and server.price.currency == currency
        and server.period_months is not None
        and server.next_charge_at is not None
    )


def _hourly_rate(servers: Sequence[Server], currency: str) -> Money:
    rate = Money(amount=Decimal(0), currency=currency)
    for server in servers:
        price = server.price
        hourly = server.billing is BillingModel.HOURLY
        if hourly and price is not None and price.currency == currency:
            rate += price
    return rate


def _schedule(server: Server, start: datetime, end: datetime) -> list[datetime]:
    """Charge dates of a renewing server between `start` and `end`, counted from its next charge.

    Works backwards too, to replay what the model would have charged in the past.
    """
    assert server.next_charge_at is not None and server.period_months is not None
    anchor, period = server.next_charge_at, server.period_months
    k = 0
    while add_months(anchor, k * period) > start:
        k -= 1
    dates = []
    while (at := add_months(anchor, k * period)) <= end:
        if at > start:
            dates.append(at)
        k += 1
    return dates


def _future_charges(renewing: Sequence[Server], now: datetime) -> list[Charge]:
    charges = []
    for server in renewing:
        assert server.price is not None and server.next_charge_at is not None
        if server.next_charge_at <= now:
            # Already due: the hoster takes it right away.
            charges.append(Charge(now, server.price, server.name))
        for at in _schedule(server, now, now + HORIZON):
            charges.append(Charge(at, server.price, server.name))
    return charges


def _method(hourly_rate: Money, charges: Sequence[Charge], balance: Balance) -> Method:
    hourly = hourly_rate.amount > 0
    if hourly and not charges:
        return Method.HOURLY_RATE if balance.hours_left is None else Method.HOSTER_HOURS
    if hourly:
        return Method.MIXED
    return Method.CHARGES if charges else Method.NO_SPENDING


def _walk(
    balance: Money, hourly_rate: Money, charges: Sequence[Charge], now: datetime
) -> tuple[datetime | None, tuple[Charge, ...], Shortfall | None]:
    """Go through future charges, draining the hourly rate between them."""
    remaining = balance
    t = now
    paid: list[Charge] = []
    for charge in charges:
        drain = hourly_rate * _hours(charge.at - t)
        if drain > remaining:
            return _drained_at(t, remaining, hourly_rate), tuple(paid), None
        remaining -= drain
        t = charge.at
        if charge.amount > remaining:
            missing = charge.amount - remaining
            return charge.at, tuple(paid), Shortfall(charge.at, charge.server, missing)
        remaining -= charge.amount
        paid.append(charge)
    if hourly_rate.amount > 0:
        out = _drained_at(t, remaining, hourly_rate)
        if out <= now + HORIZON:
            return out, tuple(paid), None
    return None, tuple(paid), None


def _drained_at(start: datetime, remaining: Money, hourly_rate: Money) -> datetime:
    hours = max(remaining.amount, Decimal(0)) / hourly_rate.amount
    return start + timedelta(hours=float(hours))


def _hours(delta: timedelta) -> Decimal:
    return Decimal(delta.total_seconds()) / 3600


def _reconcile(
    history: Iterable[tuple[datetime, Money]],
    hourly_rate: Money,
    renewing: Sequence[Server],
    now: datetime,
    currency: str,
) -> Reconciliation | None:
    points = sorted(
        (at, money)
        for at, money in history
        if money.currency == currency and now - RECONCILE_WINDOW <= at <= now
    )
    if len(points) < 2 or now - points[0][0] < RECONCILE_MIN_SPAN:
        return None
    start = points[0][0]
    actual = Money(amount=Decimal(0), currency=currency)
    for (_, before), (_, after) in pairwise(points):
        # A rise is a top-up, not spending.
        if after < before:
            actual += before - after
    predicted = hourly_rate * _hours(now - start)
    for server in renewing:
        assert server.price is not None
        predicted += server.price * len(_schedule(server, start, now))
    return Reconciliation(actual=actual, predicted=predicted)


def _explain(f: Forecast, lang: str, tz: tzinfo) -> list[str]:
    def day(value: datetime) -> str:
        return value.astimezone(tz).strftime("%d.%m")

    def moment(value: datetime) -> str:
        return value.astimezone(tz).strftime("%d.%m %H:%M")

    lines = [t(f"forecast.method.{f.method.value}", lang, count=f.renewing_count)]
    if f.method is Method.NO_SPENDING:
        lines.append(t("forecast.no_spending", lang))
    elif f.runs_out_at is None:
        lines.append(t("forecast.beyond_horizon", lang))
    else:
        exact = day if f.method is Method.CHARGES else moment
        lines.append(t("forecast.until", lang, date=exact(f.runs_out_at)))
    for charge in f.paid[:SHOWN_CHARGES]:
        lines.append(
            t(
                "forecast.charge",
                lang,
                date=day(charge.at),
                amount=charge.amount,
                server=charge.server,
            )
        )
    if len(f.paid) > SHOWN_CHARGES:
        lines.append(t("forecast.more_charges", lang, count=len(f.paid) - SHOWN_CHARGES))
    if f.shortfall is not None:
        s = f.shortfall
        lines.append(
            t("forecast.shortfall", lang, date=day(s.at), amount=s.missing, server=s.server)
        )
    for item in f.not_renewing:
        if item.ends_at is None:
            lines.append(t("forecast.not_renewing", lang, server=item.server))
        else:
            lines.append(
                t("forecast.not_renewing_until", lang, server=item.server, date=day(item.ends_at))
            )
    r = f.reconciliation
    if r is not None and r.mismatch:
        lines.append(t("forecast.mismatch", lang, actual=r.actual, predicted=r.predicted))
    return lines
