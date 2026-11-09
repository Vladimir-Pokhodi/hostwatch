"""The data of the Mini App (1.1, part 5), counted the same way the chat counts it, and the
«📊 Analytics» button that opens it."""

from __future__ import annotations

import logging
from decimal import Decimal
from html import unescape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonWebApp, WebAppInfo

from hostwatch.core.alerts import Thresholds
from hostwatch.core.history import MoneySource
from hostwatch.core.models import Money
from hostwatch.core.rates import Rates
from hostwatch.locales import t
from hostwatch.storage.history import HistoryRepository, MonthTotal
from hostwatch.web.miniapp import (
    Amount,
    HosterOut,
    Overview,
    ProblemOut,
    SpendOut,
    Sum,
    TotalsOut,
)

from . import views
from .hub import AccountData
from .views import Level, View

log = logging.getLogger(__name__)

LEVEL_NAME = {Level.OK: "ok", Level.WARN: "warn", Level.BAD: "bad"}


def app_url(domain: str | None) -> str | None:
    """The Mini App address; it needs a domain: Telegram opens only https pages by name."""
    return f"https://{domain}/app/" if domain else None


async def set_menu_button(bot: Bot, owner_id: int, url: str, lang: str) -> None:
    """The button left of the input field in the owner's chat opens the Mini App."""
    button = MenuButtonWebApp(text=t("webapp.button", lang), web_app=WebAppInfo(url=url))
    try:
        await bot.set_chat_menu_button(chat_id=owner_id, menu_button=button)
    except TelegramAPIError as exc:
        log.warning("cannot set the menu button: %s", exc.message)


def _amount(money: Money) -> Amount:
    return Amount(amount=str(money.amount), currency=money.currency)


def _sum(amounts: list[Money], rates: Rates | None, currency: str) -> Sum:
    by_currency: dict[str, Money] = {}
    for money in amounts:
        current = by_currency.get(money.currency)
        by_currency[money.currency] = money if current is None else current + money
    parts = list(by_currency.values())
    total = None
    if parts and rates is not None:
        total = views._total(parts, rates, currency)
    elif len(parts) == 1 and parts[0].currency == currency:
        total = parts[0]
    return Sum(total=None if total is None else _amount(total), parts=[_amount(m) for m in parts])


def _charges(months: list[MonthTotal], month: str, account_id: int | None = None) -> list[Money]:
    return [
        Money(amount=m.charge, currency=m.currency)
        for m in months
        if m.month == month
        and m.charge != Decimal(0)
        and (account_id is None or m.account_id == account_id)
    ]


def _previous(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def _hoster(
    v: View,
    data: AccountData,
    found: list[views.Problem],
    spend: list[Money],
    rates: Rates | None,
    currency: str,
    th: Thresholds,
) -> HosterOut:
    level = views._account_level(data, found)
    money_level = views._money_level(v, data, th)
    if views._failed(data):
        level = money_level = Level.BAD
    f = data.forecast
    return HosterOut(
        account_id=data.account.id,
        title=data.title,
        plugin=data.plugin.meta.id,
        status=data.account.status.value,
        level=LEVEL_NAME[level],
        balance=None if data.balance is None else _amount(data.balance.money),
        balance_at=data.balance_at,
        lasts=views._lasts(v, data),
        money_level=LEVEL_NAME[money_level],
        runs_out_at=None if f is None else f.runs_out_at,
        monthly=_sum(views.monthly_cost(data.servers), rates, currency),
        servers=len(data.servers),
        spend_month=_sum(spend, rates, currency),
    )


def build_overview(
    v: View,
    accounts: list[AccountData],
    rates: Rates | None,
    currency: str,
    history: HistoryRepository | None,
    th: Thresholds | None = None,
) -> Overview:
    th = th or Thresholds()
    live = views._live(accounts)
    found = views.problems(v, accounts, th)
    month = v.now.astimezone(v.tz).strftime("%Y-%m")
    previous = _previous(month)
    months = []
    if history is not None:
        months = [
            m
            for m in history.money_by_month(MoneySource.OBSERVED, v.tz)
            if m.month in (month, previous)
        ]
    titles = {d.account.id: d.title for d in accounts}
    names = {(d.account.id, s.external_id): s.name for d in accounts for s in d.servers}
    problems = [
        ProblemOut(
            level="bad" if p.level is Level.BAD else "warn",
            account_id=p.account_id,
            hoster=titles.get(p.account_id, ""),
            server=names.get((p.account_id, p.server_id)) if p.server_id else None,
            what=unescape(p.what),
        )
        for p in found
    ]
    balances = [d.balance.money for d in live if d.balance is not None]
    costs = [money for d in live for money in views.monthly_cost(d.servers)]
    return Overview(
        lang=v.lang,
        currency=currency,
        tz=getattr(v.tz, "key", None) or str(v.tz),
        generated_at=v.now,
        rates_date=None if rates is None else rates.date.isoformat(),
        hosters_count=len(live),
        servers_count=sum(len(d.servers) for d in live),
        totals=TotalsOut(
            balances=_sum(balances, rates, currency), monthly=_sum(costs, rates, currency)
        ),
        spend=SpendOut(
            month=month,
            previous_month=previous,
            this_month=_sum(_charges(months, month), rates, currency),
            last_month=_sum(_charges(months, previous), rates, currency),
        ),
        problems=problems,
        hosters=[
            _hoster(v, d, found, _charges(months, month, d.account.id), rates, currency, th)
            for d in live
        ],
    )
