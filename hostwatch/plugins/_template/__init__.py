"""Template Hoster (https://hoster.example) through its API key: servers and balance.

The requests and answer models below are made up. Replace them with what the hoster
really sends and answers (docs/adding-a-hoster.md), record fixtures with
`hostwatch record`, and keep the tests passing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, TypeVar

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import (
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Server,
    ServerKind,
    ServerStatus,
)
from hostwatch.core.plugin import ApiKeyAuth, Plugin, PluginMeta

BASE_URL = "https://api.hoster.example/v1/"
CURRENCY = "EUR"
# The hoster's status words. Anything else becomes `unknown`, never a guess.
STATUSES = {
    "active": ServerStatus.RUNNING,
    "stopped": ServerStatus.STOPPED,
    "suspended": ServerStatus.SUSPENDED,
}

A = TypeVar("A", bound=BaseModel)


class Answer(BaseModel):
    # Only the fields the plugin uses are declared. Types are strict: a changed field fails
    # validation, and the plugin reports `LayoutChanged` instead of showing a wrong value.
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)


class ServerItem(Answer):
    id: int
    name: str
    ip: str | None
    status: str
    plan: str
    # Prices come as strings, so they turn into Decimal without float rounding.
    price_monthly: str
    paid_until: AwareDatetime | None
    auto_renew: bool


class ServerList(Answer):
    servers: list[ServerItem]


class Account(Answer):
    balance: str
    currency: str


class TemplateHosterPlugin(Plugin):
    meta = PluginMeta(
        id="template_hoster",
        name="Template Hoster",
        site="https://hoster.example",
        auth=(ApiKeyAuth(fields=("api_key",)),),
        currency=CURRENCY,
    )
    # JSON paths of names the owner chose; `hostwatch record` replaces them in fixtures.
    record_names = ("servers.*.name",)

    async def servers(self) -> list[Server]:
        answer = await self._get(ServerList, "servers")
        return [_server(item) for item in answer.servers]

    async def balance(self) -> Balance:
        account = await self._get(Account, "account")
        if account.currency.upper() != CURRENCY:
            raise HosterError(f"the account is in {account.currency}")
        return Balance(money=_money(account.balance))

    async def _get(self, model: type[A], path: str, **params: Any) -> A:
        try:
            response = await self.ctx.http.get(
                BASE_URL + path,
                params=params,
                headers={
                    "Authorization": f"Bearer {self.ctx.secrets['api_key']}",
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.TransportError:
            raise HosterError("the hoster is unreachable") from None
        _raise_for_status(response)
        try:
            return model.model_validate_json(response.content)
        except ValidationError:
            raise LayoutChanged from None


def _server(item: ServerItem) -> Server:
    return Server(
        external_id=str(item.id),
        name=item.name,
        kind=ServerKind.VPS,
        ip=item.ip,
        plan=item.plan,
        status=STATUSES.get(item.status, ServerStatus.UNKNOWN),
        raw_status=item.status,
        price=_money(item.price_monthly),
        period_months=1,
        next_charge_at=item.paid_until,
        autorenew=Autorenew.ON if item.auto_renew else Autorenew.OFF,
        billing=BillingModel.PERIODIC,
    )


def _money(text: str) -> Money:
    try:
        amount = Decimal(text)
    except ArithmeticError:
        raise LayoutChanged from None
    if not amount.is_finite():
        raise LayoutChanged
    return Money(amount=amount, currency=CURRENCY)


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    if code < 400:
        return
    if code == 401:
        raise AuthExpired
    if code == 403:
        raise Blocked
    if code == 429:
        raise RateLimited
    raise HosterError(f"HTTP {code}")
