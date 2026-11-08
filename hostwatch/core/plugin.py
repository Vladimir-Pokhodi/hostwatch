"""Plugin interface. A plugin has a capability when it implements the protocol's methods."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

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

if TYPE_CHECKING:
    from hostwatch.core.record import Scrubber

Secrets = Mapping[str, str]


@dataclass(frozen=True)
class ApiKeyAuth:
    """The owner pastes values for these secret fields, e.g. ("api_key",)."""

    fields: tuple[str, ...]


@dataclass(frozen=True)
class BrowserAuth:
    """The owner logs in on the hoster's site through the login page (spec, decision 6).

    `allowed_domains` match the host and its subdomains. `cookies` are the names the login
    must produce; `extra_cookies` are kept when present (e.g. anti-bot cookies). Both take
    shell patterns: `WHMCS*`. Only cookies of the allowed domains are taken, and they are
    stored as the account's secrets under their names.
    """

    start_url: str
    allowed_domains: tuple[str, ...]
    cookies: tuple[str, ...]
    extra_cookies: tuple[str, ...] = ()

    def wants(self, name: str) -> bool:
        return any(fnmatchcase(name, p) for p in self.cookies + self.extra_cookies)

    def missing(self, names: Iterable[str]) -> list[str]:
        """Required patterns that none of `names` matches."""
        have = list(names)
        return [p for p in self.cookies if not any(fnmatchcase(n, p) for n in have)]

    def allows_host(self, host: str) -> bool:
        return host_in(host, self.allowed_domains)


def host_in(host: str, domains: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (d.lower() for d in domains))


def browser_auth(plugin: type[Plugin]) -> BrowserAuth | None:
    return next((a for a in plugin.meta.auth if isinstance(a, BrowserAuth)), None)


def cookie_header(secrets: Secrets, auth: BrowserAuth) -> str:
    """`Cookie` header value from the cookies a browser login stored in the secrets."""
    return "; ".join(f"{name}={value}" for name, value in secrets.items() if auth.wants(name))


@dataclass(frozen=True)
class PluginMeta:
    id: str
    name: str
    site: str
    auth: tuple[ApiKeyAuth | BrowserAuth, ...]
    currency: str


class Pages(Protocol):
    """A headless browser tab for pages that only work in a browser (Cryptomus at Regxa).

    `open` yields a Playwright `Page` that may only reach `allowed_domains` and their
    subdomains. It raises `BrowserBusy` while the one Chromium the server can afford is
    taken, e.g. by a login.
    """

    def open(self, allowed_domains: tuple[str, ...]) -> AbstractAsyncContextManager[Any]: ...


class PluginContext:
    """What the core gives a plugin for one call: account secrets, an HTTP client and,
    when the `browser` container is there, a headless browser tab."""

    def __init__(
        self,
        secrets: Secrets,
        http: httpx.AsyncClient,
        save_secrets: Callable[[Secrets], Awaitable[None]],
        pages: Pages | None = None,
    ) -> None:
        self.secrets: Secrets = dict(secrets)
        self.http = http
        self.pages = pages
        self._save_secrets = save_secrets

    async def rotate_secrets(self, secrets: Secrets) -> None:
        """Persist new secrets. Call it before using a freshly issued token: the old one is dead."""
        await self._save_secrets(dict(secrets))
        self.secrets = dict(secrets)


class Plugin(ABC):
    meta: ClassVar[PluginMeta]
    # A stub before the plugin is written: the owner can log in and the session is kept,
    # but the account is not polled.
    login_only: ClassVar[bool] = False
    # JSON paths with names the owner chose, e.g. "items.*.name"; `hostwatch record`
    # replaces their values. Emails, IPs, ids and secrets are replaced without this list.
    record_names: ClassVar[tuple[str, ...]] = ()
    # The hoster's top-up page: the bot gives it when an invoice cannot be made by requests.
    topup_page: ClassVar[str | None] = None

    @classmethod
    def record_text(cls, text: str, scrubber: Scrubber) -> str:
        """Scrubs a recorded answer before the core does, for what the core cannot spot:
        names, sums and form tokens inside HTML. Use `scrubber.label`, `fake_id` and
        `fake_money`, so a value gets the same placeholder in every answer."""
        return text

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    @abstractmethod
    async def servers(self) -> list[Server]: ...

    @abstractmethod
    async def balance(self) -> Balance: ...


@runtime_checkable
class SupportsLoginCheck(Protocol):
    """Checks a fresh browser login from the server, e.g. `GET /api/auth/session`.

    Raise AuthExpired if the session does not work. Without this method a login counts
    as done once every cookie in `BrowserAuth.cookies` is there.
    """

    async def check_login(self) -> None: ...


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
