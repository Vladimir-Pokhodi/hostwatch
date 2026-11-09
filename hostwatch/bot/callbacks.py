"""Callback data of the inline buttons. Telegram limits each to 64 bytes."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

# Buttons under notifications carry this before their data: they are not the menu, yet they
# work. Pressing one closes the old menu and opens a new one below, on the target screen.
NOTE = "!"
MAX_DATA = 64


def note(data: CallbackData) -> str:
    """Callback data of a button under a notification. ValueError if it does not fit."""
    packed = NOTE + data.pack()
    if len(packed.encode()) > MAX_DATA:
        raise ValueError(f"callback data too long: {packed!r}")
    return packed


class Nav(CallbackData, prefix="n"):
    """Open a section: menu, summary, servers, balance, integrations, connect, journal,
    settings, order."""

    to: str


class HostCb(CallbackData, prefix="h"):
    """Servers of one account, a page of them."""

    acc: int
    page: int = 0


class ServerCb(CallbackData, prefix="s"):
    acc: int
    sid: str


class ActionCb(CallbackData, prefix="a"):
    """A server card action; it asks for confirmation first (`ConfirmCb`)."""

    acc: int
    sid: str
    act: str


class ConfirmCb(CallbackData, prefix="cf"):
    """«Yes» or «Cancel» under a confirmation; `n` is the one-time nonce of the action."""

    n: str
    yes: bool


class InvoicesCb(CallbackData, prefix="iv"):
    """Unpaid invoices of an account, read from the hoster."""

    acc: int


class PayCb(CallbackData, prefix="pi", sep="|"):
    """Pay invoice `inv` from the balance at the hoster; asks for confirmation first."""

    acc: int
    inv: str


class RefreshCb(CallbackData, prefix="r"):
    """Poll now. `acc` 0 means every account; `view` is what to show after it."""

    acc: int
    view: str
    sid: str = ""


class AccountCb(CallbackData, prefix="ac"):
    acc: int
    op: str


class AddCb(CallbackData, prefix="add"):
    plugin: str


class LabelCb(CallbackData, prefix="lbl"):
    """Add the account under the hoster's name instead of a typed label."""


class TopupCb(CallbackData, prefix="tu", sep="|"):
    """Top up an account: `m` empty lists the methods, `*crypto` the coins (page `p`) or,
    with a coin `c`, its networks; a method id asks for the amount. `|` separates: Aeza's
    method ids hold colons."""

    acc: int
    m: str = ""
    c: str = ""
    p: int = 0


class TopupAmountCb(CallbackData, prefix="ta", sep="|"):
    """Make the invoice for `amt` (a plain decimal); empty `amt` asks for a typed sum."""

    acc: int
    m: str
    amt: str


class PayOptCb(CallbackData, prefix="po", sep="|"):
    """The coin of an invoice made before the coin is chosen: `pay` is the row id in
    `payments`; empty `c` and `o` list the coins (page `p`), `c` the coin's networks,
    `o` (a `PayOption` id) gets the address."""

    pay: int
    c: str = ""
    o: str = ""
    p: int = 0


class PaidCb(CallbackData, prefix="pd"):
    """«Paid» under an invoice: `pay` is the row id in `payments`."""

    pay: int


class JournalCb(CallbackData, prefix="j"):
    page: int


class SetCb(CallbackData, prefix="set"):
    """A setting choice. `onb` is set while the first-run questions are asked."""

    key: str
    value: str = ""
    onb: bool = False


class LoginCb(CallbackData, prefix="lg"):
    """«Not me» under the «link opened» message: end the login session."""

    op: str
