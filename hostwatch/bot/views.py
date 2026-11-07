"""Texts and keyboards of the bot screens: pure functions of the data, so tests call them."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, tzinfo
from decimal import Decimal
from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
    JournalCb,
    LabelCb,
    Nav,
    RefreshCb,
    ServerCb,
    SetCb,
    TopupCb,
)
from .hub import AccountData, key_fields
from .prefs import CURRENCIES, DEFAULT_TZ, LANGUAGES, THRESHOLDS, TIMEZONES

HOURS_PER_MONTH = Decimal(730)
BAD_STATUSES = (ServerStatus.STOPPED, ServerStatus.SUSPENDED, ServerStatus.UNPAID)
JOURNAL_PAGE = 20
LANGUAGE_NAMES = {"ru": "Русский", "en": "English"}
HOURLY_METHODS = (Method.HOSTER_HOURS, Method.HOURLY_RATE, Method.MIXED)


@dataclass(frozen=True)
class Screen:
    text: str
    markup: InlineKeyboardMarkup | None = None


@dataclass(frozen=True)
class View:
    """What every screen needs besides its data: language and time zone of the owner."""

    lang: str
    tz: tzinfo

    def t(self, key: str, **kwargs: object) -> str:
        return t(key, self.lang, **kwargs)

    def moment(self, value: datetime) -> str:
        return value.astimezone(self.tz).strftime("%d.%m %H:%M")

    def day(self, value: datetime) -> str:
        return value.astimezone(self.tz).strftime("%d.%m")


def _button(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def _back(v: View, to: str = "menu") -> InlineKeyboardButton:
    return _button(v.t("button.back"), Nav(to=to))


# Main menu


def menu(v: View, can_order: bool) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(_button(v.t("menu.summary"), Nav(to="summary")))
    kb.row(
        _button(v.t("menu.servers"), Nav(to="servers")),
        _button(v.t("menu.balance"), Nav(to="balance")),
    )
    kb.row(_button(v.t("menu.integrations"), Nav(to="integrations")))
    if can_order:
        kb.row(_button(v.t("menu.order"), Nav(to="order")))
    kb.row(
        _button(v.t("menu.journal"), Nav(to="journal")),
        _button(v.t("menu.settings"), Nav(to="settings")),
    )
    return Screen(v.t("menu.title"), kb.as_markup())


# Errors and data age


def reason(v: View, error: str | None) -> str:
    """Account error from `describe()`, in the owner's language."""
    return explain(error, v.lang)


def _failed_line(v: View, data: AccountData) -> str:
    title = escape(data.title)
    why = escape(reason(v, data.account.last_error))
    at = data.account.last_ok_at or data.servers_at
    if at is None:
        return v.t("account.failed_never", title=title, reason=why)
    return v.t("account.failed", title=title, reason=why, date=v.moment(at))


def _failed(data: AccountData) -> bool:
    return data.account.status in (AccountStatus.ERROR, AccountStatus.RELOGIN)


def _no_data(data: AccountData) -> bool:
    return data.balance is None and data.servers_at is None


# Summary


def _lasts(v: View, data: AccountData) -> str | None:
    f = data.forecast
    if f is None:
        return None
    if f.method is Method.NO_SPENDING:
        return v.t("summary.no_spending")
    if f.runs_out_at is None:
        return v.t("forecast.beyond_horizon")
    date = v.moment(f.runs_out_at) if f.method in HOURLY_METHODS else v.day(f.runs_out_at)
    return v.t("summary.lasts_until", date=date)


def _status_counts(v: View, servers: list[Server]) -> str:
    counts = Counter(server.status for server in servers)
    parts = [
        f"{v.t(f'status.{status.value}')} {count}"
        for status in ServerStatus
        if (count := counts[status])
    ]
    return v.t("summary.servers", count=len(servers), statuses=", ".join(parts))


def _problems(v: View, data: AccountData) -> list[str]:
    lines = [
        v.t("summary.problem_status", server=escape(s.name), status=v.t(f"status.{s.status.value}"))
        for s in data.servers
        if s.status in BAD_STATUSES
    ]
    if data.forecast is not None:
        for item in data.forecast.not_renewing:
            if item.ends_at is None:
                lines.append(v.t("summary.problem_not_renewing", server=escape(item.server)))
            else:
                lines.append(
                    v.t(
                        "summary.problem_ends",
                        server=escape(item.server),
                        date=v.day(item.ends_at),
                    )
                )
    if data.account.status is AccountStatus.RELOGIN:
        lines.append(v.t("summary.problem_relogin"))
    return lines


def _summary_block(v: View, data: AccountData) -> list[str]:
    title = escape(data.title)
    if data.account.status is AccountStatus.DISABLED:
        return [v.t("account.disabled_line", title=title)]
    if _failed(data):
        return [_failed_line(v, data)]
    if _no_data(data):
        return [v.t("account.no_data", title=title)]
    head = [f"<b>{title}</b>"]
    if data.balance is not None:
        head.append(str(data.balance.money))
    lasts = _lasts(v, data)
    if lasts:
        head.append(lasts)
    lines = [" · ".join(head), _status_counts(v, data.servers)]
    lines.extend(f"⚠ {line}" for line in _problems(v, data))
    return lines


def monthly_cost(servers: list[Server]) -> list[Money]:
    """What the servers cost a month, one sum per currency. Hourly prices count 730 hours."""
    totals: dict[str, Money] = {}
    for server in servers:
        price = server.price
        if price is None:
            continue
        if server.billing is BillingModel.HOURLY:
            month = price * HOURS_PER_MONTH
        elif server.period_months:
            month = Money(amount=price.amount / server.period_months, currency=price.currency)
        else:
            continue
        current = totals.get(month.currency)
        totals[month.currency] = month if current is None else current + month
    return list(totals.values())


def _total(amounts: list[Money], rates: Rates, currency: str) -> Money | None:
    total = Money(amount=Decimal(0), currency=currency)
    for amount in amounts:
        converted = rates.convert(amount, currency)
        if converted is None:
            return None
        total += converted
    return total


def _totals(v: View, accounts: list[AccountData], rates: Rates | None, currency: str) -> list[str]:
    live = [d for d in accounts if d.account.status is not AccountStatus.DISABLED]
    balances = [d.balance.money for d in live if d.balance is not None]
    costs = [money for d in live for money in monthly_cost(d.servers)]
    if not balances and not costs:
        return []
    if rates is None:
        return [v.t("summary.no_rates", currency=currency)]
    lines = []
    balance_total = _total(balances, rates, currency)
    cost_total = _total(costs, rates, currency)
    if balance_total is not None and balances:
        lines.append(v.t("summary.total_balance", amount=balance_total))
    if cost_total is not None and costs:
        lines.append(v.t("summary.total_month", amount=cost_total))
    if lines:
        lines.append(v.t("summary.rate_note", date=rates.date.strftime("%d.%m.%Y")))
    else:
        lines.append(v.t("summary.no_rates", currency=currency))
    return lines


def summary_text(
    v: View, accounts: list[AccountData], rates: Rates | None, currency: str, now: datetime
) -> str:
    lines = [v.t("summary.title", date=v.moment(now))]
    if not accounts:
        lines += ["", v.t("summary.empty")]
    for data in accounts:
        lines += ["", *_summary_block(v, data)]
    totals = _totals(v, accounts, rates, currency)
    if totals:
        lines += ["", *totals]
    return "\n".join(lines)


def summary(
    v: View, accounts: list[AccountData], rates: Rates | None, currency: str, now: datetime
) -> Screen:
    kb = InlineKeyboardBuilder()
    if accounts:
        kb.row(_button(v.t("button.refresh"), RefreshCb(acc=0, view="summary")))
    kb.row(_back(v))
    return Screen(summary_text(v, accounts, rates, currency, now), kb.as_markup())


# Servers


def servers(v: View, accounts: list[AccountData], own_ip: str | None = None) -> Screen:
    lines = [v.t("servers.title")]
    kb = InlineKeyboardBuilder()
    live = [d for d in accounts if d.account.status is not AccountStatus.DISABLED]
    for kind in ServerKind:
        rows = [(d, s) for d in live for s in d.servers if s.kind is kind]
        if not rows:
            continue
        lines += ["", f"<b>{v.t(f'servers.kind.{kind.value}')}</b>"]
        for data, server in rows:
            line = v.t(
                "servers.line",
                hoster=escape(data.title),
                name=escape(server.name),
                status=v.t(f"status.{server.status.value}"),
                ip=server.ip or "—",
            )
            if own_ip is not None and server.ip == own_ip:
                line += f" · {v.t('servers.own')}"
            lines.append(line)
            kb.row(
                _button(
                    f"{server.name} · {data.title}",
                    ServerCb(acc=data.account.id, sid=server.external_id),
                )
            )
    if len(lines) == 1:
        lines += ["", v.t("servers.empty")]
    stale = [d for d in live if _failed(d)]
    if stale:
        lines += ["", *(_failed_line(v, d) for d in stale)]
    if live:
        kb.row(_button(v.t("button.refresh"), RefreshCb(acc=0, view="servers")))
    kb.row(_back(v))
    return Screen("\n".join(lines), kb.as_markup())


def _price(v: View, server: Server) -> str:
    if server.price is None:
        return "—"
    if server.billing is BillingModel.HOURLY:
        return v.t("price.hourly", amount=server.price)
    if server.period_months == 1:
        return v.t("price.month", amount=server.price)
    return v.t("price.months", amount=server.price, months=server.period_months)


def _usage(v: View, data: AccountData, usage: Usage | None) -> str:
    if Capability.USAGE not in data.caps:
        return v.t("card.usage_unsupported")
    parts = []
    if usage is not None:
        for name, value in (
            ("CPU", usage.cpu_percent),
            ("RAM", usage.ram_percent),
            (v.t("card.disk"), usage.disk_percent),
        ):
            if value is not None:
                parts.append(f"{name} {value:.0f}%")
        if usage.traffic_gb is not None:
            parts.append(v.t("card.traffic", gb=f"{usage.traffic_gb:.1f}"))
    if not parts:
        return v.t("card.usage_none")
    assert usage is not None
    return f"{', '.join(parts)} ({v.moment(usage.measured_at)})"


def _renews(server: Server) -> bool:
    return server.billing is BillingModel.PERIODIC and server.autorenew is Autorenew.ON


def server_actions(data: AccountData, server: Server) -> list[str]:
    """Action buttons the plugin and this server both allow, in display order.

    Power buttons also follow the status: no «Start» for a running server.
    """
    caps = data.caps
    allowed = {a for a in server.actions if ACTION_CAPABILITY[a] in caps}
    running = server.status is ServerStatus.RUNNING
    stopped = server.status is ServerStatus.STOPPED
    shown = []
    if ServerAction.START in allowed and not running:
        shown.append("start")
    if ServerAction.STOP in allowed and not stopped:
        shown.append("stop")
    if ServerAction.RESTART in allowed and not stopped:
        shown.append("restart")
    if ServerAction.AUTORENEW in allowed and server.autorenew is not Autorenew.NOT_AVAILABLE:
        shown.append("autorenew_off" if server.autorenew is Autorenew.ON else "autorenew_on")
    if ServerAction.RENEW in allowed:
        shown.append("renew")
    # The bot's own autopay is for hosters without autorenewal (spec, decision 10).
    if Capability.PAY_INVOICE in caps and ServerAction.AUTORENEW not in allowed:
        shown.append("autopay")
    if Capability.ORDER in caps:
        shown.append("order_same")
    return shown


def server_card(
    v: View, data: AccountData, server: Server, usage: Usage | None, own_days: int | None = None
) -> Screen:
    """`own_days`: this is hostwatch's own server, warned about that many days earlier."""
    autorenew = v.t(f"autorenew.{server.autorenew.value.replace('/', '')}")
    lines = [
        f"<b>{escape(server.name)}</b> · {escape(data.title)}",
        v.t("card.kind", kind=v.t(f"servers.kind.{server.kind.value}")),
        v.t("card.ip", ip=server.ip or "—"),
        v.t("card.plan", plan=escape(server.plan or "—")),
        v.t(
            "card.status",
            status=v.t(f"status.{server.status.value}"),
            raw=escape(server.raw_status),
        ),
        v.t("card.price", price=_price(v, server)),
    ]
    if server.next_charge_at is not None:
        key = "card.next_charge" if _renews(server) else "card.ends"
        lines.append(v.t(key, date=v.day(server.next_charge_at)))
    lines.append(v.t("card.autorenew", value=autorenew))
    lines.append(v.t("card.usage", value=_usage(v, data, usage)))
    if data.servers_at is not None:
        lines.append(v.t("card.data_at", date=v.moment(data.servers_at)))
    if own_days is not None:
        lines += ["", v.t("card.own", days=own_days)]
    if _failed(data):
        lines += ["", _failed_line(v, data)]

    kb = InlineKeyboardBuilder()
    acc, sid = data.account.id, server.external_id
    for action in server_actions(data, server):
        kb.button(
            text=v.t(f"action.{action}"), callback_data=ActionCb(acc=acc, sid=sid, act=action)
        )
    kb.adjust(3)
    kb.row(_button(v.t("button.refresh"), RefreshCb(acc=acc, view="server", sid=sid)))
    kb.row(_back(v, "servers"))
    return Screen("\n".join(lines), kb.as_markup())


# Balance


def balance(v: View, accounts: list[AccountData]) -> Screen:
    lines = [v.t("balance.title")]
    kb = InlineKeyboardBuilder()
    live = [d for d in accounts if d.account.status is not AccountStatus.DISABLED]
    for data in live:
        lines.append("")
        title = escape(data.title)
        if _failed(data):
            lines.append(_failed_line(v, data))
        if data.balance is None or data.balance_at is None:
            if not _failed(data):
                lines.append(v.t("account.no_data", title=title))
            continue
        money = data.balance.money
        lines.append(v.t("balance.line", title=title, amount=money, date=v.moment(data.balance_at)))
        if data.balance.hours_left is not None:
            lines.append(v.t("balance.hours_left", hours=f"{data.balance.hours_left:g}"))
        if data.forecast is not None:
            lines.append(escape(data.forecast.explain(v.lang, v.tz)))
        if Capability.TOPUP in data.caps:
            kb.row(_button(v.t("balance.topup", title=data.title), TopupCb(acc=data.account.id)))
    if not live:
        lines += ["", v.t("summary.empty")]
    else:
        kb.row(_button(v.t("button.refresh"), RefreshCb(acc=0, view="balance")))
    kb.row(_back(v))
    return Screen("\n".join(lines), kb.as_markup())


# Integrations


def account_status(v: View, status: AccountStatus) -> str:
    return v.t(f"account.status.{status.value}")


def integrations(v: View, plugins: list[type[Plugin]], accounts: list[AccountData]) -> Screen:
    lines = [v.t("integrations.title")]
    kb = InlineKeyboardBuilder()
    for plugin in plugins:
        own = [d for d in accounts if d.account.plugin_id == plugin.meta.id]
        lines.append("")
        if not own:
            lines.append(v.t("integrations.not_connected", hoster=escape(plugin.meta.name)))
        for data in own:
            lines.append(
                v.t(
                    "integrations.account",
                    title=escape(data.title),
                    status=account_status(v, data.account.status),
                )
            )
            kb.row(
                _button(
                    f"{data.title} · {account_status(v, data.account.status)}",
                    AccountCb(acc=data.account.id, op="card"),
                )
            )
        add = v.t("integrations.add", hoster=plugin.meta.name)
        kb.row(_button(add, AddCb(plugin=plugin.meta.id)))
    kb.row(_back(v))
    return Screen("\n".join(lines), kb.as_markup())


def account_card(v: View, data: AccountData) -> Screen:
    account = data.account
    lines = [
        f"<b>{escape(data.title)}</b>",
        v.t("account.card.status", status=account_status(v, account.status)),
        v.t(
            "account.card.last_ok",
            date=v.moment(account.last_ok_at) if account.last_ok_at else v.t("account.never"),
        ),
    ]
    if account.last_error and account.status is not AccountStatus.OK:
        lines.append(v.t("account.card.error", reason=escape(reason(v, account.last_error))))
    if data.servers_at is not None:
        lines.append(v.t("account.card.servers", count=len(data.servers)))

    kb = InlineKeyboardBuilder()
    acc = account.id
    if account.status is not AccountStatus.DISABLED and not data.plugin.login_only:
        kb.row(_button(v.t("button.refresh"), RefreshCb(acc=acc, view="account")))
    if any(isinstance(auth, BrowserAuth) for auth in data.plugin.meta.auth):
        kb.row(_button(v.t("account.relogin"), AccountCb(acc=acc, op="relogin")))
    if key_fields(data.plugin):
        kb.row(_button(v.t("account.change_key"), AccountCb(acc=acc, op="key")))
    if account.status is AccountStatus.DISABLED:
        kb.row(_button(v.t("account.enable"), AccountCb(acc=acc, op="enable")))
    else:
        kb.row(_button(v.t("account.disable"), AccountCb(acc=acc, op="disable")))
    kb.row(_button(v.t("account.delete"), AccountCb(acc=acc, op="delete")))
    kb.row(_back(v, "integrations"))
    return Screen("\n".join(lines), kb.as_markup())


def confirm_delete(v: View, data: AccountData) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(
        _button(v.t("button.yes_delete"), AccountCb(acc=data.account.id, op="delete_yes")),
        _button(v.t("button.cancel"), AccountCb(acc=data.account.id, op="card")),
    )
    return Screen(v.t("account.delete_confirm", title=escape(data.title)), kb.as_markup())


def ask_label(v: View, plugin_name: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(_button(v.t("integrations.label_default", name=plugin_name), LabelCb()))
    kb.row(_button(v.t("button.cancel"), Nav(to="integrations")))
    return Screen(v.t("integrations.ask_label", hoster=escape(plugin_name)), kb.as_markup())


def ask_key(v: View, plugin_name: str, field: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(_button(v.t("button.cancel"), Nav(to="integrations")))
    return Screen(
        v.t("integrations.ask_key", hoster=escape(plugin_name), field=escape(field)),
        kb.as_markup(),
    )


# Journal


def journal(
    v: View, entries: list[JournalEntry], page: int, titles: dict[int, str], has_more: bool
) -> Screen:
    lines = [v.t("journal.title")]
    if not entries:
        lines += ["", v.t("journal.empty")]
    for entry in entries:
        hoster = titles.get(entry.account_id, "—") if entry.account_id is not None else "—"
        lines.append(
            v.t(
                "journal.line",
                date=v.moment(entry.at),
                actor=v.t(f"journal.actor.{entry.actor}"),
                hoster=escape(hoster),
                action=escape(entry.action),
                result=v.t("journal.ok" if entry.ok else "journal.failed"),
            )
        )
    kb = InlineKeyboardBuilder()
    nav = []
    if page > 0:
        nav.append(_button("←", JournalCb(page=page - 1)))
    if has_more:
        nav.append(_button("→", JournalCb(page=page + 1)))
    if nav:
        kb.row(*nav)
    kb.row(_back(v))
    return Screen("\n".join(lines), kb.as_markup())


# Settings and first run


def settings(v: View, timezone: str, currency: str, thresholds: dict[str, str]) -> Screen:
    lines = [
        v.t("settings.title"),
        "",
        v.t("settings.language", value=LANGUAGE_NAMES[v.lang]),
        v.t("settings.timezone", value=timezone),
        v.t("settings.currency", value=currency),
        "",
        v.t("settings.thresholds"),
    ]
    lines += [v.t(f"threshold.{key}", value=value) for key, value in thresholds.items()]
    lines += ["", v.t("settings.thresholds_note")]
    kb = InlineKeyboardBuilder()
    kb.row(
        _button(v.t("settings.button.language"), SetCb(key="lang")),
        _button(v.t("settings.button.timezone"), SetCb(key="tz")),
        _button(v.t("settings.button.currency"), SetCb(key="cur")),
    )
    for item in THRESHOLDS:
        kb.row(_button(v.t(f"threshold.button.{item.key}"), SetCb(key="th", value=item.key)))
    kb.row(_button(v.t("settings.button.backup"), SetCb(key="backup")))
    kb.row(_back(v))
    return Screen("\n".join(lines), kb.as_markup())


def choose_language(v: View, onb: bool) -> Screen:
    kb = InlineKeyboardBuilder()
    for lang in LANGUAGES:
        kb.button(text=LANGUAGE_NAMES[lang], callback_data=SetCb(key="lang", value=lang, onb=onb))
    if not onb:
        kb.row(_back(v, "settings"))
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
        kb.row(_back(v, "settings"))
    return Screen(v.t("onboarding.timezone", suggested=suggested), kb.as_markup())


def choose_currency(v: View, onb: bool) -> Screen:
    kb = InlineKeyboardBuilder()
    for currency in CURRENCIES:
        kb.button(text=currency, callback_data=SetCb(key="cur", value=currency, onb=onb))
    if not onb:
        kb.row(_back(v, "settings"))
    return Screen(v.t("onboarding.currency"), kb.as_markup())


def ask_threshold(v: View, key: str, current: str) -> Screen:
    kb = InlineKeyboardBuilder()
    kb.row(_button(v.t("button.cancel"), Nav(to="settings")))
    return Screen(v.t(f"threshold.ask.{key}", value=current), kb.as_markup())
