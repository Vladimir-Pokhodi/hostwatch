"""Housekeeping jobs: database backups with the nightly history rollup, heartbeat, release
check, own public IP."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, tzinfo
from pathlib import Path

import httpx

from hostwatch.storage.history import HistoryRepository
from hostwatch.storage.repositories import SettingsRepository

log = logging.getLogger(__name__)

BACKUPS_KEPT = 7
BACKUP_AT = time(3, 30)
BACKUP_DONE_KEY = "backup.last_date"
RELEASES_URL = "https://api.github.com/repos/Vladimir-Pokhodi/hostwatch/releases/latest"
RELEASE_ANNOUNCED_KEY = "release.announced"
IP_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip")


async def every(seconds: float, job: Callable[[], Awaitable[None]], name: str) -> None:
    """Run `job` now and then every `seconds`. A failure is logged and does not stop it."""
    while True:
        try:
            await job()
        except Exception:
            log.exception("job %s failed", name)
        await asyncio.sleep(seconds)


class Backups:
    """`.backup` of the live database into `folder`, one a night, the last 7 kept.

    Right after the backup, load samples older than 30 days are rolled into hours.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        folder: Path,
        settings: SettingsRepository,
        tz: Callable[[], tzinfo],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        keep: int = BACKUPS_KEPT,
    ) -> None:
        self._conn = conn
        self.folder = folder
        self._settings = settings
        self._tz = tz
        self._now = now
        self._keep = keep

    async def tick(self) -> None:
        local = self._now().astimezone(self._tz())
        today = local.date().isoformat()
        if local.time() < BACKUP_AT or self._settings.get(BACKUP_DONE_KEY) == today:
            return
        path = self.make()
        self._settings.set(BACKUP_DONE_KEY, today)
        log.info("database backup written to %s", path)
        rolled = HistoryRepository(self._conn).rollup(self._now())
        log.info("%d load samples rolled into hours", rolled)

    def make(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = self._now().astimezone(self._tz()).strftime("%Y%m%d-%H%M%S")
        path = self.folder / f"hostwatch-{stamp}.sqlite3"
        self._copy_to(path)
        self._prune()
        return path

    def snapshot(self) -> bytes:
        """A fresh copy for the «Send backup» button. It does not count towards the 7."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hostwatch.sqlite3"
            self._copy_to(path)
            return path.read_bytes()

    def files(self) -> list[Path]:
        return sorted(self.folder.glob("hostwatch-*.sqlite3"))

    def _copy_to(self, path: Path) -> None:
        target = sqlite3.connect(path)
        try:
            self._conn.backup(target)
        finally:
            target.close()

    def _prune(self) -> None:
        for old in self.files()[: -self._keep]:
            old.unlink()


async def ping(http: httpx.AsyncClient, url: str) -> None:
    """Heartbeat for an outside monitor (spec, decision 17). The URL is a secret: not logged."""
    try:
        response = await http.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("heartbeat failed: %s", type(exc).__name__)


def _version(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return None if match is None else tuple(int(part) for part in match.group(1).split("."))


class ReleaseCheck:
    """Tells the owner once about a newer release on GitHub. Any failure is ignored quietly:
    the repository may have no releases yet."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        current: str,
        settings: SettingsRepository,
        announce: Callable[[str, str], Awaitable[bool]],
        url: str = RELEASES_URL,
    ) -> None:
        self._http = http
        self._current = current
        self._settings = settings
        self._announce = announce
        self._url = url

    async def tick(self) -> None:
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
            data = response.json()
            tag, link = str(data["tag_name"]), str(data["html_url"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.debug("release check skipped: %s", type(exc).__name__)
            return
        latest, current = _version(tag), _version(self._current)
        if latest is None or current is None or latest <= current:
            return
        if self._settings.get(RELEASE_ANNOUNCED_KEY) == tag:
            return
        if await self._announce(tag, link):
            self._settings.set(RELEASE_ANNOUNCED_KEY, tag)


async def public_ip(http: httpx.AsyncClient, services: tuple[str, ...] = IP_SERVICES) -> str | None:
    """The address the world sees this server by, or None if no service answered."""
    for url in services:
        try:
            response = await http.get(url)
            response.raise_for_status()
            return str(ipaddress.ip_address(response.text.strip()))
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("public IP from %s failed: %s", url, type(exc).__name__)
    return None
