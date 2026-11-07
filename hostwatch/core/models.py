"""Data that plugins return and the core stores. Money is always `Decimal`, never float."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, Strict

_SYMBOLS_BEFORE = {"EUR": "€", "USD": "$"}
_SYMBOLS_AFTER = {"RUB": "₽"}


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Money(Model):
    amount: Annotated[Decimal, Strict()]
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str) -> Self:
        return cls(amount=Decimal(amount), currency=currency)

    def __add__(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(amount=self.amount * factor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check_currency(other)
        return self.amount >= other.amount

    def __str__(self) -> str:
        amount = f"{self.amount.quantize(Decimal('0.01')):,}".replace(",", " ")
        if self.currency in _SYMBOLS_BEFORE:
            sign, amount = ("-", amount[1:]) if amount.startswith("-") else ("", amount)
            return f"{sign}{_SYMBOLS_BEFORE[self.currency]}{amount}"
        symbol = _SYMBOLS_AFTER.get(self.currency, self.currency)
        return f"{amount} {symbol}"

    def _check_currency(self, other: Money) -> None:
        if other.currency != self.currency:
            raise ValueError(f"currency mismatch: {self.currency} and {other.currency}")


class ServerKind(StrEnum):
    VPS = "vps"
    DEDICATED = "dedicated"


class ServerStatus(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    SUSPENDED = "suspended"
    UNPAID = "unpaid"
    UNKNOWN = "unknown"


class BillingModel(StrEnum):
    HOURLY = "hourly"
    PERIODIC = "periodic"


class Autorenew(StrEnum):
    ON = "on"
    OFF = "off"
    NOT_AVAILABLE = "n/a"


class PowerAction(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"


class ServerAction(StrEnum):
    """What the hoster allows for one server. The bot also checks the plugin capability."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    AUTORENEW = "autorenew"
    RENEW = "renew"


class Server(Model):
    external_id: str
    name: str
    kind: ServerKind
    ip: str | None = None
    plan: str | None = None
    status: ServerStatus
    raw_status: str
    # For hourly billing the price is per hour and there is no period.
    price: Money | None = None
    period_months: int | None = Field(default=None, ge=1)
    next_charge_at: AwareDatetime | None = None
    autorenew: Autorenew
    billing: BillingModel
    actions: frozenset[ServerAction] = frozenset()


class Balance(Model):
    money: Money
    # Some hosters (Timeweb) compute how long the balance lasts themselves.
    hours_left: float | None = None


class Usage(Model):
    server_id: str
    measured_at: AwareDatetime
    cpu_percent: float | None = None
    ram_percent: float | None = None
    disk_percent: float | None = None
    traffic_gb: float | None = None


class InvoiceStatus(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class Invoice(Model):
    id: str
    amount: Money
    status: InvoiceStatus
    description: str = ""
    due_at: AwareDatetime | None = None
    server_id: str | None = None


class TopupMethod(Model):
    id: str
    name: str
    minimum: Money | None = None


class Payment(Model):
    """A top-up invoice the hoster created; the owner pays it by the link."""

    id: str
    amount: Money
    method: str
    url: str
    created_at: AwareDatetime
    qr_data: str | None = None


class Offer(Model):
    id: str
    name: str
    location: str
    price: Money
    period_months: int = Field(ge=1)
    os: tuple[str, ...]


class OrderRequest(Model):
    offer_id: str
    os: str
    period_months: int = Field(ge=1)


class ActionResult(Model):
    ok: bool
    # The hoster's answer without secrets, for the journal.
    detail: str = ""


class AccountStatus(StrEnum):
    OK = "ok"
    RELOGIN = "relogin"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Account:
    id: int
    plugin_id: str
    label: str
    status: AccountStatus
    last_error: str | None
    last_ok_at: datetime | None
