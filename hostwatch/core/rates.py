"""Bank of Russia exchange rates for totals in the owner's currency. Cached for a day."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from hostwatch.core.models import Money

log = logging.getLogger(__name__)

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
FALLBACK_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
MAX_AGE = timedelta(days=1)
CENT = Decimal("0.01")


class RatesUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Rates:
    date: date
    # Roubles per one unit of the currency.
    rub_per_unit: dict[str, Decimal]

    def convert(self, money: Money, to: str) -> Money | None:
        """None if one of the currencies is missing from the rates."""
        source = self._rate(money.currency)
        target = self._rate(to)
        if source is None or target is None:
            return None
        amount = (money.amount * source / target).quantize(CENT)
        return Money(amount=amount, currency=to)

    def _rate(self, currency: str) -> Decimal | None:
        return Decimal(1) if currency == "RUB" else self.rub_per_unit.get(currency)


class CbrRates:
    def __init__(
        self,
        http: httpx.AsyncClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self._now = now
        self._cached: Rates | None = None
        self._fetched_at: datetime | None = None

    async def get(self) -> Rates:
        """Fresh rates, or the last known ones (with their date) when both sources fail."""
        fresh = self._fetched_at is not None and self._now() - self._fetched_at < MAX_AGE
        if self._cached is not None and fresh:
            return self._cached
        for fetch in (self._from_cbr, self._from_fallback):
            try:
                rates = await fetch()
            except (httpx.HTTPError, ValueError, KeyError, ET.ParseError, InvalidOperation) as exc:
                log.warning("rates source failed: %s", type(exc).__name__)
                continue
            self._cached, self._fetched_at = rates, self._now()
            return rates
        if self._cached is not None:
            return self._cached
        raise RatesUnavailable("no exchange rates: both sources failed")

    async def _from_cbr(self) -> Rates:
        response = await self._http.get(CBR_URL)
        response.raise_for_status()
        return parse_cbr_xml(response.content)

    async def _from_fallback(self) -> Rates:
        response = await self._http.get(FALLBACK_URL)
        response.raise_for_status()
        return parse_fallback_json(response.text)


def parse_cbr_xml(content: bytes) -> Rates:
    root = ET.fromstring(content)
    day, month, year = (int(part) for part in root.attrib["Date"].split("."))
    rates = {}
    for valute in root.iter("Valute"):
        code = _text(valute, "CharCode")
        nominal = Decimal(_text(valute, "Nominal"))
        value = Decimal(_text(valute, "Value").replace(",", "."))
        rates[code] = value / nominal
    if not rates:
        raise ValueError("no rates in the answer")
    return Rates(date(year, month, day), rates)


def parse_fallback_json(text: str) -> Rates:
    data = json.loads(text, parse_float=Decimal, parse_int=Decimal)
    rates = {
        code: Decimal(item["Value"]) / Decimal(item["Nominal"])
        for code, item in data["Valute"].items()
    }
    if not rates:
        raise ValueError("no rates in the answer")
    return Rates(datetime.fromisoformat(data["Date"]).date(), rates)


def _text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None:
        raise ValueError(f"no {tag}")
    return child.text
