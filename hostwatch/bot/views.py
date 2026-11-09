"""Texts and keyboards of the bot screens: pure functions of the data, so tests call them.

Every screen follows the same rules (plan v1.1, part 2): the first line is the state, with
an icon and words, and it alone is bold; then a few short lines, one thought each; long
explanations sit behind an «ℹ️» button. Icons: 🟢 fine, 🟡 warning, 🔴 problem or stopped,
⚠️ a problem line, 💰 money, 🖥 server, 🔌 hoster, ⚙️ settings. Buttons: short, at most
two a row (coins and pages aside), «‹ Back · 🏠 Home» at the bottom.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum
from html import escape
from urllib.parse import quote

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from hostwatch.core.alerts import Thresholds
from hostwatch.core.autopay import eligible as autopay_eligible
from hostwatch.core.errors import explain
from hostwatch.core.forecast import Method
from hostwatch.core.models import (
    AccountStatus,
    Autorenew,
    BillingModel,
    Money,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    Usage,
)
from hostwatch.core.plugin import ACTION_CAPABILITY, BrowserAuth, Capability, Plugin
from hostwatch.core.rates import Rates
from hostwatch.locales import t
from hostwatch.storage.repositories import JournalEntry

from .callbacks import (
    AccountCb,
    ActionCb,
    AddCb,
    HostCb,
    InvoicesCb,
    JournalCb,
    LabelCb,
    Nav,
    RefreshCb,
    ServerCb,
    SetCb,
    TopupCb,
    note,
)
from .hub import AccountData, key_fields
from .prefs import CURRENCIES, DEFAULT_TZ, LANGUAGES, THRESHOLDS, TIMEZONES

HOURS_PER_MONTH = Decimal(730)
BAD_STATUSES = (ServerStatus.STOPPED, ServerStatus.SUSPENDED, ServerStatus.UNPAID)
JOURNAL_PAGE = 10
SERVERS_PAGE = 8
# Problem lines on the home screen; all of them are on «⚠️ Problems».
HOME_PROBLEMS = 4
PROBLEMS_SHOWN = 12
# A button label longer than this gets a row of its own: two would be cut short.
PAIR_WIDTH = 18
LANGUAGE_NAMES = {"ru": "Русский", "en": "English"}
HOURLY_METHODS = (Method.HOSTER_HOURS, Method.HOURLY_RATE, Method.MIXED)
SCREEN_LIMIT = 4000


@dataclass(frozen=True)
class Screen:
    text: str
    markup: InlineKeyboardMarkup | None = None
    # A picture (a payment QR code): the menu becomes a photo with `text` as its caption.
    photo: bytes | None = None


@dataclass(frozen=True)
class View:
    """What every screen needs besides its data: language, time zone and the time now."""

    lang: str
    tz: tzinfo
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def t(self, key: str, **kwargs: object) -> str:
        return t(key, self.lang, **kwargs)

    def moment(self, value: datetime) -> str:
        return value.astimezone(self.tz).strftime("%d.%m %H:%M")

    def day(self, value: datetime) -> str:
        return value.astimezone(self.tz).strftime("%d.%m")

    def _days_from_today(self, value: datetime) -> int:
        return (value.astimezone(self.tz).date() - self.now.astimezone(self.tz).date()).days

    def when(self, value: datetime, exact: bool = True) -> str:
        """«today 15:11», «tomorrow 09:00», else «12.11» (with the time if `exact`)."""
        days = self._days_from_today(value)
        clock = value.astimezone(self.tz).strftime("%H:%M")
        if days == 0:
            return self.t("time.today", time=clock)
        if days == 1:
            return self.t("time.tomorrow", time=clock)
        return self.moment(value) if exact else self.day(value)

    def in_days(self, value: datetime) -> str:
        """«today», «tomorrow», «in 3 d.»."""
        days = self._days_from_today(value)
        if days <= 0:
            return self.t("time.today_word")
        if days == 1:
            return self.t("time.tomorrow_word")
        return self.t("time.in_days", n=days)

    def days_short(self, value: datetime) -> str:
        """«3 d.» on a button; «today» when it is due today or overdue."""
        days = self._days_from_today(value)
        return self.t("time.today_word") if days <= 0 else self.t("time.days", n=days)

    def count(self, n: int, noun: str) -> str:
        """«1 сервер», «3 сервера», «13 серверов»."""
        return f"{n} {plural(self.lang, n, self.t(f'plural.{noun}').split('|'))}"


def plural(lang: str, n: int, forms: Sequence[str]) -> str:
    if lang != "ru" or len(forms) < 3:
        return forms[0] if n == 1 else forms[-1]
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def approx(money: Money) -> str:
    """A rounded sum for totals and estimates: «≈ 12 480 ₽»."""
    whole = money.amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return "≈ " + str(Money(amount=whole, currency=money.currency)).replace(".00", "")


def _button(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def _nav(v: View, back: CallbackData | None = None) -> list[InlineKeyboardButton]:
    """«‹ Back» one level up and «🏠 Home». A section of the home screen gets «Home» alone:
    there both would lead to the same place."""
    home = _button(v.t("bot.button.home"), Nav(to="menu"))
    if back is None:
        return [home]
    return [_button(v.t("bot.button.back"), back), home]


def pairs(kb: InlineKeyboardBuilder, buttons: Sequence[InlineKeyboardButton]) -> None:
    """Buttons two a row; a long label gets a row of its own."""
    row: list[InlineKeyboardButton] = []
    for item in buttons:
        if len(item.text) > PAIR_WIDTH:
            if row:
                kb.row(*row)
                row = []
            kb.row(item)
            continue
        row.append(item)
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)


def _fit(lines: list[str]) -> str:
    """Join lines, dropping the last ones if Telegram would refuse the message."""
    text = "\n".join(lines)
    while len(text) > SCREEN_LIMIT and len(lines) > 1:
        lines = [*lines[:-2], "…"]
        text = "\n".join(lines)
    return text


# Errors and data age


def reason(v: View, error: str | None) -> str:
    """Account error from `describe()`, in the owner's language."""
    return explain(error, v.lang)


def _failed_line(v: View, data: AccountData) -> str:
    why = escape(reason(v, data.account.last_error))
    at = data.account.last_ok_at or data.servers_at
    if at is None:
        return v.t("account.failed_never", reason=why)
    return v.t("account.failed", reason=why, date=v.moment(at))


def _failed(data: AccountData) -> bool:
    return data.account.status in (AccountStatus.ERROR, AccountStatus.RELOGIN)


def _live(accounts: list[AccountData]) -> list[AccountData]:
    return [d for d in accounts if d.account.status is not AccountStatus.DISABLED]


# Problems: what the home screen, the lists and the cards point at


class Level(IntEnum):
    OK = 0
    WARN = 1
    BAD = 2


ICON = {Level.OK: "🟢", Level.WARN: "🟡", Level.BAD: "🔴"}
LINE_ICON = {Level.WARN: "⚠️", Level.BAD: "🔴"}


@dataclass(frozen=True)
class Problem:
    level: Level
    account_id: int
    # What is wrong, for a card of the server: «RAM 93%».
    what: str
    # The whole line without its icon: «Aeza · SEOHUB_BACK — RAM 93%».
    text: str
    button: str
    target: CallbackData
    server_id: str = ""
    # A load above the threshold: the card marks it in the load line instead.
    load: bool = False

    def line(self) -> str:
        return f"{LINE_ICON[self.level]} {self.text}"


def _topup_target(data: AccountData) -> CallbackData:
    if Capability.TOPUP in data.caps:
        return TopupCb(acc=data.account.id)
    return Nav(to="balance")


def _money_level(v: View, data: AccountData, th: Thresholds) -> Level:
    f = data.forecast
    if f is None or f.runs_out_at is None:
        return Level.OK
    left = f.runs_out_at - v.now
    if left < timedelta(days=1):
        return Level.BAD
    if left < timedelta(days=th.balance_days):
        return Level.WARN
    return Level.OK


def _renews(server: Server, data: AccountData) -> bool:
    return server.autorenew is Autorenew.ON or server.external_id in data.autopay


def _load_parts(v: View, usage: Usage | None, th: Thresholds) -> list[str]:
    if usage is None:
        return []
    parts = []
    for name, value, limit in (
        ("CPU", usage.cpu_percent, th.load_percent),
        ("RAM", usage.ram_percent, th.load_percent),
        (v.t("card.disk"), usage.disk_percent, th.disk_percent),
    ):
        if value is not None and value >= limit:
            parts.append(f"{name} {value:.0f}%")
    return parts


def problems(v: View, accounts: list[AccountData], th: Thresholds | None = None) -> list[Problem]:
    """Everything that needs the owner, the worst first."""
    th = th or Thresholds()
    found: list[Problem] = []
    for data in _live(accounts):
        acc, title = data.account.id, escape(data.title)
        card = AccountCb(acc=acc, op="card")
        hoster_button = v.t("problem.button.account", title=data.title)
        if data.account.status is AccountStatus.RELOGIN:
            what = v.t("problem.relogin")
            found.append(Problem(Level.BAD, acc, what, f"{title} — {what}", hoster_button, card))
        elif data.account.status is AccountStatus.ERROR:
            what = v.t("problem.no_data", reason=escape(reason(v, data.account.last_error)))
            found.append(Problem(Level.BAD, acc, what, f"{title} — {what}", hoster_button, card))
        money = _money_level(v, data, th)
        f = data.forecast
        topup_button = v.t("problem.button.topup", title=data.title)
        if money is not Level.OK and f is not None and f.runs_out_at is not None:
            exact = f.method in HOURLY_METHODS
            what = v.t("problem.money_until", date=v.when(f.runs_out_at, exact))
            found.append(
                Problem(money, acc, what, f"{title} — {what}", topup_button, _topup_target(data))
            )
        elif (
            f is not None
            and f.shortfall is not None
            and f.shortfall.at - v.now < timedelta(days=th.balance_days)
        ):
            what = v.t(
                "problem.shortfall",
                server=escape(f.shortfall.server),
                amount=f.shortfall.missing,
                date=v.when(f.shortfall.at, exact=False),
            )
            target = _topup_target(data)
            found.append(Problem(Level.WARN, acc, what, f"{title} — {what}", topup_button, target))
        for server in data.servers:
            found += _server_problems(v, data, server, th)
    return sorted(found, key=lambda p: -p.level)


def _server_problems(v: View, data: AccountData, server: Server, th: Thresholds) -> list[Problem]:
    acc, sid = data.account.id, server.external_id
    head = f"{escape(data.title)} · {escape(server.name)}"
    target = ServerCb(acc=acc, sid=sid)
    label = v.t("problem.button.server", server=server.name)
    found = []

    def add(level: Level, what: str, load: bool = False) -> None:
        found.append(Problem(level, acc, what, f"{head} — {what}", label, target, sid, load))

    owner_stop = server.status is ServerStatus.STOPPED and sid in data.owner_stopped
    if server.status in BAD_STATUSES and not owner_stop:
        add(Level.BAD, v.t(f"status.{server.status.value}"))
    ends = server.next_charge_at
    if (
        server.billing is BillingModel.PERIODIC
        and ends is not None
        and not _renews(server, data)
        and ends - v.now < timedelta(days=th.balance_days)
    ):
        add(Level.WARN, v.t("problem.ends", date=v.when(ends, exact=False)))
    load = _load_parts(v, data.usage.get(sid), th)
    if load:
        add(Level.WARN, "  ".join(load), load=True)
    return found


def _level_of(found: list[Problem]) -> Level:
    return max((p.level for p in found), default=Level.OK)


def _account_level(data: AccountData, found: list[Problem]) -> Level:
    return _level_of([p for p in found if p.account_id == data.account.id])


def _server_level(server: Server, found: list[Problem], acc: int) -> Level:
    mine = [p for p in found if p.account_id == acc and p.server_id == server.external_id]
    return _level_of(mine)


def _server_icon(server: Server, found: list[Problem], acc: int) -> str:
    if server.status in BAD_STATUSES:
        return "🔴"
    if server.status is ServerStatus.UNKNOWN:
        return "⚪"
    return ICON[_server_level(server, found, acc)]


# Money sums


def monthly_cost(servers: list[Server]) -> list[Money]:
    """What the servers cost a month, one sum per currency. Hourly prices count 730 hours."""
    totals: dict[str, Money] = {}
    for server in servers:
        month = _month_price(server)
        if month is None:
            continue
        current = totals.get(month.currency)
        totals[month.currency] = month if current is None else current + month
    return list(totals.values())


def _month_price(server: Server) -> Money | None:
    price = server.price
    if price is None:
        return None
    if server.billing is BillingModel.HOURLY:
        return price * HOURS_PER_MONTH
    if server.period_months:
        return Money(amount=price.amount / server.period_months, currency=price.currency)
    return None


def _total(amounts: list[Money], rates: Rates, currency: str) -> Money | None:
    total = Money(amount=Decimal(0), currency=currency)
    for amount in amounts:
        converted = rates.convert(amount, currency)
        if converted is None:
            return None
        total += converted
    return total


def _sum(amounts: list[Money], rates: Rates | None, currency: str, exact: bool) -> str:
    """One sum in the owner's currency when there are rates; else one per currency."""
    if not amounts:
        return "—"
    if rates is not None:
        total = _total(amounts, rates, currency)
        if total is not None:
            return approx(total)
    by_currency: dict[str, Money] = {}
    for amount in amounts:
        current = by_currency.get(amount.currency)
        by_currency[amount.currency] = amount if current is None else current + amount
    if exact:
        return " · ".join(str(m) for m in by_currency.values())
    return " · ".join(approx(m) for m in by_currency.values())


def _totals(v: View, accounts: list[AccountData], rates: Rates | None, currency: str) -> list[str]:
    live = _live(accounts)
    balances = [d.balance.money for d in live if d.balance is not None]
    costs = [money for d in live for money in monthly_cost(d.servers)]
    lines = []
    if balances:
        lines.append(v.t("home.balances", amount=_sum(balances, rates, currency, exact=True)))
    if costs:
        lines.append(v.t("home.month", amount=_sum(costs, rates, currency, exact=False)))
    return lines


# Home


def _state_line(v: View, accounts: list[AccountData], found: list[Problem]) -> str:
    live = _live(accounts)
    servers = sum(len(d.servers) for d in live)
    counts = f"{v.count(len(live), 'hoster')} · {v.count(servers, 'server')}"
    if not found:
        return f"<b>🟢 {v.t('home.ok')} · {counts}</b>"
    level = _level_of(found)
    state = v.count(len(found), "problem")
    return f"<b>{ICON[level]} {state} · {counts}</b>"


def menu(
    v: View,
    accounts: list[AccountData],
    rates: Rates | None = None,
    currency: str = "RUB",
    th: Thresholds | None = None,
    app_url: str | None = None,
) -> Screen:
    """The home screen: the state, the worst problems, the money; then the sections.
    `app_url`: the Mini App, opened by «📊 Analytics» (a domain is set)."""
    kb = InlineKeyboardBuilder()
    if not accounts:
        lines = [f"<b>🔌 {v.t('home.empty')}</b>", v.t("home.empty_hint")]
        kb.row(_button(v.t("menu.connect"), Nav(to="connect")))
        kb.row(_button(v.t("menu.settings"), Nav(to="settings")))
        return Screen("\n".join(lines), kb.as_markup())
    found = problems(v, accounts, th)
    lines = [_state_line(v, accounts, found)]
    lines += [p.line() for p in found[:HOME_PROBLEMS]]
    if len(found) > HOME_PROBLEMS:
        lines.append(v.t("home.more", count=len(found) - HOME_PROBLEMS))
    lines += _totals(v, accounts, rates, currency)
    if found:
        kb.row(_button(v.t("menu.problems", count=len(found)), Nav(to="problems")))
    if app_url is not None:
        kb.row(InlineKeyboardButton(text=v.t("webapp.button"), web_app=WebAppInfo(url=app_url)))
    kb.row(
        _button(v.t("menu.servers"), Nav(to="servers")),
        _button(v.t("menu.balance"), Nav(to="balance")),
    )
    kb.row(
        _button(v.t("menu.integrations"), Nav(to="integrations")),
        _button(v.t("menu.settings"), Nav(to="settings")),
    )
    return Screen("\n".join(lines), kb.as_markup())


def problems_screen(v: View, accounts: list[AccountData], th: Thresholds | None = None) -> Screen:
    """Every problem with a button to the place where it is solved."""
    found = problems(v, accounts, th)
    kb = InlineKeyboardBuilder()
    if not found:
        lines = [f"<b>🟢 {v.t('problems.none')}</b>"]
    else:
        lines = [f"<b>{ICON[_level_of(found)]} {v.count(len(found), 'problem')}</b>"]
        lines += [p.line() for p in found[:PROBLEMS_SHOWN]]
        if len(found) > PROBLEMS_SHOWN:
            lines.append(v.t("home.more", count=len(found) - PROBLEMS_SHOWN))
    buttons: list[InlineKeyboardButton] = []
    seen: set[str] = set()
    for problem in found[:PROBLEMS_SHOWN]:
        packed = problem.target.pack()
        if packed not in seen:
            seen.add(packed)
            buttons.append(InlineKeyboardButton(text=problem.button, callback_data=packed))
    pairs(kb, buttons)
    kb.row(_button(v.t("button.refresh"), RefreshCb(acc=0, view="problems")))
    kb.row(*_nav(v))
    return Screen(_fit(lines), kb.as_markup())


# The daily summary (a notification of its own)


def _lasts(v: View, data: AccountData) -> str:
    f = data.forecast
    if f is None:
        return ""
    if f.method is Method.NO_SPENDING:
        return v.t("money.no_spending")
    if f.runs_out_at is None:
        return v.t("money.beyond_horizon")
    return v.t("money.until", date=v.when(f.runs_out_at, f.method in HOURLY_METHODS))


def _money_line(v: View, data: AccountData, th: Thresholds) -> str:
    """«🟡 Timeweb · 325.40 ₽ · до сегодня 18:12»."""
    title = escape(data.title)
    if _failed(data) and data.balance is None:
        return f"🔴 {title} · {v.t('money.no_data')}"
    if data.balance is None:
        return f"⚪ {title} · {v.t('money.no_data')}"
    parts = [title, str(data.balance.money)]
    lasts = _lasts(v, data)
    if lasts:
        parts.append(lasts)
    level = _money_level(v, data, th)
    if _failed(data):
        level = Level.BAD
        at = data.account.last_ok_at or data.balance_at
        if at is not None:
            parts.append(v.t("money.data_at", date=v.moment(at)))
    return f"{ICON[level]} " + " · ".join(parts)


def summary_text(
    v: View,
    accounts: list[AccountData],
    rates: Rates | None,
    currency: str,
    now: datetime,
    th: Thresholds | None = None,
) -> str:
    th = th or Thresholds()
    lines = [v.t("summary.title", date=v.moment(now))]
    if not accounts:
        return "\n".join([*lines, v.t("home.empty")])
    found = problems(v, accounts, th)
    lines.append(_state_line(v, accounts, found).replace("<b>", "").replace("</b>", ""))
    lines += [p.line() for p in found[:PROBLEMS_SHOWN]]
    if len(found) > PROBLEMS_SHOWN:
        lines.append(v.t("home.more", count=len(found) - PROBLEMS_SHOWN))
    lines.append("")
    lines += [_money_line(v, d, th) for d in _live(accounts)]
    lines += _totals(v, accounts, rates, currency)
    return _fit(lines)


def summary_markup(v: View, has_problems: bool) -> InlineKeyboardMarkup:
    """Under the daily summary: a new menu below, on the home or the problems screen."""
    kb = InlineKeyboardBuilder()
    row = [InlineKeyboardButton(text=v.t("summary.open"), callback_data=note(Nav(to="menu")))]
    if has_problems:
        row.append(
            InlineKeyboardButton(
                text=v.t("summary.problems"), callback_data=note(Nav(to="problems"))
            )
        )
    kb.row(*row)
    return kb.as_markup()


# Servers


def _ordered(servers: list[Server]) -> list[Server]:
    """VPS first, then dedicated servers: the order of the list and of its pages."""
    return [server for kind in ServerKind for server in servers if server.kind is kind]


def _pages(count: int) -> int:
    return max(1, -(-count // SERVERS_PAGE))


def server_page(data: AccountData, server: Server) -> int:
    """The page of the account's server list the server is on."""
    ids = [s.external_id for s in _ordered(data.servers)]
    return ids.index(server.external_id) // SERVERS_PAGE if server.external_id in ids else 0


def servers(
    v: View, accounts: list[AccountData], th: Thresholds | None = None, can_order: bool = False
) -> Screen:
    """First level: the hosters, with their number of servers and their state."""
    live = _live(accounts)
    found = problems(v, accounts, th)
    total = sum(len(d.servers) for d in live)
    kb = InlineKeyboardBuilder()
    if not live:
        lines = [f"<b>🖥 {v.t('servers.empty')}</b>"]
    else:
        troubled = [p for p in found if p.server_id]
        state = v.t("servers.all_ok") if not troubled else v.count(len(troubled), "problem")
        icon = ICON[_level_of(troubled)]
        lines = [f"<b>🖥 {v.count(total, 'server')} · {icon} {state}</b>", v.t("servers.pick")]
        lines += [f"🔴 {escape(d.title)} — {_failed_line(v, d)}" for d in live if _failed(d)]
    buttons = [
        _button(
            f"{ICON[_account_level(d, found)]} {d.title} · {len(d.servers)}",
            HostCb(acc=d.account.id),
        )
        for d in live
    ]
    pairs(kb, buttons)
    extra = []
    if can_order:
        extra.append(_button(v.t("menu.order"), Nav(to="order")))
    if live:
        extra.append(_button(v.t("button.refresh"), RefreshCb(acc=0, view="servers")))
    if extra:
        kb.row(*extra)
    kb.row(*_nav(v))
    return Screen("\n".join(lines), kb.as_markup())


def _server_label(
    v: View, data: AccountData, server: Server, found: list[Problem], own_ip: str | None
) -> str:
    """«🟢 bear · 3 d.»: the state, the name, the days to the next charge if known."""
    parts = [f"{_server_icon(server, found, data.account.id)} {server.name}"]
    if server.billing is BillingModel.PERIODIC and server.next_charge_at is not None:
        parts.append(v.days_short(server.next_charge_at))
    if own_ip is not None and server.ip == own_ip:
        parts.append(v.t("servers.own"))
    return " · ".join(parts)


def host_servers(
    v: View,
    data: AccountData,
    page: int,
    own_ip: str | None = None,
    th: Thresholds | None = None,
) -> Screen:
    """Second level: one hoster's servers, VPS first, SERVERS_PAGE a page."""
    ordered = _ordered(data.servers)
    pages = _pages(len(ordered))
    page = min(max(page, 0), pages - 1)
    shown = ordered[page * SERVERS_PAGE : (page + 1) * SERVERS_PAGE]
    acc = data.account.id
    found = [p for p in problems(v, [data], th) if p.account_id == acc]
    level = Level.BAD if _failed(data) else _level_of(found)
    lines = [f"<b>{ICON[level]} {escape(data.title)} · {v.count(len(ordered), 'server')}</b>"]
    kinds = [kind for kind in ServerKind if any(s.kind is kind for s in ordered)]
    if len(kinds) > 1:
        lines.append(
            " · ".join(
                v.t(f"servers.count.{k.value}", n=sum(s.kind is k for s in ordered)) for k in kinds
            )
        )
    if pages > 1:
        lines.append(v.t("bot.servers.page", page=page + 1, pages=pages))
    if _failed(data):
        lines.append(f"🔴 {_failed_line(v, data)}")
    server_found = [p for p in found if p.server_id]
    lines += [
        f"{LINE_ICON[p.level]} {escape(_name_of(data, p.server_id))} — {p.what}"
        for p in server_found[:HOME_PROBLEMS]
    ]
    if len(server_found) > HOME_PROBLEMS:
        lines.append(v.t("home.more", count=len(server_found) - HOME_PROBLEMS))
    if not ordered:
        lines.append(v.t("servers.none"))
    kb = InlineKeyboardBuilder()
    for server in shown:
        label = _server_label(v, data, server, found, own_ip)
        kb.row(_button(label, ServerCb(acc=acc, sid=server.external_id)))
    if pages > 1:
        turn = []
        if page > 0:
            turn.append(_button("◀", HostCb(acc=acc, page=page - 1)))
        turn.append(_button(f"{page + 1}/{pages}", HostCb(acc=acc, page=page)))
        if page < pages - 1:
            turn.append(_button("▶", HostCb(acc=acc, page=page + 1)))
        kb.row(*turn)
    kb.row(_button(v.t("button.refresh"), RefreshCb(acc=acc, view="host", sid=str(page))))
    kb.row(*_nav(v, Nav(to="servers")))
    return Screen("\n".join(lines), kb.as_markup())


def _name_of(data: AccountData, server_id: str) -> str:
    return next((s.name for s in data.servers if s.external_id == server_id), server_id)


def _price(v: View, server: Server) -> str:
    if server.price is None:
        return "—"
    if server.billing is BillingModel.HOURLY:
        month = _month_price(server)
        assert month is not None
        return v.t("price.hourly", amount=server.price, month=approx(month))
    if server.period_months == 1:
        return v.t("price.month", amount=server.price)
    return v.t("price.months", amount=server.price, months=server.period_months)


def _usage(v: View, data: AccountData, usage: Usage | None, th: Thresholds) -> str:
    """«CPU 12%  RAM 93% ⚠️  Disk 41%»: a value above its threshold is marked."""
    if Capability.USAGE not in data.caps:
        return v.t("card.usage_unsupported")
    parts = []
    if usage is not None:
        for name, value, limit in (
            ("CPU", usage.cpu_percent, th.load_percent),
            ("RAM", usage.ram_percent, th.load_percent),
            (v.t("card.disk"), usage.disk_percent, th.disk_percent),
        ):
            if value is not None:
                mark = " ⚠️" if value >= limit else ""
                parts.append(f"{name} {value:.0f}%{mark}")
        if usage.traffic_gb is not None:
            parts.append(v.t("card.traffic", gb=f"{usage.traffic_gb:.1f}"))
    if not parts:
        return v.t("card.usage_none")
    return "  ".join(parts)


def _bills(server: Server) -> bool:
    return server.billing is BillingModel.PERIODIC and server.autorenew is Autorenew.ON


def autopay_lines(v: View, data: AccountData, server: Server) -> list[str]:
    """The bot's autopay for this server, or a hint to use the hoster's own autorenewal."""
    if autopay_eligible(data.caps, server):
        on = server.external_id in data.autopay
        return [v.t("autopay.card.on" if on else "autopay.card.off")]
    native = ServerAction.AUTORENEW in server.actions and Capability.AUTORENEW in data.caps
    if native and server.autorenew is Autorenew.OFF and server.billing is BillingModel.PERIODIC:
        return [v.t("autopay.card.native_hint")]
    return []


def server_actions(data: AccountData, server: Server) -> list[str]:
    """Action buttons the plugin and this server both allow, in display order.

    Power buttons also follow the status: no «Start» for a running server.
    """
    caps = data.caps
    allowed = {a for a in server.actions if ACTION_CAPABILITY[a] in caps}
    running = server.status is ServerStatus.RUNNING
    stopped = server.status is ServerStatus.STOPPED
    shown = []
    if ServerAction.RESTART in allowed and not stopped:
        shown.append("restart")
    if ServerAction.START in allowed and not running:
        shown.append("start")
    if ServerAction.STOP in allowed and not stopped:
        shown.append("stop")
    if ServerAction.RENEW in allowed:
        shown.append("renew")
    if ServerAction.AUTORENEW in allowed and server.autorenew is not Autorenew.NOT_AVAILABLE:
        shown.append("autorenew_off" if server.autorenew is Autorenew.ON else "autorenew_on")
    # The bot's own autopay is for hosters without autorenewal (spec, decision 10).
    if autopay_eligible(caps, server):
        shown.append("autopay")
    if Capability.ORDER in caps:
        shown.append("order_same")
    return shown


def server_card(
    v: View,
    data: AccountData,
    server: Server,
    usage: Usage | None,
    own_days: int | None = None,
    th: Thresholds | None = None,
    app_url: str | None = None,
) -> Screen:
    """`own_days`: this is hostwatch's own server, warned about that many days earlier.
    With `app_url`, «📊 Charts» opens the Mini App on this server."""
    acc, sid = data.account.id, server.external_id
    found = problems(v, [data], th)
    title = f"{_server_icon(server, found, acc)} {escape(server.name)} · {escape(data.title)}"
    if server.status is not ServerStatus.RUNNING:
        title += f" — {v.t(f'status.{server.status.value}')}"
    facts = [v.t("card.ip", ip=escape(server.ip)) if server.ip else v.t("card.no_ip")]
    facts.append(v.t(f"servers.kind.{server.kind.value}"))
    if server.plan:
        facts.append(escape(server.plan))
    lines = [f"<b>{title}</b>", " · ".join(facts), _usage(v, data, usage, th or Thresholds())]
    # The load is marked in its own line; the status is in the title.
    lines += [
        f"{LINE_ICON[p.level]} {p.what}"
        for p in found
        if p.server_id == sid and p.level is Level.WARN and not p.load
    ]
    price = [v.t("card.price", price=_price(v, server))]
    if server.billing is BillingModel.PERIODIC and server.next_charge_at is not None:
        renews = _bills(server) or sid in data.autopay
        key = "card.next_charge" if renews else "card.ends"
        at = server.next_charge_at
        price.append(v.t(key, date=v.day(at), left=v.in_days(at)))
    lines.append(" · ".join(price))
    if server.billing is BillingModel.PERIODIC:
        autorenew = v.t(f"autorenew.{server.autorenew.value.replace('/', '')}")
        lines.append(v.t("card.autorenew", value=autorenew))
    lines += autopay_lines(v, data, server)
    if own_days is not None:
        lines.append(v.t("card.own", days=own_days))
    if _failed(data):
        lines.append(f"🔴 {_failed_line(v, data)}")
    elif data.servers_at is not None:
        lines.append(v.t("card.data_at", date=v.moment(data.servers_at)))

    kb = InlineKeyboardBuilder()
    buttons = []
    for action in server_actions(data, server):
        text = v.t(f"action.{action}")
        if action == "autopay":
            text = v.t("autopay.button.off" if sid in data.autopay else "autopay.button.on")
        buttons.append(_button(text, ActionCb(acc=acc, sid=sid, act=action)))
    if Capability.ROOT_PASSWORD in data.caps:
        buttons.append(
            _button(v.t("order.button.password"), ActionCb(acc=acc, sid=sid, act="password"))
        )
    buttons.append(_button(v.t("button.refresh"), RefreshCb(acc=acc, view="server", sid=sid)))
    if app_url:
        url = f"{app_url}?server={acc}:{quote(sid, safe='')}"
        buttons.append(InlineKeyboardButton(text=v.t("card.charts"), web_app=WebAppInfo(url=url)))
    pairs(kb, buttons)
    kb.row(*_nav(v, HostCb(acc=acc, page=server_page(data, server))))
    return Screen("\n".join(lines), kb.as_markup())


# Money


def balance(
    v: View,
    accounts: list[AccountData],
    rates: Rates | None = None,
    currency: str = "RUB",
    th: Thresholds | None = None,
) -> Screen:
    """«💰 Money»: a line per hoster, the totals, «Top up» buttons; details behind «ℹ️»."""
    th = th or Thresholds()
    live = _live(accounts)
    kb = InlineKeyboardBuilder()
    if not live:
        lines = [f"<b>💰 {v.t('home.empty')}</b>"]
        kb.row(*_nav(v))
        return Screen("\n".join(lines), kb.as_markup())
    levels = [Level.BAD if _failed(d) else _money_level(v, d, th) for d in live]
    worst = max(levels, default=Level.OK)
    state = v.t("money.ok") if worst is Level.OK else v.t("money.attention")
    lines = [f"<b>💰 {v.t('money.title')} · {ICON[worst]} {state}</b>"]
    lines += [_money_line(v, d, th) for d in live]
    for data in live:
        f = data.forecast
        if f is not None and f.shortfall is not None and _money_level(v, data, th) is Level.OK:
            lines.append(
                "⚠️ "
                + v.t(
                    "money.shortfall",
                    title=escape(data.title),
                    server=escape(f.shortfall.server),
                    amount=f.shortfall.missing,
                    date=v.day(f.shortfall.at),
                )
            )
    lines += _totals(v, accounts, rates, currency)
    if rates is not None:
        lines.append(v.t("money.rate_note", date=rates.date.strftime("%d.%m")))
    buttons = []
    for data in live:
        if Capability.TOPUP in data.caps:
            buttons.append(
                _button(v.t("balance.topup", title=data.title), TopupCb(acc=data.account.id))
            )
        if {Capability.INVOICES, Capability.PAY_INVOICE} <= data.caps:
            buttons.append(
                _button(v.t("balance.invoices", title=data.title), InvoicesCb(acc=data.account.id))
            )
    pairs(kb, buttons)
    kb.row(
        _button(v.t("money.button.info"), Nav(to="money_info")),
        _button(v.t("button.refresh"), RefreshCb(acc=0, view="balance")),
    )
    kb.row(*_nav(v))
    return Screen(_fit(lines), kb.as_markup())


def money_info(v: View, accounts: list[AccountData]) -> Screen:
    """«ℹ️ How it is counted»: the forecast of every hoster, in full."""
    lines = [f"<b>ℹ️ {v.t('money.info_title')}</b>"]
    for data in _live(accounts):
        lines.append("")
        lines.append(f"🔌 {escape(data.title)}")
        if data.balance is None or data.balance_at is None:
            lines.append(v.t("money.no_data"))
            continue
        lines.append(
            v.t("money.read_at", amount=data.balance.money, date=v.moment(data.balance_at))
        )
        if data.balance.hours_left is not None:
            lines.append(v.t("balance.hours_left", hours=f"{data.balance.hours_left:g}"))
        if data.forecast is not None:
            lines.append(escape(data.forecast.explain(v.lang, v.tz), quote=False))
    lines += ["", v.t("money.info_rates")]
    kb = InlineKeyboardBuilder()
    kb.row(*_nav(v, Nav(to="balance")))
    return Screen(_fit(lines), kb.as_markup())


# Hosters (integrations)


def account_status(v: View, status: AccountStatus) -> str:
    return v.t(f"account.status.{status.value}")


STATUS_ICON = {
    AccountStatus.OK: "🟢",
    AccountStatus.RELOGIN: "🔴",
    AccountStatus.ERROR: "🔴",
    AccountStatus.DISABLED: "⏸",
}


def integrations(
    v: View, accounts: list[AccountData], plugins: Sequence[type[Plugin]] = ()
) -> Screen:
    """Connected accounts; the hosters not connected yet are behind «＋ Connect»."""
    kb = InlineKeyboardBuilder()
    connected = {d.account.plugin_id for d in accounts}
    fresh = [p.meta.name for p in plugins if p.meta.id not in connected]
    if not accounts:
        lines = [f"<b>🔌 {v.t('integrations.none')}</b>", v.t("bot.integrations.empty")]
    else:
        bad = [d for d in accounts if _failed(d)]
        state = v.t("integrations.all_ok") if not bad else v.count(len(bad), "problem")
        icon = "🔴" if bad else "🟢"
        lines = [f"<b>🔌 {v.count(len(accounts), 'hoster')} · {icon} {state}</b>"]
        for data in accounts:
            status = data.account.status
            detail = (
                v.count(len(data.servers), "server")
                if status is AccountStatus.OK
                else account_status(v, status)
            )
            lines.append(f"{STATUS_ICON[status]} {escape(data.title)} · {detail}")
    if fresh:
        lines.append(v.t("integrations.can_add", names=escape(", ".join(fresh))))
    pairs(
        kb,
        [
            _button(
                f"{STATUS_ICON[d.account.status]} {d.title}", AccountCb(acc=d.account.id, op="card")
            )
            for d in accounts
        ],
    )
    kb.row(_button(v.t("bot.integrations.connect"), Nav(to="connect")))
    kb.row(*_nav(v))
    return Screen("\n".join(lines), kb.as_markup())


def connect(v: View, plugins: list[type[Plugin]], accounts: list[AccountData]) -> Screen:
    """Hosters to add: those not connected yet first, then another account of the others."""
    connected = {d.account.plugin_id for d in accounts}
    fresh = [p for p in plugins if p.meta.id not in connected]
    more = [p for p in plugins if p.meta.id in connected]
    lines = [v.t("bot.connect.title")]
    lines.append(v.t("bot.connect.choose") if fresh else v.t("bot.connect.all_connected"))
    if more:
        lines.append(v.t("bot.connect.more"))
    kb = InlineKeyboardBuilder()
    pairs(kb, [_button(p.meta.name, AddCb(plugin=p.meta.id)) for p in fresh])
    pairs(
        kb,
        [
            _button(v.t("bot.connect.another", hoster=p.meta.name), AddCb(plugin=p.meta.id))
            for p in more
        ],
    )
    kb.row(*_nav(v, Nav(to="integrations")))
    return Screen("\n".join(lines), kb.as_markup())


def account_card(v: View, data: AccountData) -> Screen:
    account = data.account
    status = account.status
    lines = [f"<b>{STATUS_ICON[status]} {escape(data.title)} · {account_status(v, status)}</b>"]
    facts = []
    if data.servers_at is not None:
        facts.append(f"🖥 {v.count(len(data.servers), 'server')}")
    if data.balance is not None:
        facts.append(f"💰 {data.balance.money}")
    if facts:
        lines.append(" · ".join(facts))
    if account.last_ok_at is not None:
        lines.append(v.t("account.card.last_ok", date=v.moment(account.last_ok_at)))
    else:
        lines.append(v.t("account.never"))
    if account.last_error and status is not AccountStatus.OK:
        lines.append(f"⚠️ {escape(reason(v, account.last_error))}")

    kb = InlineKeyboardBuilder()
    acc = account.id
    buttons = []
    if status is not AccountStatus.DISABLED and not data.plugin.login_only:
        buttons.append(_button(v.t("button.refresh"), RefreshCb(acc=acc, view="account")))
    if any(isinstance(auth, BrowserAuth) for auth in data.plugin.meta.auth):
        buttons.append(_button(v.t("account.relogin"), AccountCb(acc=acc, op="relogin")))
    if key_fields(data.plugin):
        buttons.append(_button(v.t("account.change_key"), AccountCb(acc=acc, op="key")))
    if status is AccountStatus.DISABLED:
        buttons.append(_button(v.t("account.enable"), AccountCb(acc=acc, op="enable")))
    else:
        buttons.append(_button(v.t("account.disable"), AccountCb(acc=acc, op="disable")))
    buttons.append(_button(v.t("account.delete"), AccountCb(acc=acc, op="delete")))
    pairs(kb, buttons)
    kb.row(*_nav(v, Nav(to="integrations")))
    return Screen("\n".join(lines), kb.as_markup())


def confirm_delete(v: View, data: AccountData) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(_button(v.t("button.yes_delete"), AccountCb(acc=data.account.id, op="delete_yes")))
    kb.row(*_nav(v, AccountCb(acc=data.account.id, op="card")))
    return Screen(v.t("account.delete_confirm", title=escape(data.title)), kb.as_markup())


def ask_label(v: View, plugin_name: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(_button(v.t("integrations.label_default", name=plugin_name), LabelCb()))
    kb.row(*_nav(v, Nav(to="connect")))
    return Screen(v.t("integrations.ask_label", hoster=escape(plugin_name)), kb.as_markup())


def ask_key(v: View, plugin_name: str, field: str, back: CallbackData | None = None) -> Screen:
    """`back`: where «Back» leads; the hoster list when a new account is being added."""
    kb = InlineKeyboardBuilder()
    kb.row(*_nav(v, back or Nav(to="connect")))
    return Screen(
        v.t("integrations.ask_key", hoster=escape(plugin_name), field=escape(field)),
        kb.as_markup(),
    )


# Journal


JOURNAL_ACTIONS = frozenset(
    {
        "topup",
        "login",
        "start",
        "stop",
        "restart",
        "autorenew_on",
        "autorenew_off",
        "renew",
        "pay_invoice",
        "connect",
        "disconnect",
    }
)
# Actions whose record names a sum worth showing.
JOURNAL_MONEY = frozenset(
    {"renew", "pay_invoice", "topup", "order", "topup_paid", "topup_result", "autopay_hold"}
)


def journal_action(
    v: View, entry: JournalEntry, servers: Mapping[tuple[int, str], str] | None = None
) -> str:
    """What was done, in the owner's language; the server, invoice and sum when there are.
    `servers` names servers by (account, server id)."""
    p = entry.params
    name = _journal_name(v, entry)
    parts = [name]
    sid = p.get("server")
    if sid and entry.action != "order" and entry.account_id is not None:
        parts.append(f"· {(servers or {}).get((entry.account_id, sid), sid)}")
    invoice = p.get("invoice")
    if invoice and entry.action in ("pay_invoice", "autopay_hold", "autopay_answer"):
        parts.append(f"#{invoice}")
    if entry.action == "disconnect" and p.get("plugin"):
        label = p.get("label")
        parts.append(f"· {p['plugin']}" + (f" ({label})" if label else ""))
    amount = p.get("amount")
    if amount and entry.action in JOURNAL_MONEY:
        parts.append(_journal_money(amount, p.get("currency", "")))
    return " ".join(parts)


def _journal_name(v: View, entry: JournalEntry) -> str:
    action, p = entry.action, entry.params
    if action in JOURNAL_ACTIONS:
        return v.t(f"journal.action.{action}")
    if action in ("order", "root_password"):
        return v.t(f"order.journal.{action}")
    if action == "account_status":
        new = p.get("new", "")
        if new == AccountStatus.OK.value:
            return v.t("journal.action.account_back")
        known = {status.value for status in AccountStatus}
        status = v.t(f"account.status.{new}") if new in known else new
        return v.t("journal.action.account_status", status=status)
    if action == "settings":
        return _journal_setting(v, p.get("key", ""), p.get("old", ""), p.get("new", ""))
    if action == "topup_paid":
        return v.t("journal.action.topup_paid")
    if action == "topup_result":
        arrived = entry.response == "arrived"
        return v.t("journal.action.topup_arrived" if arrived else "journal.action.topup_not_seen")
    if action == "autopay_mark":
        return v.t(
            "journal.action.autopay_on" if p.get("on") == "yes" else "journal.action.autopay_off"
        )
    if action == "autopay_hold":
        reason = p.get("reason", "")
        key = f"journal.hold.{reason}"
        text = v.t(key) if key in JOURNAL_HOLDS else reason
        return v.t("journal.action.autopay_hold", reason=text)
    if action == "autopay_answer":
        approved = p.get("answer") == "approved"
        return v.t(
            "journal.action.autopay_approved" if approved else "journal.action.autopay_declined"
        )
    return action


JOURNAL_HOLDS = frozenset(
    f"journal.hold.{r}" for r in ("ask_grown", "ask_new", "over_limit", "low_balance")
)
JOURNAL_SETTINGS = frozenset(
    {
        "lang",
        "timezone",
        "currency",
        "threshold.balance_days",
        "threshold.load_percent",
        "threshold.disk_percent",
        "threshold.quiet_hours",
        "threshold.summary_time",
        "order.limit",
        "order.ssh_key",
        "autopay.days",
    }
)


def _journal_setting(v: View, key: str, old: str, new: str) -> str:
    if key in JOURNAL_SETTINGS:
        name = v.t(f"journal.setting.{key}")
    elif key.startswith("autopay.limit."):
        name = v.t("journal.setting.autopay.limit")
    else:
        name = key
    if key == "order.ssh_key":
        old = v.t("journal.setting.set") if old else ""
        new = v.t("journal.setting.set" if new == "set" else "journal.setting.cleared")
    if key == "lang":
        old, new = LANGUAGE_NAMES.get(old, old), LANGUAGE_NAMES.get(new, new)
    return v.t("journal.action.settings", name=name, old=old or "—", new=new or "—")


def _journal_money(amount: str, currency: str) -> str:
    """«500.00 ₽» like every sum in the bot; the raw text if it is not a sum."""
    try:
        return str(Money.of(amount, currency))
    except (ValueError, ArithmeticError):
        return f"{amount} {currency}".strip()


def journal(
    v: View,
    entries: list[JournalEntry],
    page: int,
    titles: dict[int, str],
    has_more: bool,
    servers: Mapping[tuple[int, str], str] | None = None,
) -> Screen:
    title = v.t("journal.title")
    if page > 0 or has_more:
        title += f" · {v.t('journal.page', page=page + 1)}"
    lines = [f"<b>{title}</b>"]
    if not entries:
        lines.append(v.t("journal.empty"))
    for entry in entries:
        hoster = titles.get(entry.account_id, "—") if entry.account_id is not None else "—"
        # A held autopay waits for the owner; it did not fail.
        mark = "⏳" if entry.action == "autopay_hold" else "✅" if entry.ok else "❌"
        who = "🤖 " if entry.actor != "owner" else ""
        lines.append(
            v.t(
                "journal.line",
                date=v.moment(entry.at),
                mark=mark,
                who=who,
                hoster=escape(hoster),
                action=escape(journal_action(v, entry, servers)),
            )
        )
    kb = InlineKeyboardBuilder()
    turn = []
    if page > 0:
        turn.append(_button("◀", JournalCb(page=page - 1)))
    if has_more:
        turn.append(_button("▶", JournalCb(page=page + 1)))
    if turn:
        kb.row(*turn)
    kb.row(*_nav(v, Nav(to="settings")))
    return Screen(_fit(lines), kb.as_markup())


# Settings and first run


def settings(v: View, timezone: str, currency: str, thresholds: dict[str, str]) -> Screen:
    lines = [
        f"<b>⚙️ {v.t('settings.title')}</b>",
        v.t("settings.general", language=LANGUAGE_NAMES[v.lang], tz=timezone, currency=currency),
        v.t(
            "settings.alerts",
            days=thresholds.get("balance_days", "—"),
            load=thresholds.get("load_percent", "—"),
            disk=thresholds.get("disk_percent", "—"),
        ),
        v.t(
            "settings.times",
            quiet=thresholds.get("quiet_hours", "—"),
            summary=thresholds.get("summary_time", "—"),
        ),
    ]
    kb = InlineKeyboardBuilder()
    buttons = [
        _button(v.t("settings.button.language"), SetCb(key="lang")),
        _button(v.t("settings.button.timezone"), SetCb(key="tz")),
        _button(v.t("settings.button.currency"), SetCb(key="cur")),
    ]
    buttons += [
        _button(v.t(f"threshold.button.{item.key}"), SetCb(key="th", value=item.key))
        for item in THRESHOLDS
    ]
    buttons += [
        _button(v.t("autopay.settings.button"), SetCb(key="ap")),
        _button(v.t("order.settings.button"), SetCb(key="order")),
        _button(v.t("menu.journal"), Nav(to="journal")),
        _button(v.t("settings.button.backup"), SetCb(key="backup")),
    ]
    pairs(kb, buttons)
    kb.row(*_nav(v))
    return Screen("\n".join(lines), kb.as_markup())


def choose_language(v: View, onb: bool) -> Screen:
    kb = InlineKeyboardBuilder()
    for lang in LANGUAGES:
        kb.button(text=LANGUAGE_NAMES[lang], callback_data=SetCb(key="lang", value=lang, onb=onb))
    if not onb:
        kb.row(*_nav(v, Nav(to="settings")))
    # The owner has not picked a language yet, so the first question is in both.
    text = (
        v.t("onboarding.language")
        if not onb
        else f"{t('onboarding.language', 'ru')}\n" + t("onboarding.language", "en")
    )
    return Screen(text, kb.as_markup())


def choose_timezone(v: View, onb: bool) -> Screen:
    suggested = DEFAULT_TZ[v.lang]
    zones = [suggested, *(zone for zone in TIMEZONES if zone != suggested)]
    kb = InlineKeyboardBuilder()
    for zone in zones:
        label = f"{zone} ✓" if zone == suggested else zone
        kb.button(text=label, callback_data=SetCb(key="tz", value=zone, onb=onb))
    kb.adjust(2)
    kb.row(_button(v.t("settings.timezone_other"), SetCb(key="tz", value="other", onb=onb)))
    if not onb:
        kb.row(*_nav(v, Nav(to="settings")))
    return Screen(v.t("onboarding.timezone", suggested=suggested), kb.as_markup())


def choose_currency(v: View, onb: bool) -> Screen:
    kb = InlineKeyboardBuilder()
    for currency in CURRENCIES:
        kb.button(text=currency, callback_data=SetCb(key="cur", value=currency, onb=onb))
    if not onb:
        kb.row(*_nav(v, Nav(to="settings")))
    return Screen(v.t("onboarding.currency"), kb.as_markup())


def ask_timezone(v: View, onb: bool) -> Screen:
    """A time zone to type. On the first run there is nowhere to go back to yet."""
    kb = InlineKeyboardBuilder()
    if not onb:
        kb.row(*_nav(v, SetCb(key="tz")))
    return Screen(v.t("settings.timezone_ask"), kb.as_markup())


def ask_threshold(v: View, key: str, current: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(*_nav(v, Nav(to="settings")))
    return Screen(v.t(f"threshold.ask.{key}", value=current), kb.as_markup())


# Buttons under notifications

# Alert keys are «kind:account[:server[:metric]]» (`core.alerts`).
SERVER_ALERTS = frozenset({"server_status", "ends_soon", "load", "disk"})
ACCOUNT_ALERTS = frozenset({"relogin", "plugin_broken"})
MAX_ALERT_BUTTONS = 6


def home_note(v: View) -> InlineKeyboardMarkup:
    """«🏠 Open the menu» under a notification that has no other button."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=v.t("summary.open"), callback_data=note(Nav(to="menu"))))
    return kb.as_markup()


def alert_buttons(
    v: View, keys: list[str], titles: dict[int, str], servers: dict[tuple[int, str], str]
) -> InlineKeyboardMarkup:
    """«Top up», «Open server» and «Open hoster» under alerts. They open a new menu below on
    that screen (`callbacks.note`). Accounts and servers that are gone get no button; an
    alert left without one gets «🏠 Open the menu»."""
    seen: set[str] = set()
    buttons: list[InlineKeyboardButton] = []
    for key in keys:
        kind, _, rest = key.partition(":")
        acc_text, _, sid = rest.partition(":")
        sid = sid.split(":", 1)[0]
        if not acc_text.isdigit() or int(acc_text) not in titles:
            continue
        acc = int(acc_text)
        data: CallbackData
        if kind == "balance_low":
            text, data = v.t("problem.button.topup", title=titles[acc]), TopupCb(acc=acc)
        elif kind in SERVER_ALERTS and (acc, sid) in servers:
            text, data = (
                v.t("problem.button.server", server=servers[acc, sid]),
                ServerCb(acc=acc, sid=sid),
            )
        elif kind in ACCOUNT_ALERTS:
            text, data = (
                v.t("problem.button.account", title=titles[acc]),
                AccountCb(acc=acc, op="card"),
            )
        else:
            continue
        try:
            packed = note(data)
        except ValueError:
            continue
        if packed in seen:
            continue
        seen.add(packed)
        buttons.append(InlineKeyboardButton(text=text, callback_data=packed))
        if len(seen) >= MAX_ALERT_BUTTONS:
            break
    if not buttons:
        return home_note(v)
    kb = InlineKeyboardBuilder()
    pairs(kb, buttons)
    return kb.as_markup()
