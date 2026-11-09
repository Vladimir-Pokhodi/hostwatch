"""History kept forever (1.1, part 4): load, server changes, money in and out.

Load: every poll adds a sample; samples older than 30 days are rolled into hours at
night (`rollup`) and deleted. Server events and money events are never pruned.

Money events come from two sources that overlap: `observed` (the balance went up or
down between two reads; complete, but not tied to servers) and `hoster` (charges the
hoster ties to a server). Sum one source, not both: totals from `observed`, the split
by server from `hoster`.

The read functions are for the Mini App API.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

from hostwatch.core.history import (
    MoneyKind,
    MoneyMove,
    MoneySource,
    ServerChange,
    ServerEventKind,
)
from hostwatch.core.models import Money, Usage

SAMPLES_KEPT = timedelta(days=30)
# A range up to this long is drawn from samples; a longer one from hours.
RAW_RANGE = timedelta(hours=48)
# Metric column in `usage_samples` -> attribute of `Usage`.
METRICS = {
    "cpu": "cpu_percent",
    "ram": "ram_percent",
    "disk": "disk_percent",
    "traffic_gb": "traffic_gb",
    "net_in_mbits": "net_in_mbits",
    "net_out_mbits": "net_out_mbits",
}
BACKFILL_ATTEMPTS = 3
# The `settings` key migration 8 sets to the time it ran.
STARTED_KEY = "history.started"


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _hour(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


# Hour of a stored time: "2026-11-09T03:15:00+00:00" -> "2026-11-09T03:00:00+00:00".
HOUR_SQL = "substr(at, 1, 13) || ':00:00+00:00'"


@dataclass(frozen=True)
class UsagePoint:
    """One point of a load chart: a sample (avg == max) or an hour."""

    at: datetime
    avg: dict[str, float | None]
    max: dict[str, float | None]
    samples: int


@dataclass(frozen=True)
class UsageSeries:
    # "samples" or "hourly"
    resolution: str
    points: list[UsagePoint]


@dataclass(frozen=True)
class ServerEvent:
    id: int
    account_id: int
    server_id: str
    at: datetime
    kind: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class MoneyEvent:
    id: int
    account_id: int
    at: datetime
    kind: MoneyKind
    amount: Money
    source: MoneySource
    server_id: str | None
    external_id: str | None
    description: str


@dataclass(frozen=True)
class MonthTotal:
    # "2026-11" in the time zone asked for.
    month: str
    account_id: int
    server_id: str | None
    currency: str
    topup: Decimal
    charge: Decimal


class HistoryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # Load

    def add_samples(self, account_id: int, usage: Iterable[Usage]) -> int:
        """Store measurements; one already stored for that moment is kept. Measurements
        with no value at all are skipped. Returns how many rows were added."""
        rows = []
        for item in usage:
            values = [getattr(item, attr) for attr in METRICS.values()]
            if all(v is None for v in values):
                continue
            rows.append((account_id, item.server_id, _ts(item.measured_at), *values))
        if not rows:
            return 0
        with self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                f"INSERT OR IGNORE INTO usage_samples (account_id, server_id, at,"
                f" {', '.join(METRICS)}) VALUES (?, ?, ?, {', '.join('?' for _ in METRICS)})",
                rows,
            )
            return self._conn.total_changes - before

    def rollup(self, now: datetime, keep: timedelta = SAMPLES_KEPT) -> int:
        """Roll samples older than `keep` into hours and delete them. Only whole hours
        go, so an hour is not split. An hour already there is merged, weighted by the
        sample count. Returns how many samples were rolled."""
        cutoff = _ts(_hour(now - keep))
        avg = ", ".join(f"AVG({m}), MAX({m})" for m in METRICS)
        cols = ", ".join(f"{m}_avg, {m}_max" for m in METRICS)
        merge = ", ".join(
            f"{m}_avg = CASE WHEN excluded.{m}_avg IS NULL THEN {m}_avg"
            f" WHEN {m}_avg IS NULL THEN excluded.{m}_avg"
            f" ELSE ({m}_avg * samples + excluded.{m}_avg * excluded.samples)"
            f" / (samples + excluded.samples) END,"
            f" {m}_max = MAX(COALESCE({m}_max, excluded.{m}_max),"
            f" COALESCE(excluded.{m}_max, {m}_max))"
            for m in METRICS
        )
        with self._conn:
            self._conn.execute(
                f"INSERT INTO usage_hourly (account_id, server_id, hour, {cols}, samples)"
                f" SELECT account_id, server_id, {HOUR_SQL}, {avg}, COUNT(*)"
                " FROM usage_samples WHERE at < ? GROUP BY account_id, server_id, 3"
                " ON CONFLICT (account_id, server_id, hour) DO UPDATE SET"
                f" {merge}, samples = samples + excluded.samples",
                (cutoff,),
            )
            cursor = self._conn.execute("DELETE FROM usage_samples WHERE at < ?", (cutoff,))
        return cursor.rowcount

    def usage_series(
        self, account_id: int, server_id: str, start: datetime, end: datetime
    ) -> UsageSeries:
        """Load between `start` (included) and `end` (not): raw samples for a range up to
        48 hours, hours for a longer one. Hours not rolled up yet are made from samples."""
        if end - start <= RAW_RANGE:
            return UsageSeries("samples", self._samples(account_id, server_id, start, end))
        return UsageSeries("hourly", self._hours(account_id, server_id, start, end))

    def _samples(
        self, account_id: int, server_id: str, start: datetime, end: datetime
    ) -> list[UsagePoint]:
        rows = self._conn.execute(
            "SELECT * FROM usage_samples WHERE account_id = ? AND server_id = ?"
            " AND at >= ? AND at < ? ORDER BY at",
            (account_id, server_id, _ts(start), _ts(end)),
        )
        points = []
        for r in rows:
            values = {m: r[m] for m in METRICS}
            points.append(UsagePoint(datetime.fromisoformat(r["at"]), values, values, 1))
        return points

    def _hours(
        self, account_id: int, server_id: str, start: datetime, end: datetime
    ) -> list[UsagePoint]:
        args = (account_id, server_id, _ts(_hour(start)), _ts(end))
        stored = self._conn.execute(
            "SELECT * FROM usage_hourly WHERE account_id = ? AND server_id = ?"
            " AND hour >= ? AND hour < ?",
            args,
        )
        avg = ", ".join(f"AVG({m}) AS {m}_avg, MAX({m}) AS {m}_max" for m in METRICS)
        fresh = self._conn.execute(
            f"SELECT {HOUR_SQL} AS hour, {avg}, COUNT(*) AS samples FROM usage_samples"
            " WHERE account_id = ? AND server_id = ? AND at >= ? AND at < ? GROUP BY 1",
            args,
        )
        hours: dict[str, UsagePoint] = {}
        for r in [*stored, *fresh]:
            point = UsagePoint(
                at=datetime.fromisoformat(r["hour"]),
                avg={m: r[f"{m}_avg"] for m in METRICS},
                max={m: r[f"{m}_max"] for m in METRICS},
                samples=r["samples"],
            )
            old = hours.get(r["hour"])
            hours[r["hour"]] = point if old is None else _merge(old, point)
        return [hours[key] for key in sorted(hours)]

    # Past load from the hoster

    def needs_backfill(self, account_id: int, server_ids: Iterable[str]) -> list[str]:
        """Servers whose past load was not read yet and has attempts left."""
        rows = self._conn.execute(
            "SELECT server_id FROM usage_backfills WHERE account_id = ?"
            " AND (done = 1 OR attempts >= ?)",
            (account_id, BACKFILL_ATTEMPTS),
        )
        finished = {r["server_id"] for r in rows}
        return [sid for sid in server_ids if sid not in finished]

    def backfill_done(
        self, account_id: int, server_id: str, at: datetime, points: int | None
    ) -> None:
        """`points` None: the attempt failed and is counted."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO usage_backfills (account_id, server_id, at, attempts, done, points)"
                " VALUES (?, ?, ?, 1, ?, ?)"
                " ON CONFLICT (account_id, server_id) DO UPDATE SET at = excluded.at,"
                " attempts = attempts + 1, done = excluded.done, points = excluded.points",
                (account_id, server_id, _ts(at), int(points is not None), points or 0),
            )

    # Server events

    def add_server_events(
        self, account_id: int, changes: Sequence[ServerChange], at: datetime
    ) -> None:
        if not changes:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO server_events (account_id, server_id, at, kind, old, new)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(account_id, c.server_id, _ts(at), c.kind.value, c.old, c.new) for c in changes],
            )

    def server_events(
        self,
        account_id: int | None = None,
        server_id: str | None = None,
        *,
        kinds: Iterable[ServerEventKind] = (),
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
    ) -> list[ServerEvent]:
        """Newest first."""
        where, args = _filters(account_id, server_id, since, until)
        wanted = [k.value for k in kinds]
        if wanted:
            where.append(f"kind IN ({', '.join('?' for _ in wanted)})")
            args.extend(wanted)
        rows = self._conn.execute(
            f"SELECT * FROM server_events WHERE {' AND '.join(where)}"
            " ORDER BY at DESC, id DESC LIMIT ?",
            (*args, limit),
        )
        return [
            ServerEvent(
                id=r["id"],
                account_id=r["account_id"],
                server_id=r["server_id"],
                at=datetime.fromisoformat(r["at"]),
                kind=r["kind"],
                old=r["old"],
                new=r["new"],
            )
            for r in rows
        ]

    # Money events

    def add_money(self, account_id: int, moves: Iterable[MoneyMove]) -> int:
        """Store money moves; a hoster record already stored (same id) is skipped."""
        rows = [
            (
                account_id,
                _ts(m.at),
                m.kind.value,
                str(m.amount.amount),
                m.amount.currency,
                m.server_id,
                m.source.value,
                m.external_id,
                m.description,
            )
            for m in moves
        ]
        if not rows:
            return 0
        with self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO money_events (account_id, at, kind, amount, currency,"
                " server_id, source, external_id, description)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            return self._conn.total_changes - before

    def money_events(
        self,
        account_id: int | None = None,
        server_id: str | None = None,
        *,
        source: MoneySource | None = None,
        kind: MoneyKind | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[MoneyEvent]:
        """Newest first."""
        where, args = _filters(account_id, server_id, since, until)
        if source is not None:
            where.append("source = ?")
            args.append(source.value)
        if kind is not None:
            where.append("kind = ?")
            args.append(kind.value)
        rows = self._conn.execute(
            f"SELECT * FROM money_events WHERE {' AND '.join(where)}"
            " ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        return [
            MoneyEvent(
                id=r["id"],
                account_id=r["account_id"],
                at=datetime.fromisoformat(r["at"]),
                kind=MoneyKind(r["kind"]),
                amount=Money(amount=Decimal(r["amount"]), currency=r["currency"]),
                source=MoneySource(r["source"]),
                server_id=r["server_id"],
                external_id=r["external_id"],
                description=r["description"],
            )
            for r in rows
        ]

    def money_by_month(
        self,
        source: MoneySource,
        tz: tzinfo = UTC,
        *,
        account_id: int | None = None,
        server_id: str | None = None,
        by_server: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[MonthTotal]:
        """Top-ups and charges per month in `tz`, per account (and per server with
        `by_server`) and currency, oldest month first."""
        where, args = _filters(account_id, server_id, since, until)
        where.append("source = ?")
        args.append(source.value)
        rows = self._conn.execute(
            "SELECT at, account_id, server_id, kind, amount, currency FROM money_events"
            f" WHERE {' AND '.join(where)}",
            args,
        )
        totals: dict[tuple[str, int, str | None, str], list[Decimal]] = {}
        for r in rows:
            month = datetime.fromisoformat(r["at"]).astimezone(tz).strftime("%Y-%m")
            key = (month, r["account_id"], r["server_id"] if by_server else None, r["currency"])
            pair = totals.setdefault(key, [Decimal(0), Decimal(0)])
            pair[0 if r["kind"] == MoneyKind.TOPUP.value else 1] += Decimal(r["amount"])
        return [
            MonthTotal(month, acc, sid, currency, topup, charge)
            for (month, acc, sid, currency), (topup, charge) in sorted(
                totals.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or "")
            )
        ]

    # Where the history begins

    def started(self) -> datetime | None:
        """When the bot began keeping history: the mark migration 8 left, or the oldest
        record if one is older (a database that had history before the mark)."""
        row = self._conn.execute(
            "SELECT MIN(at) FROM (SELECT value AS at FROM settings WHERE key = ?"
            " UNION ALL SELECT MIN(at) FROM money_events"
            " UNION ALL SELECT MIN(at) FROM server_events"
            " UNION ALL SELECT MIN(at) FROM usage_backfills)",
            (STARTED_KEY,),
        ).fetchone()
        return None if row[0] is None else datetime.fromisoformat(row[0])

    def first_seen(self, account_id: int, server_id: str) -> datetime | None:
        """The oldest record of a server: an event or a load point."""
        row = self._conn.execute(
            "SELECT MIN(at) FROM (SELECT MIN(at) AS at FROM server_events"
            " WHERE account_id = ? AND server_id = ?"
            " UNION ALL SELECT MIN(at) FROM usage_samples WHERE account_id = ? AND server_id = ?"
            " UNION ALL SELECT MIN(hour) FROM usage_hourly WHERE account_id = ? AND server_id = ?)",
            (account_id, server_id) * 3,
        ).fetchone()
        return None if row[0] is None else datetime.fromisoformat(row[0])

    def first_balance(self, account_id: int) -> datetime | None:
        """When the account's balance was first read."""
        row = self._conn.execute(
            "SELECT MIN(at) FROM balance_history WHERE account_id = ?", (account_id,)
        ).fetchone()
        return None if row[0] is None else datetime.fromisoformat(row[0])


def _filters(
    account_id: int | None,
    server_id: str | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[str], list[object]]:
    where: list[str] = ["1"]
    args: list[object] = []
    if account_id is not None:
        where.append("account_id = ?")
        args.append(account_id)
    if server_id is not None:
        where.append("server_id = ?")
        args.append(server_id)
    if since is not None:
        where.append("at >= ?")
        args.append(_ts(since))
    if until is not None:
        where.append("at < ?")
        args.append(_ts(until))
    return where, args


def _merge(a: UsagePoint, b: UsagePoint) -> UsagePoint:
    total = a.samples + b.samples
    avg: dict[str, float | None] = {}
    peak: dict[str, float | None] = {}
    for m in METRICS:
        x, y = a.avg[m], b.avg[m]
        avg[m] = y if x is None else x if y is None else (x * a.samples + y * b.samples) / total
        highs = [v for v in (a.max[m], b.max[m]) if v is not None]
        peak[m] = max(highs) if highs else None
    return UsagePoint(a.at, avg, peak, total)
