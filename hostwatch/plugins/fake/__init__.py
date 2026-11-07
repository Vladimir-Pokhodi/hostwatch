"""Fake hoster for tests and demos. Its behaviour is set through the account secrets.

Secrets it understands:
- `balance`: balance in EUR, default 20.00;
- `fail`: raise an error on every call: auth_expired, rate_limited, blocked,
  layout_changed, hoster_error or crash (a plain RuntimeError);
- `sleep`: seconds to wait before answering;
- `token`: with `rotate` set to any value, every call replaces it with `<token>+`,
  the way Timeweb rotates its refresh token, before failing or answering.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import (
    ActionResult,
    Autorenew,
    Balance,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Offer,
    OrderRequest,
    Payment,
    PowerAction,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
    TopupMethod,
    Usage,
)
from hostwatch.core.plugin import ApiKeyAuth, Plugin, PluginMeta

CURRENCY = "EUR"
ALL_ACTIONS = frozenset(ServerAction)

_ERRORS: dict[str, Callable[[], Exception]] = {
    "auth_expired": AuthExpired,
    "rate_limited": RateLimited,
    "blocked": Blocked,
    "layout_changed": LayoutChanged,
    "hoster_error": lambda: HosterError("fake hoster says no"),
    "crash": lambda: RuntimeError("fake crash"),
}


def eur(amount: str) -> Money:
    return Money.of(amount, CURRENCY)


class FakePlugin(Plugin):
    meta = PluginMeta(
        id="fake",
        name="Fake hoster",
        site="https://fake.invalid",
        auth=(ApiKeyAuth(fields=("token",)),),
        currency=CURRENCY,
    )

    async def servers(self) -> list[Server]:
        await self._behave()
        now = datetime.now(UTC).replace(microsecond=0)
        return [
            self._server("1", "beaver", eur("4.50"), now + timedelta(days=8), Autorenew.ON),
            self._server("2", "bear", eur("12.00"), now + timedelta(days=16), Autorenew.ON),
            self._server(
                "3",
                "owl",
                eur("30.00"),
                now + timedelta(days=5),
                Autorenew.OFF,
                kind=ServerKind.DEDICATED,
                status=ServerStatus.STOPPED,
            ),
        ]

    async def balance(self) -> Balance:
        await self._behave()
        return Balance(money=eur(self.ctx.secrets.get("balance", "20.00")))

    async def usage(self, server_id: str) -> Usage:
        await self._behave()
        return Usage(
            server_id=server_id,
            measured_at=datetime.now(UTC),
            cpu_percent=12.5,
            ram_percent=40.0,
            disk_percent=55.0,
        )

    async def invoices(self) -> list[Invoice]:
        await self._behave()
        return [Invoice(id="100", amount=eur("4.50"), status=InvoiceStatus.UNPAID, server_id="1")]

    async def pay_invoice(self, invoice_id: str) -> ActionResult:
        await self._behave()
        return ActionResult(ok=True, detail=f"invoice {invoice_id} paid")

    async def set_autorenew(self, server_id: str, enabled: bool) -> ActionResult:
        await self._behave()
        return ActionResult(ok=True, detail=f"autorenew {'on' if enabled else 'off'}")

    async def renew(self, server_id: str) -> ActionResult:
        await self._behave()
        return ActionResult(ok=True, detail=f"server {server_id} renewed")

    async def power(self, server_id: str, action: PowerAction) -> ActionResult:
        await self._behave()
        return ActionResult(ok=True, detail=f"server {server_id}: {action.value}")

    async def topup_methods(self) -> list[TopupMethod]:
        await self._behave()
        return [TopupMethod(id="sbp", name="SBP", minimum=eur("0.50"))]

    async def topup(self, amount: Money, method_id: str) -> Payment:
        await self._behave()
        return Payment(
            id="200",
            amount=amount,
            method=method_id,
            url="https://fake.invalid/pay/200",
            created_at=datetime.now(UTC),
        )

    async def catalog(self) -> list[Offer]:
        await self._behave()
        return [
            Offer(
                id="small",
                name="Fake Small",
                location="nowhere",
                price=eur("4.50"),
                period_months=1,
                os=("debian-12",),
            )
        ]

    async def order(self, request: OrderRequest) -> ActionResult:
        await self._behave()
        return ActionResult(ok=True, detail=f"ordered {request.offer_id}")

    async def _behave(self) -> None:
        secrets = self.ctx.secrets
        if "rotate" in secrets:
            await self.ctx.rotate_secrets({**secrets, "token": secrets.get("token", "") + "+"})
        if "sleep" in secrets:
            await asyncio.sleep(float(secrets["sleep"]))
        if "fail" in secrets:
            raise _ERRORS[secrets["fail"]]()

    @staticmethod
    def _server(
        external_id: str,
        name: str,
        price: Money,
        next_charge_at: datetime,
        autorenew: Autorenew,
        kind: ServerKind = ServerKind.VPS,
        status: ServerStatus = ServerStatus.RUNNING,
    ) -> Server:
        return Server(
            external_id=external_id,
            name=name,
            kind=kind,
            ip=f"192.0.2.{10 + int(external_id)}",
            plan="fake-1",
            status=status,
            raw_status=status.value,
            price=price,
            period_months=1,
            next_charge_at=next_charge_at,
            autorenew=autorenew,
            billing=BillingModel.PERIODIC,
            actions=ALL_ACTIONS,
        )
