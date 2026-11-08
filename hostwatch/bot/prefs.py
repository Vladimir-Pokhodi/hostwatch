"""Owner preferences kept in the `settings` table: language, time zone, currency, thresholds."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hostwatch.core.alerts import AlertSettings, QuietHours, Thresholds
from hostwatch.storage.repositories import SettingsRepository

LANGUAGES = ("ru", "en")
CURRENCIES = ("RUB", "EUR", "USD")
DEFAULT_LANG = "ru"
DEFAULT_TZ = {"ru": "Europe/Moscow", "en": "UTC"}
TIMEZONES = (
    "Europe/Kaliningrad",
    "Europe/Moscow",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Novosibirsk",
    "Asia/Vladivostok",
    "Europe/Berlin",
    "Europe/London",
    "UTC",
)

_TIME = r"([01]\d|2[0-3]):[0-5]\d"


def _percent(value: str) -> str | None:
    return value if value.isdigit() and 1 <= int(value) <= 100 else None


def _days(value: str) -> str | None:
    return value if value.isdigit() and 1 <= int(value) <= 60 else None


def _time(value: str) -> str | None:
    return value if re.fullmatch(_TIME, value) else None


def _time_range(value: str) -> str | None:
    value = value.replace(" ", "").replace("–", "-")
    return value if re.fullmatch(f"{_TIME}-{_TIME}", value) else None


@dataclass(frozen=True)
class Threshold:
    key: str
    default: str
    # Returns the normalised value, or None when the text is not valid.
    parse: Callable[[str], str | None]


THRESHOLDS = (
    Threshold("balance_days", "5", _days),
    Threshold("load_percent", "90", _percent),
    Threshold("disk_percent", "90", _percent),
    Threshold("quiet_hours", "23:00-08:00", _time_range),
    Threshold("summary_time", "09:00", _time),
)
THRESHOLD_BY_KEY = {item.key: item for item in THRESHOLDS}


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


class Prefs:
    def __init__(self, settings: SettingsRepository) -> None:
        self._settings = settings

    @property
    def onboarded(self) -> bool:
        return self._settings.get("onboarded") == "1"

    def finish_onboarding(self) -> None:
        self._settings.set("onboarded", "1")

    @property
    def lang(self) -> str:
        value = self._settings.get("lang", DEFAULT_LANG)
        return value if value in LANGUAGES else DEFAULT_LANG

    @lang.setter
    def lang(self, value: str) -> None:
        if value not in LANGUAGES:
            raise ValueError(f"unknown language {value!r}")
        self._settings.set("lang", value)

    @property
    def timezone(self) -> str:
        value = self._settings.get("timezone") or DEFAULT_TZ[self.lang]
        return value if valid_timezone(value) else "UTC"

    @timezone.setter
    def timezone(self, value: str) -> None:
        if not valid_timezone(value):
            raise ValueError(f"unknown time zone {value!r}")
        self._settings.set("timezone", value)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def currency(self) -> str:
        value = self._settings.get("currency", "RUB")
        return value if value in CURRENCIES else "RUB"

    @currency.setter
    def currency(self, value: str) -> None:
        if value not in CURRENCIES:
            raise ValueError(f"unknown currency {value!r}")
        self._settings.set("currency", value)

    def threshold(self, key: str) -> str:
        item = THRESHOLD_BY_KEY[key]
        return self._settings.get(f"threshold.{key}", item.default) or item.default

    def set_threshold(self, key: str, text: str) -> bool:
        """Save a threshold typed by the owner. False if the text is not a valid value."""
        value = THRESHOLD_BY_KEY[key].parse(text.strip())
        if value is None:
            return False
        self._settings.set(f"threshold.{key}", value)
        return True

    def _number(self, key: str) -> int:
        value = self.threshold(key)
        return int(value) if value.isdigit() else int(THRESHOLD_BY_KEY[key].default)

    def alert_settings(self, own_ip: str | None = None) -> AlertSettings:
        return AlertSettings(
            lang=self.lang,
            tz=self.tz,
            thresholds=Thresholds(
                balance_days=self._number("balance_days"),
                load_percent=self._number("load_percent"),
                disk_percent=self._number("disk_percent"),
            ),
            quiet=QuietHours.parse(self.threshold("quiet_hours")),
            own_ip=own_ip,
        )

    @property
    def summary_at(self) -> time:
        value = self.threshold("summary_time")
        if _time(value) is None:
            value = THRESHOLD_BY_KEY["summary_time"].default
        hours, minutes = value.split(":")
        return time(int(hours), int(minutes))

    @property
    def order_limit(self) -> Decimal:
        """Orders a month in the owner's currency; above it an order takes a second «Yes».
        0 by default: every order is asked twice until the owner sets a limit."""
        try:
            value = Decimal(self._settings.get("order.limit") or "0")
        except InvalidOperation:
            return Decimal(0)
        return value if value.is_finite() and value >= 0 else Decimal(0)

    @order_limit.setter
    def order_limit(self, value: Decimal) -> None:
        if not value.is_finite() or value < 0:
            raise ValueError("the limit must be a sum of 0 or more")
        self._settings.set("order.limit", str(value))

    @property
    def ssh_key(self) -> str | None:
        """The owner's public SSH key, put on new servers where the hoster can."""
        return self._settings.get("order.ssh_key") or None

    @ssh_key.setter
    def ssh_key(self, value: str | None) -> None:
        self._settings.set("order.ssh_key", value or "")
