"""Timeweb Cloud API answers, only the fields the plugin reads.

Unknown fields are ignored: Timeweb adds fields often. A missing or mistyped field we read
is a layout change.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Finances(Schema):
    balance: Decimal
    currency: str
    hourly_cost: Decimal
    monthly_cost: Decimal
    hours_left: Decimal | None


class FinancesAnswer(Schema):
    finances: Finances


class Meta(Schema):
    total: int


class Ip(Schema):
    ip: str
    type: str
    is_main: bool = False


class Network(Schema):
    type: str
    ips: list[Ip] | None = None


class Disk(Schema):
    size: int
    used: int
    is_system: bool


class CloudServer(Schema):
    id: int
    name: str
    status: str
    is_blocked: bool = False
    preset_id: int | None = None
    networks: list[Network]
    disks: list[Disk]


class CloudServersAnswer(Schema):
    servers: list[CloudServer]
    meta: Meta


class CloudServerAnswer(Schema):
    server: CloudServer


class DedicatedServer(Schema):
    id: int
    name: str
    status: str
    ip: str | None
    price: Decimal
    cpu_description: str


class DedicatedServersAnswer(Schema):
    dedicated_servers: list[DedicatedServer]


class CostInfo(Schema):
    name: str = ""
    description: str = ""


class Configuration(Schema):
    cpu: int | None = None
    ram: int | None = None
    disk: int | None = None


class ServiceCost(Schema):
    type: str
    service_id: int | str | None = None
    # Monthly, with backups and IP addresses of the service.
    total_cost: Decimal
    info: CostInfo | None = None
    configuration: Configuration | None = None


class ServiceCostsAnswer(Schema):
    services_costs: list[ServiceCost]


class Point(Schema):
    value: float
    time: AwareDatetime


class Statistic(Schema):
    name: str
    points: list[Point] = Field(alias="list")


class StatisticsAnswer(Schema):
    statistics: list[Statistic]

    def series(self, name: str) -> list[Point]:
        return [point for stat in self.statistics if stat.name == name for point in stat.points]
