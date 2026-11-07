"""`hostwatch record`: save a plugin's real HTTP answers as test fixtures, scrubbed.

Every response the plugin receives is captured. Before anything is written, the scrubber
replaces secrets, emails, IPv4 addresses, ids and the names the plugin lists in
`Plugin.record_names` with stable placeholders, so the same id stays the same everywhere.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from hostwatch.core.logs import MIN_SECRET_LENGTH

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Checked with `ipaddress` before replacing: the pattern alone also matches times.
IPV6_CANDIDATE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
DIGITS = re.compile(r"\d+")
# A known id inside a string; shorter numbers are too likely to be something else.
EMBEDDED_ID = re.compile(r"(?<!\d)\d{5,}(?!\d)")
# Values under these keys are dropped whatever they look like.
SENSITIVE = re.compile(
    r"(?i)(email|phone|password|passwd|_pass|token|secret|cookie|photo|avatar|pin)$"
)
# Account money: balances, payments, charges, spending. Tariff prices (`price`, `prices`)
# are public and stay as they are.
MONEY = re.compile(r"(?i)(balance|amount|replenished|paid|debt|cost)$")
FIRST_FAKE_ID = 100001
FIRST_FAKE_MONEY = 1000
FAKE_MONEY_STEP = 125
MASK = "***"
PLACEHOLDER_EMAIL = "user@example.com"


@dataclass(frozen=True)
class Exchange:
    method: str
    url: httpx.URL
    status: int
    body: bytes


class Recorder:
    """Collects responses through an httpx event hook."""

    def __init__(self) -> None:
        self.exchanges: list[Exchange] = []

    def attach(self, client: httpx.AsyncClient) -> httpx.AsyncClient:
        hooks = client.event_hooks
        hooks["response"].append(self._on_response)
        client.event_hooks = hooks
        return client

    async def _on_response(self, response: httpx.Response) -> None:
        body = await response.aread()
        request = response.request
        self.exchanges.append(Exchange(request.method, request.url, response.status_code, body))


class Scrubber:
    def __init__(self, secrets: Iterable[str], names: Iterable[str] = ()) -> None:
        self._secrets = sorted(
            (s for s in secrets if len(s) >= MIN_SECRET_LENGTH), key=len, reverse=True
        )
        self._names = [tuple(path.split(".")) for path in names]
        self._ids: dict[str, int] = {}
        self._ips: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._money: dict[Decimal, int] = {}

    def exchanges(self, exchanges: Iterable[Exchange]) -> list[dict[str, Any]]:
        """Scrub all responses. Ids are collected first, so an id that shows up inside
        a string ("manual:424242") before its own field is replaced too."""
        exchanges = list(exchanges)
        for exchange in exchanges:
            for part in exchange.url.path.split("/"):
                if part.isdigit():
                    self.fake_id(part)
            self._collect_ids(_parse(exchange.body), ())
        return [self.exchange(exchange) for exchange in exchanges]

    def exchange(self, exchange: Exchange) -> dict[str, Any]:
        url = exchange.url
        record: dict[str, Any] = {
            "method": exchange.method,
            "url": f"{url.scheme}://{url.host}{self.path(url.path)}",
            "query": {key: self.text(value) for key, value in url.params.multi_items()},
            "status": exchange.status,
        }
        try:
            record["json"] = self.value(json.loads(exchange.body), ())
        except ValueError:
            record["text"] = self.text(exchange.body.decode("utf-8", "replace"))
        return record

    def path(self, path: str) -> str:
        """Numbers in a URL path are ids: /services/7777777/stats -> /services/100001/stats."""
        return "/".join(
            str(self.fake_id(part)) if part.isdigit() else self.text(part)
            for part in path.split("/")
        )

    def value(self, value: Any, path: tuple[str, ...]) -> Any:
        key = path[-1] if path else ""
        if isinstance(value, dict):
            return {k: self.value(v, (*path, k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.value(v, (*path, "*")) for v in value]
        if value is None or isinstance(value, bool):
            return value
        if SENSITIVE.search(key):
            return MASK
        if self._is_name(path) and isinstance(value, str):
            return self._label(key, value)
        if MONEY.search(key):
            return self.fake_money(value)
        if _is_id_key(path) and _looks_like_id(value):
            fake = self.fake_id(str(value))
            return fake if isinstance(value, int) else str(fake)
        if isinstance(value, str):
            return self.text(value)
        return value

    def text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        text = EMBEDDED_ID.sub(lambda m: str(self._ids.get(m.group(), m.group())), text)
        text = EMAIL.sub(PLACEHOLDER_EMAIL, text)
        text = IPV4.sub(lambda m: self._fake_ip(m.group()), text)
        return IPV6_CANDIDATE.sub(lambda m: self._fake_ipv6(m.group()), text)

    def _collect_ids(self, value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                self._collect_ids(v, (*path, k))
        elif isinstance(value, list):
            for v in value:
                self._collect_ids(v, (*path, "*"))
        elif not isinstance(value, bool) and _is_id_key(path) and _looks_like_id(value):
            self.fake_id(str(value))

    def fake_money(self, real: Any) -> Any:
        """A made-up sum of the same type and format: 4810 -> 1000, -487.8 -> -1125.5,
        "12.30" -> "1250.00". Zero and the sign stay, equal sums stay equal."""
        if not isinstance(real, int | float | str):
            return real
        try:
            amount = Decimal(str(real))
        except InvalidOperation:
            return self.text(real) if isinstance(real, str) else real
        if not amount.is_finite() or amount == 0:
            return real
        fake = Decimal(
            self._money.setdefault(
                abs(amount), FIRST_FAKE_MONEY + FAKE_MONEY_STEP * len(self._money)
            )
        ).copy_sign(amount)
        if isinstance(real, int):
            return int(fake)
        if isinstance(real, float):
            return float(fake + (Decimal("0.5").copy_sign(amount) if amount % 1 else 0))
        exponent = amount.as_tuple().exponent
        return (
            str(fake.quantize(Decimal(1).scaleb(exponent)))
            if isinstance(exponent, int)
            else str(fake)
        )

    def fake_id(self, real: str) -> int:
        return self._ids.setdefault(real, FIRST_FAKE_ID + len(self._ids))

    def _fake_ip(self, real: str) -> str:
        # TEST-NET-1 (RFC 5737): documentation addresses that route nowhere.
        return self._ips.setdefault(real, f"192.0.2.{10 + len(self._ips)}")

    def _fake_ipv6(self, candidate: str) -> str:
        try:
            ipaddress.IPv6Address(candidate)
        except ValueError:
            return candidate  # a time like 12:30:00, not an address
        # 2001:db8::/32 (RFC 3849) is reserved for documentation.
        return self._ips.setdefault(candidate, f"2001:db8::{10 + len(self._ips):x}")

    def _label(self, key: str, real: str) -> str:
        return self._labels.setdefault(real, f"{key}-{len(self._labels) + 1}")

    def _is_name(self, path: tuple[str, ...]) -> bool:
        return any(len(path) >= len(p) and _matches(path[-len(p) :], p) for p in self._names)


def _matches(path: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    return all(want in ("*", got) for got, want in zip(path, pattern, strict=True))


def _is_id_key(path: tuple[str, ...]) -> bool:
    """`id`, `serviceId`, `service_id`, and items of `orderIds` / `order_ids`."""
    key = path[-1] if path else ""
    if key == "*" and len(path) >= 2:
        parent = path[-2]
        return parent.endswith(("Ids", "_ids")) or parent == "ids"
    return key == "id" or key.endswith(("Id", "_id", "ID"))


def _parse(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


def _looks_like_id(value: object) -> bool:
    """Zero is kept: hosters use it for "no id"."""
    if isinstance(value, int):
        return value != 0
    return isinstance(value, str) and DIGITS.fullmatch(value) is not None and value.strip("0") != ""


def write_fixtures(records: list[dict[str, Any]], folder: Path) -> list[Path]:
    """Replace the folder's old fixtures with these, one file per response, in call order."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.json"):
        old.unlink()
    written = []
    for number, record in enumerate(records, start=1):
        slug = re.sub(r"[^a-z0-9]+", "_", httpx.URL(record["url"]).path.lower()).strip("_")
        path = folder / f"{number:02d}-{record['method'].lower()}-{slug}.json"
        text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
