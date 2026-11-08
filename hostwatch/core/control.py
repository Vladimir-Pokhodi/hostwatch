"""Actions on servers and money that the owner confirms in the bot (spec, «Подтверждения»).

The bot shows what, where and how much with «Yes / Cancel». «Yes» carries a one-time nonce
that lives five minutes: a second press of the same button, or a press after a restart,
finds nothing and does nothing.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from hostwatch.core.errors import HosterError
from hostwatch.core.logs import REDACTOR
from hostwatch.core.models import ActionResult, Money, PowerAction, Server, ServerStatus
from hostwatch.core.plugin import (
    Plugin,
    SupportsAutorenew,
    SupportsPayInvoice,
    SupportsPower,
    SupportsRenew,
)

NONCE_TTL = 300.0
# A poll right after «Stop» may still see the server running; the mark outlives that.
OWNER_STOP_GRACE = timedelta(minutes=10)
JOURNAL_LIMIT = 500


class Control(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    AUTORENEW_ON = "autorenew_on"
    AUTORENEW_OFF = "autorenew_off"
    RENEW = "renew"
    PAY_INVOICE = "pay_invoice"

    @property
    def spends_money(self) -> bool:
        return self in (Control.RENEW, Control.PAY_INVOICE)

    @property
    def stops_server(self) -> bool:
        """After these the hoster may report the server stopped for a while, on purpose."""
        return self in (Control.STOP, Control.RESTART)


POWER = {
    Control.START: PowerAction.START,
    Control.STOP: PowerAction.STOP,
    Control.RESTART: PowerAction.RESTART,
}


@dataclass(frozen=True)
class Request:
    """One confirmed action. `amount` is the sum the owner saw: the renewal price or the
    invoice total; the plugin refuses when the hoster asks for another one."""

    action: Control
    account_id: int
    server_id: str = ""
    invoice_id: str = ""
    amount: Money | None = None

    def params(self) -> dict[str, str]:
        """For the journal."""
        params = {"action": self.action.value}
        if self.server_id:
            params["server"] = self.server_id
        if self.invoice_id:
            params["invoice"] = self.invoice_id
        if self.amount is not None:
            params["amount"] = str(self.amount.amount)
            params["currency"] = self.amount.currency
        return params


async def perform(plugin: Plugin, request: Request) -> ActionResult:
    """Call the plugin method behind the action. A missing capability is a HosterError,
    so a stale button never reaches the hoster with a wrong request."""
    action = request.action
    if action in POWER:
        if not isinstance(plugin, SupportsPower):
            raise HosterError("power control is not supported")
        return await plugin.power(request.server_id, POWER[action])
    if action in (Control.AUTORENEW_ON, Control.AUTORENEW_OFF):
        if not isinstance(plugin, SupportsAutorenew):
            raise HosterError("autorenewal is not supported")
        return await plugin.set_autorenew(request.server_id, action is Control.AUTORENEW_ON)
    if request.amount is None:
        raise HosterError("the confirmed sum is missing")
    if action is Control.RENEW:
        if not isinstance(plugin, SupportsRenew):
            raise HosterError("renewal is not supported")
        return await plugin.renew(request.server_id, request.amount)
    if not isinstance(plugin, SupportsPayInvoice):
        raise HosterError("paying invoices is not supported")
    return await plugin.pay_invoice(request.invoice_id, request.amount)


def journal_text(detail: str) -> str:
    """The hoster's answer as the journal keeps it: loaded secrets masked, kept short."""
    return REDACTOR.redact(detail)[:JOURNAL_LIMIT]


@dataclass
class _Pending[T]:
    value: T
    expires_at: float


@dataclass
class Confirmations[T]:
    """One-time nonces for «Yes» buttons. Kept in memory: a restart forgets them all."""

    ttl: float = NONCE_TTL
    clock: Callable[[], float] = time.monotonic
    _pending: dict[str, _Pending[T]] = field(default_factory=dict)

    def issue(self, value: T) -> str:
        self._forget_expired()
        nonce = secrets.token_urlsafe(9)
        self._pending[nonce] = _Pending(value, self.clock() + self.ttl)
        return nonce

    def take(self, nonce: str) -> T | None:
        """The value once; None if the nonce is unknown, used or expired."""
        pending = self._pending.pop(nonce, None)
        if pending is None or self.clock() > pending.expires_at:
            return None
        return pending.value

    def _forget_expired(self) -> None:
        now = self.clock()
        for nonce in [n for n, p in self._pending.items() if now > p.expires_at]:
            del self._pending[nonce]


def finished_stops(
    marks: Mapping[str, datetime], servers: Iterable[Server], now: datetime
) -> list[str]:
    """Server ids whose «stopped by the owner» mark has done its job: the server is gone,
    or it runs again and the mark is older than OWNER_STOP_GRACE. A later stop then alerts."""
    current = {server.external_id: server for server in servers}
    done = []
    for server_id, at in marks.items():
        server = current.get(server_id)
        runs_again = server is not None and server.status is ServerStatus.RUNNING
        if server is None or (runs_again and now - at > OWNER_STOP_GRACE):
            done.append(server_id)
    return done
