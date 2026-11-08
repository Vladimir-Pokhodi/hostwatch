"""Callback data of the inline buttons. Telegram limits each to 64 bytes."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


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
    """A server action. Until stage 10 every one of them leads to a stub."""

    acc: int
    sid: str
    act: str


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


class TopupCb(CallbackData, prefix="tu"):
    acc: int


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
