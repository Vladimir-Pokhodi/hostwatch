"""The Mini App (1.1, part 5): initData checks, the API behind them, the overview data and
the «📊 Analytics» buttons."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from aiogram.methods import SetChatMenuButton
from aiogram.types import MenuButtonWebApp
from aiohttp.test_utils import TestClient, TestServer

from hostwatch.bot.callbacks import SetCb
from hostwatch.bot.views import menu
from hostwatch.bot.webapp import app_url, build_overview
from hostwatch.core.history import MoneyKind, MoneyMove, MoneySource
from hostwatch.core.models import AccountStatus, Money, ServerStatus, Usage
from hostwatch.core.webapp_auth import InvalidInitData, sign, validate
from hostwatch.storage.history import HistoryRepository
from hostwatch.storage.repositories import AccountRepository
from hostwatch.web.browser import local_launcher
from hostwatch.web.login import LoginEvents, LoginService
from hostwatch.web.miniapp import (
    FAILURES_PER_MINUTE,
    INIT_DATA_HEADER,
    MiniApp,
    Overview,
    SpendOut,
    Sum,
    TotalsOut,
)
from hostwatch.web.server import make_app
from tests.test_bot import Harness
from tests.test_views import EN, NOW, RATES, RU, data, server

TOKEN = "123456:TEST-token"
OWNER = 1001


def init_data(
    user_id: int = OWNER,
    auth_date: datetime = NOW,
    token: str = TOKEN,
    **extra: str,
) -> str:
    fields = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Vova", "language_code": "ru"}),
        "auth_date": str(int(auth_date.timestamp())),
        **extra,
    }
    fields["hash"] = sign(fields, token)
    return urlencode(fields)


# Validation


def test_valid_init_data() -> None:
    user = validate(init_data(), TOKEN, OWNER, now=NOW + timedelta(hours=1))
    assert user.id == OWNER
    assert user.language_code == "ru"
    assert user.auth_date == NOW


def test_signature_with_extra_fields() -> None:
    data = init_data(signature="abc", chat_type="sender")
    assert validate(data, TOKEN, OWNER, now=NOW).id == OWNER


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "hash=",
        "not a query string",
        "a=1&a=2",
        "x" * 5000,
    ],
)
def test_malformed(bad: str) -> None:
    with pytest.raises(InvalidInitData):
        validate(bad, TOKEN, OWNER, now=NOW)


def test_tampered_user_is_refused() -> None:
    data = init_data(user_id=OWNER + 1)
    forged = data.replace(f"%22id%22%3A+{OWNER + 1}", f"%22id%22%3A+{OWNER}")
    assert forged != data
    with pytest.raises(InvalidInitData, match="signature"):
        validate(forged, TOKEN, OWNER, now=NOW)


def test_tampered_date_is_refused() -> None:
    old = init_data(auth_date=NOW - timedelta(days=3))
    fresh = str(int(NOW.timestamp()))
    forged = old.replace(str(int((NOW - timedelta(days=3)).timestamp())), fresh)
    with pytest.raises(InvalidInitData, match="signature"):
        validate(forged, TOKEN, OWNER, now=NOW)


def test_other_bot_token_is_refused() -> None:
    with pytest.raises(InvalidInitData, match="signature"):
        validate(init_data(token="999:OTHER"), TOKEN, OWNER, now=NOW)


def test_wrong_user_is_refused() -> None:
    with pytest.raises(InvalidInitData, match="owner"):
        validate(init_data(user_id=OWNER + 1), TOKEN, OWNER, now=NOW)


def test_stale_and_future_dates_are_refused() -> None:
    with pytest.raises(InvalidInitData, match="stale"):
        validate(init_data(), TOKEN, OWNER, now=NOW + timedelta(hours=25))
    with pytest.raises(InvalidInitData, match="stale"):
        validate(init_data(auth_date=NOW + timedelta(hours=1)), TOKEN, OWNER, now=NOW)


def test_signed_but_without_user_is_refused() -> None:
    fields = {"auth_date": str(int(NOW.timestamp()))}
    fields["hash"] = sign(fields, TOKEN)
    with pytest.raises(InvalidInitData):
        validate(urlencode(fields), TOKEN, OWNER, now=NOW)


# The API


def empty_overview() -> Overview:
    nothing = Sum(total=None, parts=[])
    return Overview(
        lang="ru",
        currency="RUB",
        generated_at=NOW,
        rates_date=None,
        hosters_count=0,
        servers_count=0,
        totals=TotalsOut(balances=nothing, monthly=nothing),
        spend=SpendOut(
            month="2026-11", previous_month="2026-10", this_month=nothing, last_month=nothing
        ),
        problems=[],
        hosters=[],
    )


@pytest.fixture
async def client() -> AsyncIterator[TestClient[object, object]]:
    async def overview() -> Overview:
        return empty_overview()

    service = LoginService(local_launcher, LoginEvents())
    app = make_app(service, secure=False, miniapp=MiniApp(TOKEN, OWNER, overview))
    test_client: TestClient[object, object] = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await service.close()
    await test_client.close()


def fresh() -> str:
    return init_data(auth_date=datetime.now(UTC))


async def test_api_without_init_data_is_401(client: TestClient[object, object]) -> None:
    response = await client.get("/api/overview")
    assert response.status == 401
    assert await response.json() == {"error": "unauthorized"}


async def test_api_with_wrong_user_is_401(client: TestClient[object, object]) -> None:
    data = init_data(user_id=OWNER + 1, auth_date=datetime.now(UTC))
    response = await client.get("/api/overview", headers={INIT_DATA_HEADER: data})
    assert response.status == 401


async def test_api_for_the_owner(client: TestClient[object, object]) -> None:
    response = await client.get("/api/overview", headers={INIT_DATA_HEADER: fresh()})
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = await response.json()
    assert body["lang"] == "ru"
    assert body["spend"]["month"] == "2026-11"


async def test_unknown_api_path_needs_init_data_too(client: TestClient[object, object]) -> None:
    assert (await client.get("/api/servers")).status == 401
    response = await client.get("/api/servers", headers={INIT_DATA_HEADER: fresh()})
    assert response.status == 404


async def test_many_bad_requests_get_429(client: TestClient[object, object]) -> None:
    for _ in range(FAILURES_PER_MINUTE):
        assert (await client.get("/api/overview")).status == 401
    response = await client.get("/api/overview", headers={INIT_DATA_HEADER: fresh()})
    assert response.status == 429


async def test_static_files(client: TestClient[object, object]) -> None:
    index = await client.get("/app/")
    assert index.status == 200
    assert "telegram-web-app.js" in await index.text()
    assert "frame-ancestors https://web.telegram.org" in index.headers["Content-Security-Policy"]
    script = await client.get("/app/app.js")
    assert script.status == 200
    assert script.content_type == "text/javascript"
    assert (await client.get("/app/vendor/uplot/uPlot.iife.min.js")).status == 200
    assert (await client.get("/app/vendor/uplot/LICENSE")).status == 200
    assert (await client.get("/app/../server.py")).status == 404
    assert (await client.get("/app/%2e%2e/server.py")).status == 404
    assert (await client.get("/app/nope.js")).status == 404
    moved = await client.get("/app", allow_redirects=False)
    assert moved.status == 301


async def test_no_mini_app_without_a_domain() -> None:
    service = LoginService(local_launcher, LoginEvents())
    test_client: TestClient[object, object] = TestClient(TestServer(make_app(service)))
    await test_client.start_server()
    try:
        assert (await test_client.get("/app/")).status == 404
        assert (await test_client.get("/api/overview")).status == 404
    finally:
        await service.close()
        await test_client.close()


# The overview data


def test_overview_counts_like_the_chat(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    acc = accounts.add("fake", "main", {})
    other = accounts.add("fake", "eu", {})
    history = HistoryRepository(conn)

    def charge(at: datetime, amount: str, currency: str = "RUB", account: int = acc) -> None:
        move = MoneyMove(at, MoneyKind.CHARGE, Money.of(amount, currency), MoneySource.OBSERVED)
        history.add_money(account, [move])

    charge(NOW - timedelta(days=2), "300")
    charge(NOW - timedelta(days=1), "10", "EUR", other)
    charge(NOW - timedelta(days=20), "500")
    history.add_money(
        acc,
        [MoneyMove(NOW, MoneyKind.TOPUP, Money.of("5000", "RUB"), MoneySource.OBSERVED)],
    )
    hot = Usage(server_id="1", measured_at=NOW, cpu_percent=97.0, ram_percent=40.0)
    main = data([server("1", "bear")], money="100", acc=acc, usage={"1": hot})
    eu = data(
        [server("2", "fox & co", status=ServerStatus.STOPPED)],
        money="2000",
        acc=other,
        label="eu",
    )
    off = data([], acc=99, status=AccountStatus.DISABLED, label="off")

    result = build_overview(RU, [main, eu, off], RATES, "RUB", history)

    assert result.lang == "ru"
    assert result.hosters_count == 2
    assert result.servers_count == 2
    assert [h.account_id for h in result.hosters] == [acc, other]
    assert result.totals.balances.total is not None
    assert result.totals.balances.total.amount == "2100.00"
    assert result.totals.monthly.total is not None
    assert Decimal(result.totals.monthly.total.amount) == Decimal("1380")
    # The stopped server first: the worst problem leads, as in the chat.
    assert result.problems[0].level == "bad"
    assert result.problems[0].server == "fox & co"
    assert any(p.what == "CPU 97%" and p.server == "bear" for p in result.problems)
    assert all("&amp;" not in p.what for p in result.problems)
    # November: 300 ₽ + 10 € at 100 ₽; October: 500 ₽. Top-ups are not spending.
    assert result.spend.month == "2026-11"
    assert result.spend.previous_month == "2026-10"
    assert result.spend.this_month.total is not None
    assert Decimal(result.spend.this_month.total.amount) == Decimal("1300")
    assert result.spend.last_month.total is not None
    assert Decimal(result.spend.last_month.total.amount) == Decimal("500")
    assert result.hosters[1].spend_month.parts[0].currency == "EUR"
    assert result.hosters[1].level == "bad"
    assert result.hosters[0].lasts


def test_overview_without_rates_keeps_currencies_apart() -> None:
    eur = data([server(price=Money.of("5", "EUR"))], money="1000")
    result = build_overview(EN, [eur], None, "RUB", None)
    assert result.totals.monthly.total is None
    assert [p.currency for p in result.totals.monthly.parts] == ["EUR"]
    assert result.totals.balances.total is not None
    assert result.spend.this_month.parts == []


# Buttons


def test_app_url_needs_a_domain() -> None:
    assert app_url(None) is None
    assert app_url("") is None
    assert app_url("hostwatch.ez0.ru") == "https://hostwatch.ez0.ru/app/"


def test_home_has_analytics_button_with_a_domain() -> None:
    accounts = [data([server()])]
    plain = menu(RU, accounts)
    assert all(b.web_app is None for row in plain.markup.inline_keyboard for b in row)  # type: ignore[union-attr]
    screen = menu(RU, accounts, app_url="https://hostwatch.ez0.ru/app/")
    buttons = [b for row in screen.markup.inline_keyboard for b in row]  # type: ignore[union-attr]
    web = [b for b in buttons if b.web_app is not None]
    assert len(web) == 1
    assert web[0].text == "📊 Аналитика"
    assert web[0].web_app is not None
    assert web[0].web_app.url == "https://hostwatch.ez0.ru/app/"
    english = menu(EN, accounts, app_url="https://x.test/app/")
    labels = [b.text for row in english.markup.inline_keyboard for b in row]  # type: ignore[union-attr]
    assert "📊 Analytics" in labels


async def test_language_change_relabels_the_menu_button(
    conn: sqlite3.Connection, accounts: AccountRepository
) -> None:
    h = Harness(conn, accounts)
    try:
        h.onboard("ru")
        h.hub.app_url = "https://hostwatch.ez0.ru/app/"
        await h.press(SetCb(key="lang", value="en"))
        calls = [c for c in h.session.calls if isinstance(c, SetChatMenuButton)]
        assert len(calls) == 1
        button = calls[0].menu_button
        assert isinstance(button, MenuButtonWebApp)
        assert button.text == "📊 Analytics"
        assert button.web_app.url == "https://hostwatch.ez0.ru/app/"
    finally:
        await h.poller.stop()
        await h.bot.session.close()
