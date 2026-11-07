from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from hostwatch.core.models import Money
from hostwatch.core.rates import CBR_URL, FALLBACK_URL, CbrRates, RatesUnavailable

CBR_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="07.11.2026" name="Foreign Currency Market">
<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal>
<Name>Доллар США</Name><Value>80,5000</Value><VunitRate>80,5</VunitRate></Valute>
<Valute ID="R01239"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal>
<Name>Евро</Name><Value>92,0000</Value><VunitRate>92</VunitRate></Valute>
<Valute ID="R01335"><NumCode>398</NumCode><CharCode>KZT</CharCode><Nominal>100</Nominal>
<Name>Тенге</Name><Value>16,0000</Value><VunitRate>0,16</VunitRate></Valute>
</ValCurs>""".encode("cp1251")

FALLBACK_JSON = """{
  "Date": "2026-11-06T11:30:00+03:00",
  "Valute": {"EUR": {"CharCode": "EUR", "Nominal": 1, "Value": 91.5}}
}"""


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@respx.mock
async def test_parses_cbr_and_converts() -> None:
    respx.get(CBR_URL).respond(content=CBR_XML)
    async with httpx.AsyncClient() as http:
        rates = await CbrRates(http).get()

    assert rates.date == date(2026, 11, 7)
    assert rates.rub_per_unit["KZT"] == Decimal("0.16")
    assert rates.convert(Money.of("10", "EUR"), "RUB") == Money.of("920.00", "RUB")
    assert rates.convert(Money.of("92", "EUR"), "USD") == Money.of("105.14", "USD")
    assert rates.convert(Money.of("1", "XYZ"), "RUB") is None


@respx.mock
async def test_falls_back_to_second_source() -> None:
    respx.get(CBR_URL).respond(status_code=503)
    respx.get(FALLBACK_URL).respond(text=FALLBACK_JSON)
    async with httpx.AsyncClient() as http:
        rates = await CbrRates(http).get()

    assert rates.date == date(2026, 11, 6)
    assert rates.rub_per_unit["EUR"] == Decimal("91.5")


@respx.mock
async def test_caches_for_a_day_and_keeps_stale_rates_on_failure() -> None:
    cbr = respx.get(CBR_URL).respond(content=CBR_XML)
    clock = Clock()
    async with httpx.AsyncClient() as http:
        source = CbrRates(http, now=clock)
        first = await source.get()
        await source.get()
        assert cbr.call_count == 1

        clock.now += timedelta(days=1, minutes=1)
        cbr.respond(status_code=500)
        respx.get(FALLBACK_URL).respond(text="not json")
        stale = await source.get()

    assert cbr.call_count == 2
    assert stale is first


@respx.mock
async def test_no_rates_at_all() -> None:
    respx.get(CBR_URL).mock(side_effect=httpx.ConnectError("down"))
    respx.get(FALLBACK_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(RatesUnavailable):
            await CbrRates(http).get()
