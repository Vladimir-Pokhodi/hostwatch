"""PayMaster, the SBP gateway of AdminVPS: from the WHMCS invoice form to a `qr.nspk.ru` link.

Checked from Python on 2026-11-08:

1. `POST clientarea.php?action=addfunds` answers with a page that holds the gateway form
   (`action="https://paymaster.ru/Payment/Init"`, hidden `LMI_*` fields; the invoice id
   is `LMI_PAYMENT_NO`);
2. posting that form gives `302 Location: https://paymaster.ru/cpay/<id>`;
3. `POST /cpay/api/Auth/AuthPaymentExternal {"paymentMethodAlias": "SBP"}` with the
   header `x-public-id: <id>` answers with `externalConfirmationInfoExt.paymentUrl`,
   the SBP link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, ValidationError
from selectolax.lexbor import LexborHTMLParser

from hostwatch.core.errors import HosterError, LayoutChanged

SITE = "https://paymaster.ru"
INIT = f"{SITE}/Payment/Init"
AUTH = f"{SITE}/cpay/api/Auth/AuthPaymentExternal"
CPAY = re.compile(r"^(?:https://paymaster\.ru)?/cpay/([0-9a-fA-F-]{16,64})(?:[/?#]|$)")
NSPK = "https://qr.nspk.ru/"


@dataclass(frozen=True)
class GatewayForm:
    action: str
    fields: dict[str, str]

    @property
    def invoice_id(self) -> str:
        return self.fields["LMI_PAYMENT_NO"]

    @property
    def amount(self) -> Decimal:
        try:
            return Decimal(self.fields["LMI_PAYMENT_AMOUNT"])
        except InvalidOperation:
            raise LayoutChanged from None


def gateway_form(html: str) -> GatewayForm:
    """The PayMaster form on the WHMCS page after «Add funds»."""
    page = LexborHTMLParser(html)
    form = next(
        (f for f in page.css("form") if (f.attributes.get("action") or "").startswith(INIT)),
        None,
    )
    if form is None:
        raise LayoutChanged
    fields = {
        name: input_.attributes.get("value") or ""
        for input_ in form.css("input")
        if (name := input_.attributes.get("name"))
    }
    if not fields.get("LMI_PAYMENT_NO", "").isdigit() or "LMI_PAYMENT_AMOUNT" not in fields:
        raise LayoutChanged
    if fields.get("LMI_PAYMENT_METHOD") != "SBP":
        raise LayoutChanged
    return GatewayForm(INIT, fields)


def payment_id(location: str) -> str:
    """`<id>` of `Location: https://paymaster.ru/cpay/<id>` after the form is posted."""
    found = CPAY.match(location)
    if found is None:
        raise LayoutChanged
    return found.group(1)


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _External(_Answer):
    paymentUrl: str


class _Result(_Answer):
    status: str
    externalConfirmationInfoExt: _External | None = None


class _Data(_Answer):
    authorizationResultExt: _Result


class _Auth(_Answer):
    error: object = None
    data: _Data | None = None


def sbp_link(body: bytes) -> str:
    """The SBP link from the answer to AuthPaymentExternal."""
    try:
        answer = _Auth.model_validate_json(body)
    except ValidationError:
        raise LayoutChanged from None
    if answer.error or answer.data is None:
        raise HosterError("PayMaster refused the SBP payment")
    external = answer.data.authorizationResultExt.externalConfirmationInfoExt
    if external is None or not external.paymentUrl.startswith(NSPK):
        raise LayoutChanged
    return external.paymentUrl
