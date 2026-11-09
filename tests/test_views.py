"""The screens of the chat (plan v1.1, part 2) from data alone: what the owner reads first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.bot import topup
from hostwatch.bot.callbacks import AccountCb, Nav, ServerCb, TopupCb, note
from hostwatch.bot.hub import AccountData
from hostwatch.bot.views import (
    HOME_PROBLEMS,
    View,
    alert_buttons,
    approx,
    balance,
    host_servers,
    integrations,
    menu,
    pairs,
    plural,
    problems,
    problems_screen,
    server_card,
    servers,
    settings,
    summary_markup,
)
from hostwatch.core.alerts import Thresholds
from hostwatch.core.forecast import forecast
from hostwatch.core.models import (
    Account,
    AccountStatus,
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Payment,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    TopupKind,
    TopupMethod,
    Usage,
)
from hostwatch.core.rates import Rates
from hostwatch.plugins.fake import FakePlugin
from hostwatch.plugins.timeweb import TimewebPlugin

# 12:00 in Moscow.
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
MSK = __import__("zoneinfo").ZoneInfo("Europe/Moscow")
RU = View("ru", MSK, NOW)
EN = View("en", MSK, NOW)
SOON = NOW + timedelta(days=2)
RATES = Rates(NOW.date(), {"EUR": Decimal("100")})


def server(sid: str = "1", name: str = "bear", **changes: Any) -> Server:
    base = Server(
        external_id=sid,
        name=name,
        kind=ServerKind.VPS,
        ip="192.0.2.1",
        status=ServerStatus.RUNNING,
        raw_status="active",
        price=Money.of("690", "RUB"),
        period_months=1,
        next_charge_at=NOW + timedelta(days=6),
        autorenew=Autorenew.ON,
        billing=BillingModel.PERIODIC,
        actions=frozenset(ServerAction),
    )
    return base.model_copy(update=changes)


def data(
    servers_: list[Server],
    money: str | None = "1000",
    acc: int = 1,
    status: AccountStatus = AccountStatus.OK,
    usage: dict[str, Usage] | None = None,
    stopped: frozenset[str] = frozenset(),
    plugin: Any = FakePlugin,
    label: str = "main",
) -> AccountData:
    account = Account(acc, plugin.meta.id, label, status, None, NOW)
    bal = Balance(money=Money.of(money, "RUB")) if money is not None else None
    fc = forecast(bal, servers_, NOW) if bal is not None else None
    return AccountData(
        account,
        plugin,
        servers_,
        NOW,
        bal,
        NOW if bal else None,
        fc,
        usage=usage or {},
        owner_stopped=stopped,
    )


def lines(text: str) -> list[str]:
    return text.split("\n")


def rows(screen: Any) -> list[list[str]]:
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


# Words and numbers


def test_plural_forms() -> None:
    forms = ("сервер", "сервера", "серверов")
    got = [plural("ru", n, forms) for n in (1, 2, 5, 11, 12, 21, 22, 25, 111)]
    assert got == [
        "сервер",
        "сервера",
        "серверов",
        "серверов",
        "серверов",
        "сервер",
        "сервера",
        "серверов",
        "серверов",
    ]
    assert RU.count(13, "server") == "13 серверов"
    assert EN.count(1, "server") == "1 server" and EN.count(4, "hoster") == "4 hosters"


def test_relative_dates_and_rounded_sums() -> None:
    assert RU.when(NOW + timedelta(hours=3)) == "сегодня 15:00"
    assert RU.when(NOW + timedelta(days=1)) == "завтра 12:00"
    assert RU.when(NOW + timedelta(days=3)) == "12.11 12:00"
    assert RU.when(NOW + timedelta(days=3), exact=False) == "12.11"
    assert RU.in_days(NOW + timedelta(days=3)) == "через 3 дн."
    assert EN.days_short(NOW + timedelta(days=3)) == "3 d."
    assert approx(Money.of("12480.40", "RUB")) == "≈ 12 480 ₽"
    assert approx(Money.of("41.5", "EUR")) == "≈ €42"


def test_pairs_put_two_short_buttons_a_row() -> None:
    kb = InlineKeyboardBuilder()
    short = [InlineKeyboardButton(text=f"b{n}", callback_data=str(n)) for n in range(3)]
    long = InlineKeyboardButton(text="a much longer label here", callback_data="x")
    pairs(kb, [short[0], long, short[1], short[2]])
    assert [[b.text for b in row] for row in kb.as_markup().inline_keyboard] == [
        ["b0"],
        ["a much longer label here"],
        ["b1", "b2"],
    ]


# Home and problems


def test_home_all_good() -> None:
    screen = menu(RU, [data([server(), server("2", "fox")])], RATES, "RUB")
    text = screen.text
    assert lines(text)[0] == "<b>🟢 Всё в порядке · 1 хостер · 2 сервера</b>"
    assert "💰 На балансах: ≈ 1 000 ₽" in text
    assert "💳 В месяц: ≈ 1 380 ₽" in text
    assert text.count("<b>") == 1
    assert rows(screen) == [
        ["🖥 Серверы", "💰 Деньги"],
        ["🔌 Хостеры", "⚙️ Настройки"],
    ]


def test_home_shows_the_worst_problems_first_and_counts_the_rest() -> None:
    usage = {"2": Usage(server_id="2", measured_at=NOW, ram_percent=93)}
    many = [
        server("1", "wolf", status=ServerStatus.STOPPED),
        server("2", "fox"),
        *(
            server(str(n), f"s{n}", autorenew=Autorenew.OFF, next_charge_at=SOON)
            for n in range(3, 7)
        ),
    ]
    screen = menu(RU, [data(many, money="100000", usage=usage)], RATES, "RUB")
    text = lines(screen.text)
    assert text[0] == "<b>🔴 6 проблем · 1 хостер · 6 серверов</b>"
    assert text[1] == "🔴 Fake hoster (main) · wolf — остановлен"
    assert len([line for line in text if line.startswith(("🔴", "⚠️"))]) == HOME_PROBLEMS
    assert "…и ещё 2" in text
    assert rows(screen)[0] == ["⚠️ Проблемы (6)"]
    assert len(text) <= 10


def test_problems_lead_where_they_are_solved() -> None:
    usage = {"2": Usage(server_id="2", measured_at=NOW, cpu_percent=95, disk_percent=97)}
    low = data(
        [server(next_charge_at=SOON), server("2", "fox", next_charge_at=SOON)],
        money="10",
        usage=usage,
    )
    gone = data([], money=None, acc=2, status=AccountStatus.RELOGIN, label="old")
    found = problems(RU, [low, gone], Thresholds())
    texts = [p.line() for p in found]
    assert "🔴 Fake hoster (old) — нужен вход" in texts
    assert "⚠️ Fake hoster (main) · fox — CPU 95%  Диск 97%" in texts
    money = next(p for p in found if "деньги" in p.text)
    assert money.target == TopupCb(acc=1)
    screen = problems_screen(RU, [low, gone])
    buttons = {b.callback_data: b.text for row in screen.markup.inline_keyboard for b in row}
    assert buttons[AccountCb(acc=2, op="card").pack()] == "🔌 Fake hoster (old)"
    assert buttons[ServerCb(acc=1, sid="2").pack()] == "🖥 fox"
    assert buttons[TopupCb(acc=1).pack()] == "💰 Пополнить Fake hoster (main)"
    assert rows(screen)[-1] == ["🏠 Главная"]


def test_a_server_the_owner_stopped_is_no_problem() -> None:
    stopped = server(status=ServerStatus.STOPPED)
    assert problems(RU, [data([stopped], stopped=frozenset({"1"}))]) == []
    assert lines(menu(RU, [data([stopped], stopped=frozenset({"1"}))]).text)[0].startswith("<b>🟢")


def test_summary_buttons_open_a_new_menu() -> None:
    markup = summary_markup(RU, has_problems=True)
    assert [(b.text, b.callback_data) for b in markup.inline_keyboard[0]] == [
        ("🏠 Открыть меню", note(Nav(to="menu"))),
        ("⚠️ Проблемы", note(Nav(to="problems"))),
    ]
    assert len(alert_buttons(EN, [], {}, {}).inline_keyboard[0]) == 1


# Servers


def test_servers_screen_and_a_hoster_list() -> None:
    accounts = [data([server(), server("2", "fox", status=ServerStatus.STOPPED)])]
    screen = servers(RU, accounts, can_order=True)
    assert lines(screen.text)[0] == "<b>🖥 2 сервера · 🔴 1 проблема</b>"
    assert rows(screen) == [
        ["🔴 Fake hoster (main) · 2"],
        ["🛒 Заказать", "🔃 Обновить"],
        ["🏠 Главная"],
    ]

    listing = host_servers(RU, accounts[0], 0)
    assert lines(listing.text) == [
        "<b>🔴 Fake hoster (main) · 2 сервера</b>",
        "🔴 fox — остановлен",
    ]
    assert rows(listing)[:2] == [["🟢 bear · 6 дн."], ["🔴 fox · 6 дн."]]
    assert rows(listing)[-1] == ["‹ Назад", "🏠 Главная"]


def test_server_card_reads_in_a_glance() -> None:
    usage = Usage(server_id="1", measured_at=NOW, cpu_percent=12, ram_percent=93, disk_percent=41)
    d = data([server(plan="Cloud 30")], usage={"1": usage})
    card = server_card(RU, d, d.servers[0], usage)
    assert lines(card.text) == [
        "<b>🟡 bear · Fake hoster (main)</b>",
        "IP <code>192.0.2.1</code> · VPS · Cloud 30",
        "CPU 12%  RAM 93% ⚠️  Диск 41%",
        "💳 690.00 ₽/мес · списание 15.11 (через 6 дн.)",
        "🔁 Автопродление: вкл",
        "🕓 Данные от 09.11 12:00",
    ]
    board = rows(card)
    assert board[0] == ["🔄 Перезагрузить", "⏻ Выключить"]
    assert all(len(row) <= 2 for row in board)
    assert board[-1] == ["‹ Назад", "🏠 Главная"]


def test_hourly_server_card_shows_the_month_estimate() -> None:
    hourly = server(
        price=Money.of("0.95", "RUB"),
        period_months=None,
        next_charge_at=None,
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.HOURLY,
    )
    d = data([hourly], plugin=TimewebPlugin, label="")
    text = server_card(EN, d, hourly, None).text
    assert "💳 0.95 ₽/h · ≈ 694 ₽/mo" in text
    assert "Autorenewal" not in text


# Money and hosters


def test_money_is_a_line_per_hoster() -> None:
    rich = data([server()], money="5000")
    tonight = NOW + timedelta(hours=9)
    poor = data([server("2", "fox", next_charge_at=tonight)], money="10", acc=2, label="poor")
    screen = balance(RU, [rich, poor], RATES, "RUB")
    text = lines(screen.text)
    assert text[0] == "<b>💰 Деньги · 🔴 нужно внимание</b>"
    assert text[1].startswith("🟢 Fake hoster (main) · 5 000.00 ₽ · до ")
    assert text[2] == "🔴 Fake hoster (poor) · 10.00 ₽ · до сегодня 21:00"
    assert "💰 На балансах: ≈ 5 010 ₽" in text
    assert rows(screen)[-2] == ["ℹ️ Как считаю", "🔃 Обновить"]


def test_hosters_screen_names_what_can_be_connected() -> None:
    from hostwatch.plugins.aeza import AezaPlugin

    screen = integrations(EN, [data([server()])], [AezaPlugin, FakePlugin])
    text = lines(screen.text)
    assert text == [
        "<b>🔌 1 hoster · 🟢 all connected</b>",
        "🟢 Fake hoster (main) · 1 server",
        "＋ Can connect: Aeza",
    ]
    assert rows(screen) == [["🟢 Fake hoster (main)"], ["＋ Connect"], ["🏠 Home"]]


def test_settings_fit_in_four_lines() -> None:
    values = {
        "balance_days": "5",
        "load_percent": "90",
        "disk_percent": "90",
        "quiet_hours": "23:00-08:00",
        "summary_time": "09:00",
    }
    screen = settings(RU, "Europe/Moscow", "RUB", values)
    assert len(lines(screen.text)) == 4
    assert all(len(row) <= 2 for row in rows(screen))


# Top-up steps


def test_top_up_shows_its_step() -> None:
    d = data([server()])
    sbp = TopupMethod(id="sbp", name="СБП", kind=TopupKind.SBP, minimum=Money.of("100", "RUB"))
    choice = topup.Choice(d, [sbp])
    methods = topup.methods_screen(RU, choice)
    assert lines(methods.text)[:2] == [
        "<b>💰 Пополнение · Fake hoster (main)</b>",
        "Шаг 1 из 3 · способ",
    ]
    assert rows(methods)[0] == ["⚡ СБП"]
    amount = topup.amount_screen(RU, choice, sbp, None)
    assert "Шаг 2 из 3 · сумма" in amount.text
    assert "Способ: СБП · минимум 100.00 ₽" in amount.text
    payment = Payment(
        id="77",
        amount=Money.of("500", "RUB"),
        method="sbp",
        created_at=NOW,
        url="https://qr.nspk.ru/X",
    )
    invoice = topup.invoice_screen(RU, d.title, sbp, payment, 1)
    assert lines(invoice.text)[:3] == [
        "<b>💰 Счёт · Fake hoster (main)</b>",
        "Шаг 3 из 3 · оплата",
        "500.00 ₽ · СБП · №77",
    ]
    assert rows(invoice)[0] == ["💳 Оплатить", "✅ Оплатил"]
    assert len(lines(invoice.text)) <= 6
