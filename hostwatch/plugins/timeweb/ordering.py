"""Ordering a cloud server at Timeweb through the public API (docs/research-orders.md).

- catalog: `GET presets/servers` (ready tariffs, ₽ a month) and `GET os/servers`;
- order: `POST servers {"name", "preset_id", "os_id", "ssh_keys_ids"}` → 201 `{"server"}`
  with `root_pass`; the API reference only, no order was sent yet;
- billing is hourly from the balance, so a plan has one `HOURLY` price;
- the owner's key: found in `GET ssh-keys` by its body, else `POST ssh-keys`;
- root password: `root_pass` of `GET servers/{id}`.

Windows and Bitrix images are left out: they cost extra, and the catalog has no price for it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import Field, ValidationError

from hostwatch.core.catalog import (
    HOURLY,
    Catalog,
    Location,
    OperatingSystem,
    OrderRequest,
    OrderResult,
    OrderTemplate,
    Plan,
    Price,
)
from hostwatch.core.errors import AuthExpired, Blocked, HosterError, LayoutChanged, RateLimited
from hostwatch.core.models import Money
from hostwatch.plugins.timeweb.schema import Schema

if TYPE_CHECKING:
    from hostwatch.plugins.timeweb.plugin import TimewebPlugin

API = "https://api.timeweb.cloud/api/v1/"
CURRENCY = "RUB"
KEY_NAME = "hostwatch"
SKIPPED_FAMILIES = {"windows"}
SKIPPED_NAMES = {"bitrix"}
# `location` codes of the API; the v2 locations list has no names, only country codes.
LOCATIONS = {
    "ru-1": "Санкт-Петербург",
    "ru-2": "Новосибирск",
    "ru-3": "Москва",
    "ru-4": "Екатеринбург",
    "ru-5": "Казань",
    "pl-1": "Гданьск",
    "kz-1": "Алматы",
    "nl-1": "Амстердам",
    "de-1": "Франкфурт",
    "us-4": "США",
}


class Preset(Schema):
    id: int
    location: str
    price: Decimal
    cpu: int
    ram: int  # MB
    disk: int  # MB
    disk_type: str = ""
    description_short: str = ""


class PresetsAnswer(Schema):
    server_presets: list[Preset]


class Requirements(Schema):
    cpu_min: int | None = None
    ram_min: int | None = None
    disk_min: int | None = None


class Os(Schema):
    id: int
    family: str
    name: str
    version: str
    requirements: Requirements | None = None


class OsAnswer(Schema):
    servers_os: list[Os]


class SshKey(Schema):
    id: int
    body: str


class SshKeysAnswer(Schema):
    ssh_keys: list[SshKey]


class SshKeyAnswer(Schema):
    ssh_key: SshKey


class OsRef(Schema):
    id: int


class NewServer(Schema):
    id: int
    status: str = ""
    root_pass: str | None = Field(default=None, repr=False)


class NewServerAnswer(Schema):
    server: NewServer


class ServerShape(Schema):
    """What «order the same» needs; the password in the same answer is not read."""

    preset_id: int | None = None
    os: OsRef | None = None
    location: str | None = None


class ServerShapeAnswer(Schema):
    server: ServerShape


async def catalog(plugin: TimewebPlugin) -> Catalog:
    presets = (await plugin._get("presets/servers", PresetsAnswer)).server_presets
    systems = [
        os
        for os in (await plugin._get("os/servers", OsAnswer)).servers_os
        if os.family not in SKIPPED_FAMILIES and os.name not in SKIPPED_NAMES
    ]
    plans = tuple(_plan(preset, systems) for preset in presets)
    codes = list(dict.fromkeys(p.location for p in sorted(presets, key=lambda p: p.price)))
    return Catalog(
        locations=tuple(Location(id=code, name=LOCATIONS.get(code, code)) for code in codes),
        plans=plans,
        systems=tuple(
            OperatingSystem(id=str(os.id), name=f"{os.name.capitalize()} {os.version}")
            for os in systems
        ),
        ssh_keys=True,
    )


async def quote(plugin: TimewebPlugin, request: OrderRequest) -> Money:
    if request.period_months != HOURLY:
        raise HosterError("Timeweb bills by the hour only")
    presets = (await plugin._get("presets/servers", PresetsAnswer)).server_presets
    found = next((p for p in presets if str(p.id) == request.plan_id), None)
    if found is None:
        raise HosterError("the plan is not sold any more")
    return Money(amount=found.price, currency=CURRENCY)


async def order(plugin: TimewebPlugin, request: OrderRequest) -> OrderResult:
    if not (request.plan_id.isdigit() and request.os_id.isdigit()):
        raise HosterError("bad plan or OS id")
    body: dict[str, Any] = {
        "name": request.name,
        "preset_id": int(request.plan_id),
        "os_id": int(request.os_id),
    }
    if request.ssh_key:
        body["ssh_keys_ids"] = [await _key_id(plugin, request.ssh_key)]
    response = await _send(plugin, "POST", "servers", body)
    server = _parse(response, NewServerAnswer).server
    return OrderResult(
        ok=True,
        detail=f"HTTP {response.status_code}: server {server.id}, {server.status or '?'}",
        server_id=str(server.id),
        password=server.root_pass or None,
    )


async def template(plugin: TimewebPlugin, server_id: str) -> OrderTemplate:
    if not server_id.isdigit():
        return OrderTemplate()  # a dedicated server: not in the cloud catalog
    server = (await plugin._get(f"servers/{server_id}", ServerShapeAnswer)).server
    return OrderTemplate(
        location_id=server.location,
        plan_id=None if server.preset_id is None else str(server.preset_id),
        os_id=None if server.os is None else str(server.os.id),
        period_months=HOURLY,
    )


async def root_password(plugin: TimewebPlugin, server_id: str) -> str:
    if not server_id.isdigit():
        raise HosterError("the API tells no password for this server")
    server = (await plugin._get(f"servers/{server_id}", NewServerAnswer)).server
    if not server.root_pass:
        raise HosterError("Timeweb tells no password for this server")
    return server.root_pass


def _plan(preset: Preset, systems: list[Os]) -> Plan:
    disk = f"{preset.disk / 1024:g} GB {preset.disk_type.upper()}".strip()
    return Plan(
        id=str(preset.id),
        name=preset.description_short or f"#{preset.id}",
        location_id=preset.location,
        specs=f"{preset.cpu} CPU · {preset.ram / 1024:g} GB RAM · {disk}",
        prices=(Price(period_months=HOURLY, amount=Money(amount=preset.price, currency=CURRENCY)),),
        os_ids=tuple(str(os.id) for os in systems if _fits(os, preset)),
    )


def _fits(os: Os, preset: Preset) -> bool:
    need = os.requirements
    if need is None:
        return True
    return (
        (need.cpu_min or 0) <= preset.cpu
        and (need.ram_min or 0) <= preset.ram
        and (need.disk_min or 0) <= preset.disk
    )


async def _key_id(plugin: TimewebPlugin, key: str) -> int:
    """The id of the owner's key in the account; the key is added once if it is not there."""
    wanted = _key_body(key)
    keys = (await plugin._get("ssh-keys", SshKeysAnswer)).ssh_keys
    found = next((k for k in keys if _key_body(k.body) == wanted), None)
    if found is not None:
        return found.id
    body = {"name": KEY_NAME, "body": key, "is_default": False}
    response = await _send(plugin, "POST", "ssh-keys", body)
    return _parse(response, SshKeyAnswer).ssh_key.id


def _key_body(key: str) -> str:
    """Type and base64 of a key: the comment does not matter."""
    return " ".join(key.split()[:2])


async def _send(
    plugin: TimewebPlugin, method: str, path: str, body: dict[str, Any]
) -> httpx.Response:
    token = plugin.ctx.secrets.get("api_token")
    if not token:
        raise AuthExpired
    try:
        response = await plugin.ctx.http.request(
            method,
            API + path,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except httpx.TimeoutException:
        raise TimeoutError from None
    except httpx.TransportError:
        raise HosterError("Timeweb is unreachable") from None
    _check(response)
    return response


def _check(response: httpx.Response) -> None:
    """An order refused with a JSON reason (no money, a limit) is the hoster's word, also
    with 403; a 403 without JSON is QRATOR."""
    code = response.status_code
    if code < 400:
        return
    if code == 401:
        raise AuthExpired
    if code == 429:
        raise RateLimited
    reason = _error_code(response)
    if reason is None:
        if code == 403:
            raise Blocked
        reason = f"HTTP {code}"
    raise HosterError(reason)


def _error_code(response: httpx.Response) -> str | None:
    """Timeweb's `error_code` is a fixed word such as `no_balance`; `message` may echo input."""
    try:
        code = response.json().get("error_code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and code.isidentifier() else None


def _parse[M: Schema](response: httpx.Response, model: type[M]) -> M:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise LayoutChanged from None
