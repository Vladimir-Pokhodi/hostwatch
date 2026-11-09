"""The Mini App tabs (1.1, part 5, stage 6): servers, a server's page and load, money and
the journal, through the API with the bot's data behind it."""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hostwatch.bot.views import View, journal_action, server_card
from hostwatch.bot.webdata import JOURNAL_GROUPS, WebData, build_money, build_usage
from hostwatch.core.alerts import Thresholds
from hostwatch.core.history import (
    MoneyKind,
    MoneyMove,
    MoneySource,
    ServerChange,
    ServerEventKind,
)
from hostwatch.core.models import Balance, Money, ServerStatus, Usage
from hostwatch.core.rates import Rates
from hostwatch.storage.history import STARTED_KEY, HistoryRepository
from hostwatch.storage.repositories import (
    AccountRepository,
    JournalEntry,
    JournalRepository,
    SettingsRepository,
)
from hostwatch.web.browser import local_launcher
from hostwatch.web.login import LoginEvents, LoginService
from hostwatch.web.miniapp import INIT_DATA_HEADER, MiniApp
from hostwatch.web.server import make_app
from tests.test_miniapp import OWNER, TOKEN, init_data
from tests.test_views import EN, NOW, RATES, RU, data, server

STARTED = NOW - timedelta(days=40)
Client = TestClient[object, object]


@dataclasses.dataclass
class World:
    conn: sqlite3.Connection
    history: HistoryRepository
    journal: JournalRepository
    accounts: list[Any]
    rates: Rates | None = RATES

    def source(self) -> WebData:
        async def rates() -> Rates | None:
            return self.rates

        return WebData(
            accounts=lambda: self.accounts,
            history=self.history,
            journal_repo=self.journal,
            rates=rates,
            view=lambda: RU,
            currency=lambda: "RUB",
            thresholds=Thresholds,
        )


def charge(
    h: HistoryRepository,
    acc: int,
    at: datetime,
    amount: str,
    currency: str = "RUB",
    source: MoneySource = MoneySource.OBSERVED,
    sid: str | None = None,
    kind: MoneyKind = MoneyKind.CHARGE,
) -> None:
    ext = f"{acc}-{at.isoformat()}-{amount}" if source is MoneySource.HOSTER else None
    move = MoneyMove(at, kind, Money.of(amount, currency), source, sid, ext, "renewal")
    h.add_money(acc, [move])


@pytest.fixture
def world(conn: sqlite3.Connection, accounts: AccountRepository) -> World:
    main = accounts.add("fake", "main", {})
    eu = accounts.add("fake", "eu", {})
    conn.execute("UPDATE settings SET value = ? WHERE key = ?", (STARTED.isoformat(), STARTED_KEY))
    conn.commit()
    history = HistoryRepository(conn)
    hot = Usage(server_id="1", measured_at=NOW, cpu_percent=97.0, ram_percent=40.0)
    first = data(
        [server("1", "bear"), server("2", "fox", price=Money.of("310", "RUB"))],
        acc=main,
        usage={"1": hot},
    )
    second = data(
        [
            server(
                "9",
                "beaver",
                price=Money.of("9.40", "EUR"),
                status=ServerStatus.STOPPED,
                next_charge_at=NOW + timedelta(days=2),
            )
        ],
        acc=eu,
        label="eu",
        stopped=frozenset({"9"}),
    )
    second = dataclasses.replace(second, balance=Balance(money=Money.of("41.20", "EUR")))
    # Observed: what left the balances. Hoster: the same money, tied to servers.
    charge(history, main, NOW - timedelta(days=2), "690")
    charge(history, main, NOW - timedelta(days=1), "310")
    charge(history, main, NOW - timedelta(days=35), "1000")
    charge(history, main, NOW - timedelta(days=3), "5000", kind=MoneyKind.TOPUP)
    charge(history, eu, NOW - timedelta(days=1), "9.40", "EUR")
    charge(history, eu, NOW - timedelta(days=1), "9.40", "EUR", MoneySource.HOSTER, "9")
    for minutes in range(0, 24 * 60, 15):
        at = NOW - timedelta(minutes=minutes)
        if timedelta(hours=5) < NOW - at < timedelta(hours=8):
            continue  # the bot was down: a gap
        history.add_samples(
            main,
            [
                Usage(
                    server_id="1", measured_at=at, cpu_percent=30.0 + minutes % 7, ram_percent=60.0
                )
            ],
        )
    for hours in range(48, 24 * 20):
        at = NOW - timedelta(hours=hours)
        history.add_samples(main, [Usage(server_id="1", measured_at=at, cpu_percent=20.0)])
    history.add_server_events(
        main,
        [ServerChange("1", ServerEventKind.PRICE, "590 RUB", "690 RUB")],
        NOW - timedelta(days=10),
    )
    journal = JournalRepository(conn)
    SettingsRepository(conn).set("lang", "en")
    journal.note("owner", "restart", main, {"server": "1", "action": "restart"})
    journal.note(
        "autopay",
        "pay_invoice",
        eu,
        {"server": "9", "invoice": "77", "amount": "9.40", "currency": "EUR"},
    )
    return World(conn, history, journal, [first, second])


@pytest.fixture
async def client(world: World) -> AsyncIterator[Client]:
    service = LoginService(local_launcher, LoginEvents())
    app = make_app(service, secure=False, miniapp=MiniApp(TOKEN, OWNER, world.source()))
    test_client: Client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await service.close()
    await test_client.close()


def owner() -> dict[str, str]:
    return {INIT_DATA_HEADER: init_data(auth_date=datetime.now(UTC))}


async def get(client: Client, path: str, status: int = 200) -> Any:
    response = await client.get(path, headers=owner())
    assert response.status == status, await response.text()
    assert response.headers["Cache-Control"] == "no-store"
    return await response.json()


PATHS = [
    "/api/overview",
    "/api/servers",
    "/api/servers/1/1",
    "/api/servers/1/1/usage?range=7d",
    "/api/money",
    "/api/money/events",
    "/api/journal",
]


@pytest.mark.parametrize("path", PATHS)
async def test_every_endpoint_needs_the_owner(client: Client, path: str) -> None:
    assert (await client.get(path)).status == 401
    stranger = {INIT_DATA_HEADER: init_data(user_id=OWNER + 1, auth_date=datetime.now(UTC))}
    assert (await client.get(path, headers=stranger)).status == 401
    assert (await client.get(path, headers=owner())).status == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/servers?x=1",
        "/api/servers/1/1/usage?range=2d",
        "/api/servers/1/1/usage?range=7d&range=24h",
        "/api/money?months=0",
        "/api/money?months=37",
        "/api/money?months=abc",
        "/api/money/events?limit=101",
        "/api/money/events?kind=gift",
        "/api/money/events?source=both",
        "/api/money/events?offset=-1",
        "/api/journal?actor=root",
        "/api/journal?group=x",
        "/api/journal?since=2026-13-01",
        "/api/journal?since=2026-11-09&until=2026-11-01",
        "/api/journal?account=0",
    ],
)
async def test_bad_parameters_are_400(client: Client, path: str) -> None:
    body = await get(client, path, 400)
    assert body["error"] == "bad request"


@pytest.mark.parametrize(
    "path",
    [
        "/api/servers/1/nope",
        "/api/servers/99/1",
        "/api/servers/x/1",
        "/api/servers/1/nope/usage",
        "/api/servers/1/1/other",
        f"/api/servers/1/{'a' * 201}",
    ],
)
async def test_unknown_servers_are_404(client: Client, path: str) -> None:
    assert (await get(client, path, 404)) == {"error": "not found"}


async def test_servers_list(client: Client) -> None:
    body = await get(client, "/api/servers")
    assert body["tz"] == "Europe/Moscow"
    assert [h["title"] for h in body["hosters"]] == ["Fake hoster (main)", "Fake hoster (eu)"]
    by_name = {s["name"]: s for s in body["servers"]}
    assert set(by_name) == {"bear", "fox", "beaver"}
    bear = by_name["bear"]
    assert bear["high_load"] and bear["level"] == "warn" and bear["cpu"] == 97.0
    assert bear["problems"] == []
    assert body["limits"] == {"load": 90, "disk": 90, "days": 5}
    assert bear["monthly"] == {"amount": "690.00", "currency": "RUB"}
    assert bear["days_to_charge"] == 6
    beaver = by_name["beaver"]
    # Stopped by the owner: shown as off, not as a problem.
    assert beaver["level"] == "off" and beaver["problems"] == []
    assert beaver["monthly_converted"] == {"amount": "940.00", "currency": "RUB"}
    assert by_name["fox"]["level"] == "ok"


async def test_server_page(client: Client) -> None:
    body = await get(client, "/api/servers/1/1")
    assert body["server"]["name"] == "bear"
    assert body["events"] == [
        {
            "at": (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
            "kind": "price",
            "old": "590 RUB",
            "new": "690 RUB",
        }
    ]
    cost = body["cost"]
    # No hoster records for it: estimated from its price since first seen.
    assert cost["basis"] == "estimate"
    # It was there when the history began on 30.09.
    assert [m["month"] for m in cost["months"]] == ["2026-09", "2026-10", "2026-11"]
    assert cost["since"].startswith(STARTED.date().isoformat())
    assert Decimal(cost["total"]["total"]["amount"]) > Decimal("690") * Decimal("0.5")
    assert body["collecting_since"].startswith(STARTED.date().isoformat())


async def test_server_cost_from_when_it_appeared(client: Client, world: World) -> None:
    born = NOW - timedelta(days=4)
    world.history.add_server_events(
        1, [ServerChange("2", ServerEventKind.APPEARED, None, "fox")], born
    )
    cost = (await get(client, "/api/servers/1/2"))["cost"]
    assert cost["basis"] == "estimate"
    assert [m["month"] for m in cost["months"]] == ["2026-11"]
    # 310 ₽ a month, 4 days of 30.
    assert Decimal(cost["total"]["total"]["amount"]) == Decimal("41.33")


async def test_server_cost_from_the_hoster(client: Client) -> None:
    cost = (await get(client, "/api/servers/2/9"))["cost"]
    assert cost["basis"] == "hoster"
    assert cost["months"] == [
        {
            "month": "2026-11",
            "amount": {
                "total": {"amount": "940.00", "currency": "RUB"},
                "parts": [{"amount": "9.40", "currency": "EUR"}],
            },
        }
    ]


async def test_usage_ranges(client: Client) -> None:
    day = await get(client, "/api/servers/1/1/usage?range=24h")
    assert day["resolution"] == "samples"
    assert set(day["series"]) == {"cpu", "ram", "disk", "net_in", "net_out"}
    assert day["series"]["cpu"]["max"] is None
    cpu = day["series"]["cpu"]["avg"]
    assert len(cpu) == len(day["t"]) and day["t"] == sorted(day["t"])
    # One empty point where the bot was down, so the line breaks there.
    assert cpu.count(None) == 1
    week = await get(client, "/api/servers/1/1/usage?range=7d")
    assert week["resolution"] == "hourly"
    assert week["series"]["cpu"]["max"] is not None
    assert len(week["t"]) <= 7 * 24 + 10
    year = await get(client, "/api/servers/1/1/usage?range=1y")
    assert year["resolution"] == "daily"
    assert 19 <= len([v for v in year["series"]["cpu"]["avg"] if v is not None]) <= 21
    assert len(year["t"]) <= 400


def test_usage_without_data(world: World) -> None:
    usage = build_usage(RU, world.history, world.accounts, 1, "2", "30d")
    assert usage is not None
    assert usage.t == [] and usage.series["cpu"].avg == []
    assert usage.collecting_since == STARTED


async def test_money_counts_observed_only(client: Client) -> None:
    body = await get(client, "/api/money?months=12")
    months = body["months"]
    assert len(months) == 12 and months[-1]["month"] == "2026-11"
    nov, octo = months[-1], months[-2]
    assert nov["partial"] and not nov["no_data"]
    # The history started on 30.09: September is partial, August has no data.
    assert months[-3]["month"] == "2026-09" and months[-3]["partial"]
    assert months[-4]["no_data"]
    # 690 + 310 ₽ + 9.40 € (940 ₽). The hoster's copy of the 9.40 € is not added again.
    assert Decimal(nov["total"]["total"]["amount"]) == Decimal("1940")
    assert Decimal(octo["total"]["total"]["amount"]) == Decimal("1000")
    assert Decimal(body["charge"]["total"]["amount"]) == Decimal("2940")
    assert Decimal(body["topup"]["total"]["amount"]) == Decimal("5000")
    main, eu = body["hosters"]
    assert Decimal(main["charge"]["total"]["amount"]) == Decimal("2000")
    # No hoster records at «main»: split by tariff prices 690 : 310, marked an estimate.
    split = {s["name"]: (s["charge"]["total"]["amount"], s["estimate"]) for s in main["servers"]}
    assert split == {"bear": ("1380.00", True), "fox": ("620.00", True)}
    assert eu["servers"] == [
        {
            "server_id": "9",
            "name": "beaver",
            "charge": {
                "total": {"amount": "940.00", "currency": "RUB"},
                "parts": [{"amount": "9.40", "currency": "EUR"}],
            },
            "estimate": False,
        }
    ]
    assert eu["unassigned"]["parts"] == []


def test_money_without_rates_keeps_currencies(world: World) -> None:
    result = build_money(RU, world.accounts, world.history, None, "RUB", 2)
    assert result.charge.total is None
    assert {p.currency for p in result.charge.parts} == {"RUB", "EUR"}
    assert result.rates_date is None


async def test_money_events_filters_and_pages(client: Client) -> None:
    body = await get(client, "/api/money/events")
    # Observed by default: the hoster's copy is not listed twice.
    assert [e["source"] for e in body["events"]] == ["observed"] * 5
    assert not body["has_more"]
    charges = await get(client, "/api/money/events?kind=charge&account=1&limit=2")
    assert [e["amount"]["amount"] for e in charges["events"]] == ["310", "690"]
    assert charges["has_more"]
    rest = await get(client, "/api/money/events?kind=charge&account=1&limit=2&offset=2")
    assert [e["amount"]["amount"] for e in rest["events"]] == ["1000"]
    hoster = await get(client, "/api/money/events?source=hoster")
    [only] = hoster["events"]
    assert only["server"] == "beaver" and only["converted"]["amount"] == "940.00"


async def test_journal_filters_and_labels(client: Client) -> None:
    body = await get(client, "/api/journal")
    texts = [(e["actor"], e["group"], e["text"]) for e in body["entries"]]
    assert ("autopay", "money", "оплата счёта · beaver #77 €9.40") in texts
    assert ("owner", "servers", "перезагрузка · bear") in texts
    assert ("owner", "settings", "настройка «язык»: — → English") in texts
    assert ("owner", "accounts", "подключение хостера") in texts
    autopay = await get(client, "/api/journal?actor=autopay")
    assert [e["action"] for e in autopay["entries"]] == ["pay_invoice"]
    settings = await get(client, "/api/journal?group=settings")
    assert {e["action"] for e in settings["entries"]} == {"settings"}
    one = await get(client, "/api/journal?limit=1")
    assert len(one["entries"]) == 1 and one["has_more"]
    nothing = await get(client, "/api/journal?until=2020-01-01")
    assert nothing == {"entries": [], "has_more": False}
    today = datetime.now(RU.tz).date().isoformat()
    dated = await get(client, f"/api/journal?since={today}&until={today}&account=2")
    assert [e["hoster"] for e in dated["entries"]] == ["Fake hoster (eu)"] * len(dated["entries"])
    assert dated["entries"]


SAMPLE_PARAMS: dict[str, dict[str, str]] = {
    "account_status": {"old": "ok", "new": "relogin"},
    "settings": {"key": "threshold.load_percent", "old": "90", "new": "85"},
    "topup_paid": {"amount": "500", "currency": "RUB", "invoice": "1"},
    "topup_result": {"amount": "500", "currency": "RUB", "invoice": "1"},
    "autopay_mark": {"server": "1", "on": "yes"},
    "autopay_hold": {"server": "1", "invoice": "5", "reason": "ask_grown", "amount": "9"},
    "autopay_answer": {"invoice": "5", "answer": "approved"},
    "disconnect": {"plugin": "aeza", "label": ""},
}


@pytest.mark.parametrize("view", [RU, EN])
@pytest.mark.parametrize("action", sorted(JOURNAL_GROUPS))
def test_every_journal_action_has_a_label(view: View, action: str) -> None:
    entry = JournalEntry(NOW, "owner", 1, action, SAMPLE_PARAMS.get(action, {}), "arrived", True)
    text = journal_action(view, entry, {(1, "1"): "bear"})
    # Never the raw name: «autopay_hold», or «restart» in Russian.
    assert text and "_" not in text and "{" not in text
    assert view is EN or not text.startswith(action)


def test_history_labels_say_what_happened() -> None:
    def say(action: str, params: dict[str, str], response: str = "") -> str:
        return journal_action(EN, JournalEntry(NOW, "owner", 1, action, params, response, True))

    assert say("account_status", {"old": "relogin", "new": "ok"}) == "working again"
    assert say("settings", {"key": "order.ssh_key", "old": "", "new": "set"}) == (
        "setting «SSH key for orders»: — → set"
    )
    assert say("settings", {"key": "autopay.limit.3", "old": "", "new": "500"}) == (
        "setting «autopay limit»: — → 500"
    )
    assert say("topup_result", {"amount": "5", "currency": "EUR"}, "not_seen") == (
        "top-up not seen €5.00"
    )
    assert say("autopay_answer", {"invoice": "5", "answer": "declined"}) == "autopay declined #5"


def test_server_card_opens_the_charts() -> None:
    d = data([server("a/1", "bear")])
    card = server_card(RU, d, d.servers[0], None, app_url="https://hostwatch.ez0.ru/app/")
    assert card.markup is not None
    urls = [b.web_app.url for row in card.markup.inline_keyboard for b in row if b.web_app]
    assert urls == ["https://hostwatch.ez0.ru/app/?server=1:a%2F1"]
    plain = server_card(RU, d, d.servers[0], None)
    assert plain.markup is not None
    assert not [b for row in plain.markup.inline_keyboard for b in row if b.web_app]


def test_history_started_is_the_mark_or_older(conn: sqlite3.Connection) -> None:
    history = HistoryRepository(conn)
    mark = history.started()
    assert mark is not None and abs(datetime.now(UTC) - mark) < timedelta(minutes=5)
    older = mark - timedelta(days=3)
    conn.execute("INSERT INTO accounts (plugin_id, label, secrets) VALUES ('fake', '', x'00')")
    charge(history, 1, older, "5")
    assert history.started() == older.replace(microsecond=0)
