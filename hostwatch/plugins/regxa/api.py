"""Regxa answers (`https://regxa.com/api/...`): a Next.js front for WHMCS and Virtualizor.

Only the fields hostwatch uses are declared. WHMCS sends sums as strings ("12.50") and
Virtualizor mixes numbers and numeric strings, so those fields are parsed leniently;
everything else is strict, and a changed field makes the plugin report `LayoutChanged`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

# "12.50", 12.5 and 12 are all fine for a sum.
Sum = Annotated[Decimal, Field(strict=False)]
# Virtualizor sends the same figure as 12, 12.5 or "12.5" depending on the endpoint.
Figure = Annotated[float, Field(strict=False)]


class Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)


class SessionUser(Answer):
    client_id: str | None = None


class Session(Answer):
    """`/api/auth/session`: `{}` without a session."""

    user: SessionUser | None = None


class Client(Answer):
    credit: Sum
    currency_code: str


class Profile(Answer):
    """`/api/profile/client/{clientId}`."""

    success: bool
    data: Client


class CustomField(Answer):
    name: str
    value: str


class CustomFields(Answer):
    customfield: list[CustomField] = []


class Service(Answer):
    """One item of `/api/services/getservices?clientId=...` (a WHMCS product)."""

    id: int
    name: str
    status: str
    nextDueDate: str
    amount: Sum
    billingCycle: str
    # WHMCS gives an empty list instead of an object when there are no fields.
    customfields: CustomFields | list[Any] = []

    def field(self, name: str) -> str | None:
        if isinstance(self.customfields, CustomFields):
            for item in self.customfields.customfield:
                if item.name == name:
                    return item.value
        return None


class VpsDetails(Answer):
    """`/api/services/vps/details/{vpsid}`."""

    hostname: str = ""
    suspended: Annotated[bool, Field(strict=False)] = False
    # {ip record id: address}, IPv4 and IPv6 mixed. WHMCS-style empty list when none.
    ips: dict[str, str] | list[Any] = {}


class VpsStatus(Answer):
    """`/api/services/vps/status/{vpsid}`. Virtualizor power state: 1 on, 0 off, 2 suspended.

    While the VPS is being set up the endpoint answers with the bare state number instead.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", strict=False)

    status: int
    used_cpu: Figure | None = None  # percent
    used_ram: Figure | None = None  # MB
    ram: Figure | None = None  # MB
    used_disk: Figure | None = None  # GB
    disk: Figure | None = None  # GB
    used_bandwidth: Figure | None = None  # GB this month


class InvoiceRow(Answer):
    """One item of `/api/billing/invoices` (WHMCS GetInvoices), newest first."""

    id: int
    duedate: str
    total: Sum
    status: str
    currencycode: str


class InvoiceRows(Answer):
    invoice: list[InvoiceRow] = []


class Invoices(Answer):
    result: str
    totalresults: int
    # An empty list instead of an object when the client has no invoices.
    invoices: InvoiceRows | list[Any] = []

    def rows(self) -> list[InvoiceRow]:
        return self.invoices.invoice if isinstance(self.invoices, InvoiceRows) else []


class InvoiceItem(Answer):
    type: str
    # The service id for a "Hosting" item; 0 for "Add Funds".
    relid: int
    description: str


class InvoiceItems(Answer):
    item: list[InvoiceItem] = []


class InvoiceDetails(Answer):
    """`/api/billing/invoices/{id}` (WHMCS GetInvoice). `balance` is what is left to pay."""

    result: str
    status: str = ""
    balance: Sum | None = None
    items: InvoiceItems | list[Any] = []

    def lines(self) -> list[InvoiceItem]:
        return self.items.item if isinstance(self.items, InvoiceItems) else []


class AddedFunds(Answer):
    invoiceid: int


class AddFunds(Answer):
    """`POST /api/billing/addfunds {"amount"}`: a WHMCS "Add Funds" invoice."""

    success: bool
    message: str = ""
    data: AddedFunds | None = None


class CryptomusLink(Answer):
    """`POST /api/billing/cryptomus/create-payment {"invoiceId", "returnUrl"}`."""

    success: bool
    url: str | None = None
    message: str = ""


class Outcome(Answer):
    """Answer of the site's action endpoints: `{"success", "message"}` for apply-credit,
    `{"done_msg": ...}` for power. Both are read leniently: only these fields matter."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=False)

    success: bool | None = None
    message: str = ""
    done_msg: str = ""
