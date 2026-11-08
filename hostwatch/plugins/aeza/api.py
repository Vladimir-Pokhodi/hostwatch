"""Aeza API answers (`https://my.aeza.net/api/v2/`). Amounts are in cents of the account currency.

Only the fields hostwatch uses are declared; types are checked strictly, so a changed field
fails validation and the plugin reports `LayoutChanged` instead of guessing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

PaymentTerm = Literal["hour", "month", "quarter_year", "half_year", "year"]


class Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)


class Account(Answer):
    balance: Decimal
    currency: str


class Service(Answer):
    id: int
    name: str
    ip: str | None
    status: str
    typeSlug: str
    productName: str | None
    paymentTerm: PaymentTerm
    price: Decimal
    autoProlong: bool
    expiresAt: AwareDatetime | None
    capabilities: list[str]


class Services(Answer):
    items: list[Service]
    total: int


class Transaction(Answer):
    id: int
    amount: Decimal
    status: str
    type: str
    serviceId: int | None
    createdAt: AwareDatetime
    performedAt: AwareDatetime


class Transactions(Answer):
    items: list[Transaction]
    total: int


class Stats(Answer):
    # [[unix time, value], ...]; a missing measurement comes as null.
    data: list[tuple[int, float | None]]


class Problem(Answer):
    """Error body: {"statusCode": 400, "message": "...", ...}."""

    message: str = ""


class PaymentMethod(Answer):
    """One item of `GET /billing/payment-methods`; `min` is in cents."""

    slug: str
    name: str
    group: str
    min: int


class PaymentMethods(Answer):
    items: list[PaymentMethod]


class InvoicePayload(Answer):
    """`url` of the ЮKassa page for SBP; address, amount and QR text for crypto."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=False)

    url: str | None = None
    address: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    qr: str | None = None


class CreatedInvoice(Answer):
    """Answer of `POST /billing/invoices`."""

    id: int
    status: str
    amount: int
    createdAt: AwareDatetime
    payload: InvoicePayload


class RenewalPrices(Answer):
    """`GET services/{id}/prices`: what one more period costs, in cents, by payment term."""

    prices: dict[str, Decimal]
