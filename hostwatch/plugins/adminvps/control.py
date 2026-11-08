"""Reading what the AdminVPS service page offers for control, and the answers to control requests.

Power: the service page holds one small form per action (SolusVM module of WHMCS):
`token`, `id`, `modop=custom`, `a=boot|shutdown|reboot`, posted to the service page. WHMCS
shows the result on the next page as `#alertModuleCustomButtonSuccess` or
`#alertModuleCustomButtonFailed`.

Autorenewal and renewal: the ModulesGarden HostingRenewals addon (its own JS framework,
read from the page on 2026-11-08). A button calls `loadModal(event, target, namespace,
index)`, which GETs the service page with `loadData`, `namespace`, `index`,
`mgformtype=read`, `ajax=1` and gets JSON: `{"data": {"status", "message", "rawData":
{"htmlData", "template"}}}`. The modal holds a `<form id namespace index mgformtype>` with
hidden fields. «Confirm» POSTs the fields as multipart to the service page with the
form's `loadData`, `namespace`, `index`, `mgformtype` and `ajax=1`; the answer is the same
JSON with `status` `success` or an error and a `message`. The renewal form first asks for
the price with `mgformtype=checkPrice`; the answer's `htmlData` has `fullTotal` and
`displayFullTotal`. Opening the modals was checked live; the posts were not sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from hostwatch.core.errors import LayoutChanged
from hostwatch.core.models import ServerAction

# `a` of the power forms, by the action it does.
POWER_COMMANDS = {
    ServerAction.START: "boot",
    ServerAction.STOP: "shutdown",
    ServerAction.RESTART: "reboot",
}
TURN_ON = "turnOnRenewals"
TURN_OFF = "turnOffRenewals"
RENEW = "renewService"
LOAD_MODAL = re.compile(r"loadModal\(\$event,\s*'(\w+)',\s*'(\w+)',\s*'(\w+)'")
INVOICE = re.compile(r"viewinvoice\.php\?id=(\d+)")
TEMPLATE_TAG = re.compile(r"</?script[^>]*>", re.IGNORECASE)


@dataclass(frozen=True)
class PowerForm:
    action: str
    fields: dict[str, str]


@dataclass(frozen=True)
class Modal:
    """A HostingRenewals button: what `loadModal` sends for it."""

    target: str
    namespace: str
    index: str


@dataclass(frozen=True)
class ModalForm:
    """The form inside a HostingRenewals modal: where «Confirm» posts and what."""

    id: str
    namespace: str
    index: str
    formtype: str
    fields: dict[str, str]


@dataclass(frozen=True)
class MgAnswer:
    """`{"data": {...}}` of the framework; `raw` is its `rawData`."""

    status: str
    message: str
    data: dict[str, Any]
    raw: dict[str, Any]
    text: str

    @property
    def ok(self) -> bool:
        return self.status == "success"


def actions(page: LexborHTMLParser) -> frozenset[ServerAction]:
    """What the service page lets the owner do: the power forms and the renewal buttons."""
    found = {action for action in POWER_COMMANDS if power_form(page, action) is not None}
    modals = renewal_modals(page)
    if TURN_ON in modals or TURN_OFF in modals:
        found.add(ServerAction.AUTORENEW)
    if RENEW in modals:
        found.add(ServerAction.RENEW)
    return frozenset(found)


def power_form(page: LexborHTMLParser, action: ServerAction) -> PowerForm | None:
    command = POWER_COMMANDS.get(action)
    for form in page.css("form"):
        fields = _fields(form.css("input[name]"))
        if fields.get("modop") == "custom" and fields.get("a") == command:
            if not fields.get("token") or not fields.get("id"):
                raise LayoutChanged
            return PowerForm(form.attributes.get("action") or "", fields)
    return None


def renewal_modals(page: LexborHTMLParser) -> dict[str, Modal]:
    """HostingRenewals buttons on the page, shown or hidden, by their target."""
    modals = {}
    for link in page.css("a"):
        found = LOAD_MODAL.search(link.attributes.get("@click") or "")
        if found:
            target, namespace, index = found.groups()
            modals[target] = Modal(target, namespace, index)
    return modals


def page_token(page: LexborHTMLParser) -> str:
    """The CSRF token WHMCS puts into the page's forms."""
    field = page.css_first('input[name="token"]')
    token = (field.attributes.get("value") or "") if field is not None else ""
    if not token:
        raise LayoutChanged
    return token


def module_result(html: str) -> tuple[bool | None, str]:
    """The result alert WHMCS shows after a module button: (True, text), (False, reason),
    or (None, "") when the page has none."""
    page = LexborHTMLParser(html)
    for selector, ok in (
        ("#alertModuleCustomButtonSuccess", True),
        ("#alertModuleCustomButtonFailed", False),
    ):
        alert = page.css_first(selector)
        if alert is not None:
            return ok, alert.text(separator=" ", strip=True)
    return None, ""


def mg_answer(body: str) -> MgAnswer:
    try:
        data = json.loads(body)["data"]
    except (ValueError, KeyError, TypeError):
        raise LayoutChanged from None
    if not isinstance(data, dict):
        raise LayoutChanged
    raw = data.get("rawData")
    message = data.get("message")
    return MgAnswer(
        status=str(data.get("status") or ""),
        message=message if isinstance(message, str) else "",
        data=data,
        raw=raw if isinstance(raw, dict) else {},
        text=body,
    )


def modal_form(answer: MgAnswer) -> ModalForm:
    """The form of an opened modal; the renewal one sits in `template`, the rest in `htmlData`."""
    html = " ".join(str(answer.raw.get(key) or "") for key in ("htmlData", "template"))
    # The renewal form is a Vue template inside <script type="text/x-template">, which an
    # HTML parser keeps as plain text: unwrap it first.
    html = TEMPLATE_TAG.sub("", html)
    form = LexborHTMLParser(html).css_first("form[mgformtype]")
    if form is None:
        raise LayoutChanged
    attrs = {key: form.attributes.get(key) or "" for key in ("id", "namespace", "index")}
    formtype = form.attributes.get("mgformtype") or ""
    if not all(attrs.values()) or not formtype:
        raise LayoutChanged
    fields = _fields(form.css("input[name]"))
    if not fields.get("hostingID"):
        raise LayoutChanged
    return ModalForm(attrs["id"], attrs["namespace"], attrs["index"], formtype, fields)


def renewal_total(answer: MgAnswer) -> Decimal:
    """The sum to pay from a `checkPrice` answer: `fullTotal.total`, else the shown text.
    The page's script reads `data.htmlData`; `rawData.htmlData` is taken as well."""
    cart = next(
        (
            c
            for c in (answer.data.get("htmlData"), answer.raw.get("htmlData"))
            if isinstance(c, dict)
        ),
        None,
    )
    if cart is None:
        raise LayoutChanged
    full = cart.get("fullTotal")
    shown = cart.get("displayFullTotal")
    for value in (
        full.get("total") if isinstance(full, dict) else None,
        shown.get("total") if isinstance(shown, dict) else None,
    ):
        amount = _amount(value)
        if amount is not None:
            return amount
    raise LayoutChanged


def invoice_id(text: str) -> str | None:
    found = INVOICE.search(text)
    return found.group(1) if found else None


def _fields(inputs: list[Any]) -> dict[str, str]:
    return {
        node.attributes["name"]: node.attributes.get("value") or ""
        for node in inputs
        if node.attributes.get("name")
    }


def _amount(value: object) -> Decimal | None:
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    found = re.search(r"\d[\d\s]*(?:[.,]\d+)?", value)
    if found is None:
        return None
    try:
        return Decimal(re.sub(r"\s", "", found.group()).replace(",", "."))
    except InvalidOperation:
        return None
