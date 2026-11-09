"""Tips on tariffs and money (1.1, stage 7): every rule on made-up data, with too little
data too, and the tab through the API."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from hostwatch.bot.tips import (
    Catalogs,
    HosterPlans,
    Placed,
    PlanReader,
    build_tips,
    match_plan,
    money_text,
)
from hostwatch.bot.webdata import WebData
from hostwatch.core.alerts import Thresholds
from hostwatch.core.catalog import HOURLY, Catalog, Location, OrderTemplate, Plan, Price
from hostwatch.core.forecast import Forecast, Method, Shortfall
from hostwatch.core.history import MoneyKind, MoneyMove, MoneySource, ServerChange
from hostwatch.core.history import ServerEventKind as Ev
from hostwatch.core.models import Autorenew, BillingModel, Money, ServerStatus, Usage
from hostwatch.core.rates import Rates
from hostwatch.core.tips import (
    AccountFacts,
    Impact,
    Inputs,
    LoadHour,
    LoadStats,
    Offers,
    ServerFacts,
    Specs,
    SpendFacts,
    SpendPart,
    TipKind,
    cheaper_elsewhere,
    cramped,
    idle,
    load_stats,
    no_data,
    oversized,
    parse_specs,
    percentile,
    renewal,
    shortfall,
    tips,
    trend,
)
from hostwatch.plugins.aeza import AezaPlugin
from hostwatch.plugins.timeweb import TimewebPlugin
from hostwatch.storage.history import STARTED_KEY, HistoryRepository
from hostwatch.storage.repositories import AccountRepository, JournalRepository
from tests.test_views import EN, NOW, RU, data, server

RATES = Rates(NOW.date(), {"EUR": Decimal("100")})


def rub(x: str) -> Money:
    return Money.of(x, "RUB")


def convert(money: Money, to: str) -> Money | None:
    return money if money.currency == to else RATES.convert(money, to)


def hours(
    days: float,
    cpu: float | None = 5.0,
    ram: float | None = 20.0,
    disk: float | None = 30.0,
    *,
    every: int = 1,
    end: datetime = NOW,
) -> list[LoadHour]:
    """Hours of load ending now; `every` > 1 leaves gaps."""
    found = []
    for i in range(0, int(days * 24), every):
        at = end - timedelta(hours=i + 1)
        values = {"cpu": cpu, "ram": ram, "disk": disk}
        found.append(LoadHour(at, values, values))
    return found


def stats(days: float = 30, **kw: Any) -> LoadStats:
    window = timedelta(days=30 if days > 7 else 7)
    return load_stats(hours(days, **kw), NOW - window, NOW)


PLANS = (
    Plan(
        id="s",
        name="S",
        location_id="nl",
        specs="1 CPU · 1 GB RAM · 20 GB",
        prices=(Price(period_months=1, amount=rub("300")),),
    ),
    Plan(
        id="m",
        name="M",
        location_id="nl",
        specs="2 CPU · 4 GB RAM · 50 GB",
        prices=(Price(period_months=1, amount=rub("690")),),
    ),
    Plan(
        id="l",
        name="L",
        location_id="nl",
        specs="4 CPU · 8 GB RAM · 100 GB",
        prices=(Price(period_months=1, amount=rub("1400")),),
    ),
    Plan(
        id="m-de",
        name="M DE",
        location_id="de",
        specs="2 CPU · 4 GB RAM · 50 GB",
        prices=(Price(period_months=1, amount=rub("100")),),
    ),
)
CATALOG = Catalog(
    locations=(Location(id="nl", name="Нидерланды"), Location(id="de", name="Germany")),
    plans=PLANS,
    systems=(),
)
OWN = Offers(1, "timeweb", "Timeweb", CATALOG)


def facts(**changes: Any) -> ServerFacts:
    base = ServerFacts(
        account_id=1,
        plugin="timeweb",
        hoster="Timeweb",
        server=server("1", "bear"),
        monthly=rub("690"),
        monthly_owner=rub("690"),
        renews=True,
        plan=PLANS[1],
        location_id="nl",
        specs=Specs(2, 4, 50),
        usage_supported=True,
        load30=stats(30, ram=10.0),
        load7=stats(7),
    )
    return dataclasses.replace(base, **changes)


# Specs and load


@pytest.mark.parametrize(
    ("text", "specs"),
    [
        ("1 CPU · 2 GB RAM · 10 GB", Specs(1, 2, 10)),
        ("2 CPU · 4 GB RAM · 50 GB NVME", Specs(2, 4, 50)),
        ("1 Core · 1 GB · 30 GB", Specs(1, 1, 30)),
        ("2 CPU, 4 GB RAM, 50 GB", Specs(2, 4, 50)),
        ("4 CPU · 0.5 TB", Specs(4, 512, None)),
        ("1 CPU · 1 GB", Specs(1, 1, None)),
    ],
)
def test_parse_specs(text: str, specs: Specs) -> None:
    assert parse_specs(text) == specs


@pytest.mark.parametrize("text", [None, "", "VPS 2/4/50", "Cloud VPS X1 - Europe", "fake-1"])
def test_names_are_not_specs(text: str | None) -> None:
    assert parse_specs(text) is None


def test_percentile_nearest_rank() -> None:
    assert percentile([]) is None
    assert percentile([float(x) for x in range(1, 101)]) == 95
    assert percentile([3.0]) == 3


def test_load_stats_days_and_coverage() -> None:
    full = stats(30)
    assert full.enough and full.days == pytest.approx(30, abs=0.1)
    assert full.coverage == pytest.approx(1.0, abs=0.02)
    assert full.peak95["cpu"] == 5
    young = stats(5)
    assert not young.enough  # five days are too few
    gappy = load_stats(hours(30, every=2), NOW - timedelta(days=30), NOW)
    assert gappy.coverage == pytest.approx(0.5, abs=0.02)
    assert not gappy.enough
    empty = load_stats([], NOW - timedelta(days=30), NOW)
    assert not empty.enough and empty.first is None


# 1. Oversized


def test_oversized_names_a_smaller_plan_and_the_saving() -> None:
    tip = oversized(facts(), OWN, convert, "RUB")
    assert tip is not None and tip.kind is TipKind.OVERSIZED
    assert tip.choice is not None and tip.choice.plan.id == "s"  # same location only
    assert tip.amount == rub("390") and tip.impact is Impact.SAVING


def test_oversized_needs_room_for_the_peaks() -> None:
    # RAM peaks of 28% of 4 GB = 1.1 GB: 1 GB with headroom is too small, nothing fits.
    f = facts(load30=stats(30, ram=28.0))
    assert oversized(f, OWN, convert, "RUB") is None


def test_oversized_without_catalog_is_generic() -> None:
    tip = oversized(facts(), None, convert, "RUB")
    assert tip is not None and tip.choice is None and tip.amount is None


@pytest.mark.parametrize(
    "changes",
    [
        {"load30": stats(5)},  # not enough days
        {"load30": load_stats(hours(30, every=2), NOW - timedelta(days=30), NOW)},  # gaps
        {"load30": stats(30, cpu=12.0)},
        {"load30": stats(30, ram=35.0)},
        {"load30": stats(30, ram=None)},
        {"load30": None},
        {"server": server("1", "bear", status=ServerStatus.STOPPED)},
    ],
)
def test_oversized_says_nothing_without_reason(changes: dict[str, Any]) -> None:
    assert oversized(facts(**changes), OWN, convert, "RUB") is None


# 2. Cramped


def test_cramped_suggests_a_bigger_plan() -> None:
    tip = cramped(facts(load7=stats(7, cpu=92.0)), OWN, convert, "RUB")
    assert tip is not None and tip.metrics == ("cpu",)
    assert tip.choice is not None and tip.choice.plan.id == "l"
    assert tip.amount == rub("710") and tip.impact is Impact.COST


def test_cramped_disk_and_generic() -> None:
    tip = cramped(facts(load7=stats(7, disk=95.0), specs=None), None, convert, "RUB")
    assert tip is not None and tip.metrics == ("disk",)
    assert tip.choice is None and tip.amount is None


@pytest.mark.parametrize(
    "load",
    [stats(7, cpu=80.0, ram=89.0, disk=89.0), stats(5, cpu=99.0), None],
)
def test_cramped_needs_a_week_above_the_limits(load: LoadStats | None) -> None:
    assert cramped(facts(load7=load), OWN, convert, "RUB") is None


# 3. Cheaper elsewhere


def aeza(price: str, specs: str = "2 CPU · 4 GB RAM · 60 GB", cur: str = "EUR") -> Offers:
    plan = Plan(
        id="a1",
        name="Shared 2",
        location_id="1",
        specs=specs,
        prices=(Price(period_months=1, amount=Money.of(price, cur)),),
    )
    catalog = Catalog(locations=(Location(id="1", name="NL"),), plans=(plan,), systems=())
    return Offers(2, "aeza", "Aeza", catalog)


def test_cheaper_elsewhere_after_conversion() -> None:
    tip = cheaper_elsewhere(facts(), [OWN, aeza("5.00")], convert, "RUB")
    assert tip is not None and tip.kind is TipKind.CHEAPER
    assert tip.choice is not None and tip.choice.hoster == "Aeza"
    assert tip.amount == rub("190")  # 690 - 5 EUR × 100


@pytest.mark.parametrize(
    "offers",
    [
        [aeza("6.00")],  # 600 is only 13% cheaper
        [aeza("1.00", specs="1 CPU · 4 GB RAM · 60 GB")],  # fewer CPUs
        [aeza("1.00", specs="2 CPU · 4 GB RAM · 40 GB")],  # less disk
        [aeza("1.00", cur="USD")],  # no rate
        [OWN],  # the same hoster is rule 1's business
        [],
    ],
)
def test_cheaper_elsewhere_only_when_clearly_cheaper(offers: list[Offers]) -> None:
    assert cheaper_elsewhere(facts(), offers, convert, "RUB") is None


def test_cheaper_elsewhere_needs_specs() -> None:
    assert cheaper_elsewhere(facts(specs=None), [aeza("1.00")], convert, "RUB") is None


# 4. Renewal and money


def test_renewal_off_and_ending() -> None:
    s = server(autorenew=Autorenew.OFF, next_charge_at=NOW + timedelta(days=5))
    tip = renewal(facts(server=s, renews=False), NOW)
    assert tip is not None and tip.at == s.next_charge_at and tip.impact is Impact.RISK
    far = server(autorenew=Autorenew.OFF, next_charge_at=NOW + timedelta(days=20))
    assert renewal(facts(server=far, renews=False), NOW) is None
    # The bot's autopay pays for it: renews.
    assert renewal(facts(server=s, renews=True), NOW) is None
    hourly = server(autorenew=Autorenew.OFF, billing=BillingModel.HOURLY)
    assert renewal(facts(server=hourly, renews=False), NOW) is None


def test_shortfall_from_the_forecast() -> None:
    gap = Shortfall(NOW + timedelta(days=10), "bear", rub("250"))
    f = Forecast(Method.CHARGES, rub("440"), None, (), gap, (), None, 1)
    tip = shortfall(AccountFacts(1, "Timeweb", f, rub("250")), NOW)
    assert tip is not None and tip.money == rub("250") and tip.amount == rub("250")
    late = dataclasses.replace(f, shortfall=dataclasses.replace(gap, at=NOW + timedelta(days=60)))
    assert shortfall(AccountFacts(1, "Timeweb", late), NOW) is None
    assert shortfall(AccountFacts(1, "Timeweb", None), NOW) is None


# 5. Idle


def test_idle_stopped_for_a_week_and_billed() -> None:
    off = server(status=ServerStatus.STOPPED)
    tip = idle(facts(server=off, stopped_since=NOW - timedelta(days=9)), NOW)
    assert tip is not None and tip.amount == rub("690") and tip.impact is Impact.SAVING
    assert idle(facts(server=off, stopped_since=NOW - timedelta(days=3)), NOW) is None
    assert idle(facts(server=off, stopped_since=None), NOW) is None
    # It ends by itself: not billed any more.
    ending = server(status=ServerStatus.STOPPED, autorenew=Autorenew.OFF)
    since = NOW - timedelta(days=9)
    assert idle(facts(server=ending, renews=False, stopped_since=since), NOW) is None
    hourly = server(status=ServerStatus.STOPPED, autorenew=Autorenew.NOT_AVAILABLE)
    hourly = hourly.model_copy(update={"billing": BillingModel.HOURLY})
    assert idle(facts(server=hourly, renews=False, stopped_since=since), NOW) is not None


# 6. Spend trend


def spend(this: str, last: str, days: float = 10, **kw: Any) -> SpendFacts:
    parts = (SpendPart("Timeweb", Decimal(this), Decimal(last), 1),)
    return SpendFacts("RUB", days / 30, days, parts, **kw)


def test_trend_with_breakdown() -> None:
    servers = (
        SpendPart("Timeweb · bear", Decimal("900"), Decimal("1000"), 1, "1"),
        SpendPart("Timeweb · fox", Decimal("100"), Decimal("1000"), 1, "2"),
    )
    tip = trend(spend("1000", "2000", servers=servers))
    assert tip is not None
    assert tip.pace == Decimal("3000.00") and tip.last == Decimal("2000.00")
    assert tip.amount == rub("1000.00")
    assert [p.name for p in tip.spend] == ["Timeweb", "Timeweb · bear"]


@pytest.mark.parametrize(
    "facts_",
    [
        spend("800", "2000"),  # pace 2400: +20%
        spend("1000", "2000", days=3),  # too early in the month
        spend("1000", "0"),  # nothing last month to compare with
        None,
    ],
)
def test_trend_quiet(facts_: SpendFacts | None) -> None:
    assert trend(facts_) is None


# Not enough data, and the order


def test_no_data_is_one_card() -> None:
    young = [
        facts(load30=stats(3)),
        facts(server=server("2", "fox"), load30=stats(2)),
        facts(server=server("3", "owl")),
        facts(server=server("4", "cat"), usage_supported=False, load30=None),
    ]
    tip = no_data(young)
    assert tip is not None and tip.names == ("Timeweb · bear", "Timeweb · fox")
    assert tip.ready_at is not None
    assert tip.ready_at == pytest.approx(
        NOW - timedelta(days=2) + timedelta(days=7), abs=timedelta(hours=2)
    )  # type: ignore[arg-type]


def test_tips_sorted_by_money() -> None:
    off = server("2", "fox", status=ServerStatus.STOPPED, price=rub("2000"))
    gap = Shortfall(NOW + timedelta(days=10), "bear", rub("50"))
    f = Forecast(Method.CHARGES, rub("440"), None, (), gap, (), None, 1)
    found = tips(
        Inputs(
            now=NOW,
            currency="RUB",
            servers=[
                facts(),
                facts(
                    server=off,
                    monthly=rub("2000"),
                    monthly_owner=rub("2000"),
                    stopped_since=NOW - timedelta(days=10),
                    load30=None,
                    load7=None,
                    usage_supported=False,
                ),
                facts(server=server("3", "owl"), load30=stats(2), load7=stats(2), plan=None),
            ],
            accounts=[AccountFacts(1, "Timeweb", f, rub("50"))],
            offers=[OWN],
            spend=None,
            convert=convert,
        )
    )
    assert [t.kind for t in found] == [
        TipKind.IDLE,
        TipKind.OVERSIZED,
        TipKind.SHORTFALL,
        TipKind.NO_DATA,
    ]


# Words and the API


def test_match_plan_by_name() -> None:
    catalog = Catalog(
        locations=(),
        plans=(
            Plan(id="1", name="Shared 2 · NL", location_id="1", prices=PLANS[0].prices),
            Plan(id="2", name="X1", location_id="1", prices=PLANS[0].prices),
            Plan(id="3", name="X1", location_id="2", prices=PLANS[0].prices),
        ),
        systems=(),
    )
    assert match_plan(catalog, server(plan="Shared 2")) is not None
    assert match_plan(catalog, server(plan="X1")) is None  # two locations: ambiguous
    assert match_plan(catalog, server(plan=None)) is None


def test_money_text() -> None:
    assert money_text(rub("1234.56")) == "1 235 ₽"
    assert money_text(Money.of("9.4", "EUR")) == "€9.40"


async def test_plan_reader_asks_the_template_once() -> None:
    asked: list[str] = []

    async def catalog(acc: int) -> Catalog:
        return CATALOG

    async def template(acc: int, sid: str) -> OrderTemplate:
        asked.append(sid)
        return OrderTemplate(plan_id="m")

    reader = PlanReader(catalog, template)
    account = data([server("1", plan="S"), server("2", plan="something")], plugin=TimewebPlugin)
    for _ in range(2):
        found = await reader([account])
    assert asked == ["2"]
    plans = found.found[1].servers
    assert plans["1"].plan is not None and plans["1"].plan.id == "s"
    assert plans["2"].plan is not None and plans["2"].plan.id == "m"
    assert plans["2"].location_id == "nl"


async def test_plan_reader_survives_a_broken_catalog() -> None:
    async def catalog(acc: int) -> Catalog:
        raise RuntimeError("down")

    async def template(acc: int, sid: str) -> OrderTemplate:
        raise AssertionError

    found = await PlanReader(catalog, template)([data([server()], plugin=TimewebPlugin)])
    assert found.found == {} and found.failed == (1,)


@pytest.fixture
def history(conn: sqlite3.Connection, accounts: AccountRepository) -> HistoryRepository:
    accounts.add("timeweb", "", {})
    accounts.add("aeza", "", {})
    started = NOW - timedelta(days=70)
    conn.execute("UPDATE settings SET value = ? WHERE key = ?", (started.isoformat(), STARTED_KEY))
    conn.execute(
        "INSERT INTO balance_history (account_id, at, amount, currency) VALUES (?, ?, ?, ?)",
        (1, started.isoformat(), "1000", "RUB"),
    )
    conn.commit()
    h = HistoryRepository(conn)
    samples = []
    at = NOW - timedelta(days=20)
    while at < NOW:
        samples.append(
            Usage(server_id="1", measured_at=at, cpu_percent=4.0, ram_percent=9.0, disk_percent=20)
        )
        samples.append(Usage(server_id="3", measured_at=at, cpu_percent=50.0, ram_percent=50.0))
        at += timedelta(minutes=15)
    h.add_samples(1, samples)
    h.add_server_events(
        1, [ServerChange("2", Ev.STATUS, "running", "stopped")], NOW - timedelta(days=12)
    )
    # Last month 600 ₽, this month 900 ₽ in 9 days: pace about 3 000 ₽.
    h.add_money(
        1,
        [
            MoneyMove(
                datetime(2026, 10, 15, tzinfo=UTC),
                MoneyKind.CHARGE,
                rub("600"),
                MoneySource.OBSERVED,
            ),
            MoneyMove(
                datetime(2026, 11, 3, tzinfo=UTC),
                MoneyKind.CHARGE,
                rub("900"),
                MoneySource.OBSERVED,
            ),
        ],
    )
    return h


def accounts_() -> list[Any]:
    tw = data(
        [
            server("1", "bear", plan="M"),
            server("2", "fox", status=ServerStatus.STOPPED, price=rub("400")),
            server(
                "3",
                "owl",
                plan="L",
                next_charge_at=NOW + timedelta(days=4),
                autorenew=Autorenew.OFF,
            ),
        ],
        plugin=TimewebPlugin,
    )
    ae = data(
        [server("7", "beaver", price=Money.of("9", "EUR"))], acc=2, plugin=AezaPlugin, label=""
    )
    return [tw, ae]


def catalogs_() -> Catalogs:
    placed = {
        "1": Placed(PLANS[1], "nl"),
        "2": Placed(None, None),
        "3": Placed(PLANS[2], "nl"),
    }
    theirs = aeza("5.00", specs="4 CPU · 8 GB RAM · 120 GB").catalog
    return Catalogs({1: HosterPlans(CATALOG, placed), 2: HosterPlans(theirs, {})})


def test_build_tips_words_every_card(history: HistoryRepository) -> None:
    out = build_tips(RU, accounts_(), history, RATES, "RUB", catalogs_())
    kinds = [t.kind for t in out.tips]
    assert set(kinds) == {
        "oversized",
        "idle",
        "renewal",
        "trend",
        "cheaper",
        "shortfall",
        "no_data",
    }
    assert kinds[0] == "trend"  # the biggest sum first
    assert kinds[-1] == "no_data"
    assert "Aeza · beaver" in out.tips[-1].lines
    small = next(t for t in out.tips if t.kind == "oversized")
    assert small.server == "bear" and small.hoster
    assert small.why == "CPU p95 4%, RAM p95 9% за 20 дн. (пики по часам)."
    assert small.offer == "S (1 CPU · 1 GB RAM · 20 GB) · Нидерланды — 300 ₽/мес"
    assert small.amount is not None and small.amount.amount == "390.00"
    assert small.impact == "saving" and small.per_month
    idle_ = next(t for t in out.tips if t.kind == "idle")
    assert "как минимум с" in idle_.why and "400 ₽/мес" in idle_.why
    renew = next(t for t in out.tips if t.kind == "renewal")
    assert not renew.per_month and renew.impact == "risk"
    cheap = next(t for t in out.tips if t.kind == "cheaper" and t.server == "owl")
    assert (
        cheap.title == "Дешевле у Aeza"
        and cheap.note == "Сравнение по каталогу, без учёта страны и канала."
    )
    trend_ = next(t for t in out.tips if t.kind == "trend")
    assert trend_.lines and trend_.server_id is None
    assert out.no_catalog == []
    failed = build_tips(RU, accounts_(), history, RATES, "RUB", Catalogs(failed=(2,)))
    assert failed.no_catalog == ["Aeza"]
    english = build_tips(EN, accounts_(), history, RATES, "RUB", catalogs_())
    assert {t.title for t in english.tips} >= {"A smaller plan will do", "Stopped but paid for"}


def test_build_tips_without_history() -> None:
    out = build_tips(RU, accounts_(), None, None, "RUB", Catalogs())
    # No load and no money history: what the servers and the forecast tell.
    assert [t.kind for t in out.tips] == ["renewal", "shortfall", "no_data"]


async def test_tips_endpoint(history: HistoryRepository, conn: sqlite3.Connection) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from hostwatch.web.browser import local_launcher
    from hostwatch.web.login import LoginEvents, LoginService
    from hostwatch.web.miniapp import INIT_DATA_HEADER, MiniApp
    from hostwatch.web.server import make_app
    from tests.test_miniapp import OWNER, TOKEN, init_data

    async def rates() -> Rates | None:
        return RATES

    async def catalogs(accounts: list[Any]) -> Catalogs:
        return catalogs_()

    source = WebData(
        accounts=accounts_,
        history=history,
        journal_repo=JournalRepository(conn),
        rates=rates,
        view=lambda: RU,
        currency=lambda: "RUB",
        thresholds=Thresholds,
        catalogs=catalogs,
    )
    app = make_app(
        LoginService(local_launcher, LoginEvents()),
        secure=False,
        miniapp=MiniApp(TOKEN, OWNER, source),
    )
    async with TestClient(TestServer(app)) as client:
        refused = await client.get("/api/tips")
        assert refused.status == 401
        headers = {INIT_DATA_HEADER: init_data()}
        bad = await client.get("/api/tips?x=1", headers=headers)
        assert bad.status == 400
        answer = await client.get("/api/tips", headers=headers)
        assert answer.status == 200
        body = await answer.json()
    assert body["lang"] == "ru" and body["tips"][0]["kind"] == "trend"
    assert {"title", "why", "offer", "note", "lines", "amount"} <= set(body["tips"][0])


def test_hourly_plans_compare_by_a_month_of_hours() -> None:
    hourly = Plan(
        id="h",
        name="Cloud 2",
        location_id="msk",
        specs="2 CPU · 4 GB RAM · 50 GB",
        prices=(Price(period_months=HOURLY, amount=rub("400")),),
    )
    catalog = Catalog(locations=(Location(id="msk", name="Москва"),), plans=(hourly,), systems=())
    tip = cheaper_elsewhere(
        facts(plugin="aeza"), [Offers(1, "timeweb", "Timeweb", catalog)], convert, "RUB"
    )
    assert tip is not None and tip.amount == rub("290")
