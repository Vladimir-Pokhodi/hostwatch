"""Plugin interface. A plugin has a capability when it implements the protocol's methods."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable

import httpx

from hostwatch.core.models import (
    ActionResult,
    Balance,
    Invoice,
    Money,
    Offer,
    OrderRequest,
    Payment,
    PowerAction,
    Server,
    ServerAction,
    TopupMethod,
    Usage,
)

Secrets = Mapping[str, str]


@dataclass(frozen=True)
class ApiKeyAuth:
    """The owner pastes values for these secret fields, e.g. ("api_key",)."""

    fields: tuple[str, ...]


@dataclass(frozen=True)
class BrowserAuth:
    start_url: str
    allowed_domains: tuple[str, ...]
    cookies: tuple[str, ...]


@dataclass(frozen=True)
class PluginMeta:
    id: str
    name: str
    site: str
    auth: tuple[ApiKeyAuth | BrowserAuth, ...]
    currency: str


class PluginContext:
    """What the core gives a plugin for one call: account secrets and an HTTP client."""

    def __init__(
        self,
        secrets: Secrets,
        http: httpx.AsyncClient,
        save_secrets: Callable[[Secrets], Awaitable[None]],
    ) -> None:
        self.secrets: Secrets = dict(secrets)
        self.http = http
        self._save_secrets = save_secrets

    async def rotate_secrets(self, secrets: Secrets) -> None:
        """Persist new secrets. Call it before using a freshly issued token: the old one is dead."""
        await self._save_secrets(dict(secrets))
        self.secrets = dict(secrets)


class Plugin(ABC):
    meta: ClassVar[PluginMeta]

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    @abstractmethod
    async def servers(self) -> list[Server]: ...

    @abstractmethod
    async def balance(self) -> Balance: ...


@runtime_checkable
class SupportsUsage(Protocol):
    async def usage(self, server_id: str) -> Usage: ...


@runtime_checkable
class SupportsTopup(Protocol):
    async def topup_methods(self) -> list[TopupMethod]: ...

    async def topup(self, amount: Money, method_id: str) -> Payment: ...


@runtime_checkable
class SupportsInvoices(Protocol):
    async def invoices(self) -> list[Invoice]: ...


@runtime_checkable
class SupportsPayInvoice(Protocol):
    async def pay_invoice(self, invoice_id: str) -> ActionResult: ...


@runtime_checkable
class SupportsAutorenew(Protocol):
    async def set_autorenew(self, server_id: str, enabled: bool) -> ActionResult: ...


@runtime_checkable
class SupportsRenew(Protocol):
    async def renew(self, server_id: str) -> ActionResult: ...


@runtime_checkable
class SupportsPower(Protocol):
    async def power(self, server_id: str, action: PowerAction) -> ActionResult: ...


@runtime_checkable
class SupportsOrder(Protocol):
    async def catalog(self) -> list[Offer]: ...

    async def order(self, request: OrderRequest) -> ActionResult: ...


class Capability(StrEnum):
    USAGE = "usage"
    TOPUP = "topup"
    INVOICES = "invoices"
    PAY_INVOICE = "pay_invoice"
    AUTORENEW = "autorenew"
    RENEW = "renew"
    POWER = "power"
    ORDER = "order"


PROTOCOLS: dict[Capability, type] = {
    Capability.USAGE: SupportsUsage,
    Capability.TOPUP: SupportsTopup,
    Capability.INVOICES: SupportsInvoices,
    Capability.PAY_INVOICE: SupportsPayInvoice,
    Capability.AUTORENEW: SupportsAutorenew,
    Capability.RENEW: SupportsRenew,
    Capability.POWER: SupportsPower,
    Capability.ORDER: SupportsOrder,
}

# Which plugin capability a per-server action needs.
ACTION_CAPABILITY: dict[ServerAction, Capability] = {
    ServerAction.START: Capability.POWER,
    ServerAction.STOP: Capability.POWER,
    ServerAction.RESTART: Capability.POWER,
    ServerAction.AUTORENEW: Capability.AUTORENEW,
    ServerAction.RENEW: Capability.RENEW,
}


def capabilities(plugin: Plugin | type[Plugin]) -> set[Capability]:
    if isinstance(plugin, type):
        return {cap for cap, proto in PROTOCOLS.items() if _class_implements(plugin, proto)}
    return {cap for cap, proto in PROTOCOLS.items() if isinstance(plugin, proto)}


def _class_implements(cls: type, proto: type) -> bool:
    names = [name for name in vars(proto) if not name.startswith("_")]
    return all(callable(getattr(cls, name, None)) for name in names)
