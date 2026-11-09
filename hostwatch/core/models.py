"""Data that plugins return and the core stores. Money is always `Decimal`, never float."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, Strict, model_validator

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
    # Average network speed over the hoster's last measurement window.
    net_in_mbits: float | None = None
    net_out_mbits: float | None = None


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


class TopupKind(StrEnum):
    SBP = "sbp"
    CRYPTO = "crypto"


class TopupMethod(Model):
    """A way to top up the balance. `id` goes into callback data: keep it under 30 bytes.

    A crypto method may name its `coin` («USDT») and `network` («TRC20»): the bot then asks
    for the coin first and for the network only when the coin has several.
    """

    id: str = Field(max_length=30)
    name: str
    kind: TopupKind
    # In the account currency.
    minimum: Money | None = None
    coin: str | None = Field(default=None, max_length=16)
    network: str | None = None


class PayOption(Model):
    """A coin on a network that an invoice's payment page takes (`SupportsPayOptions`).

    `id` goes into callback data: short, without `|`. `network` is the page's own label
    («TRON (TRC-20)»).
    """

    id: str = Field(max_length=25, pattern=r"^[^|]+$")
    coin: str = Field(max_length=16)
    network: str


class Payment(Model):
    """A top-up invoice the hoster created; the owner pays it by the link or the address.

    `url` opens the payment: a `qr.nspk.ru` link for SBP, the payment page otherwise.
    For crypto paid by hand there may be only `address` with the amount to send.
    """

    id: str
    amount: Money
    method: str
    created_at: AwareDatetime
    url: str | None = None
    # What the QR picture encodes; a crypto QR holds the address and the amount.
    qr_data: str | None = None
    address: str | None = None
    # How much to send in the payment's own currency ("6.52", "USDT (TRC20)").
    pay_amount: Decimal | None = None
    pay_currency: str | None = None
    expires_at: AwareDatetime | None = None
    # Coins and networks still to choose from when the hoster's page asks after the invoice.
    options: tuple[PayOption, ...] = ()

    @model_validator(mode="after")
    def _payable(self) -> Self:
        if not self.url and not self.address:
            raise ValueError("a payment needs a link or an address")
        return self


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
