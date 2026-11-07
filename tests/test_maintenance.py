"""Backups, heartbeat, release check, public IP and message splitting."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import respx

from hostwatch.bot.app import split_text
from hostwatch.scheduler.maintenance import (
    RELEASES_URL,
    Backups,
    ReleaseCheck,
    ping,
    public_ip,
)
from hostwatch.storage.db import connect
from hostwatch.storage.repositories import SettingsRepository

MSK = ZoneInfo("Europe/Moscow")


async def test_nightly_backup_keeps_seven(tmp_path: Path) -> None:
    conn = connect(tmp_path / "hostwatch.sqlite3")
    settings = SettingsRepository(conn)
    settings.set("probe", "42")
    now = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
    backups = Backups(conn, tmp_path / "backups", settings, lambda: MSK, lambda: now)
    for day in range(1, 10):
        now = datetime(2026, 11, day, 0, 0, tzinfo=UTC)  # 03:00 in Moscow: too early
        await backups.tick()
        now = datetime(2026, 11, day, 1, 0, tzinfo=UTC)  # 04:00
        await backups.tick()
        await backups.tick()
    files = backups.files()
    assert len(files) == 7
    assert files[0].name == "hostwatch-20261103-040000.sqlite3"
    copy = sqlite3.connect(files[-1])
    assert copy.execute("SELECT value FROM settings WHERE key = 'probe'").fetchone() == ("42",)
    copy.close()
    conn.close()


def test_snapshot_is_a_whole_database(tmp_path: Path) -> None:
    conn = connect(tmp_path / "hostwatch.sqlite3")
    SettingsRepository(conn).set("probe", "1")
    backups = Backups(conn, tmp_path / "backups", SettingsRepository(conn), lambda: MSK)
    data = backups.snapshot()
    assert data.startswith(b"SQLite format 3")
    assert backups.files() == []
    conn.close()


@respx.mock
async def test_release_announced_once_and_errors_ignored(conn: sqlite3.Connection) -> None:
    announced: list[tuple[str, str]] = []

    async def announce(version: str, url: str) -> bool:
        announced.append((version, url))
        return True

    route = respx.get(RELEASES_URL)
    settings = SettingsRepository(conn)
    async with httpx.AsyncClient() as http:
        check = ReleaseCheck(http, "0.0.1", settings, announce)
        route.mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        await check.tick()
        route.mock(side_effect=httpx.ConnectError("down"))
        await check.tick()
        route.mock(return_value=httpx.Response(200, json={"tag_name": "v0.0.1", "html_url": "u"}))
        await check.tick()
        assert announced == []
        release = {"tag_name": "v1.0.0", "html_url": "https://example.org/r"}
        route.mock(return_value=httpx.Response(200, json=release))
        await check.tick()
        await check.tick()
    assert announced == [("v1.0.0", "https://example.org/r")]


@respx.mock
async def test_public_ip_falls_back_to_the_second_service() -> None:
    respx.get("https://one.invalid").mock(return_value=httpx.Response(500))
    respx.get("https://two.invalid").mock(return_value=httpx.Response(200, text="203.0.113.7\n"))
    respx.get("https://bad.invalid").mock(return_value=httpx.Response(200, text="<html>"))
    async with httpx.AsyncClient() as http:
        assert await public_ip(http, ("https://one.invalid", "https://two.invalid")) == (
            "203.0.113.7"
        )
        assert await public_ip(http, ("https://bad.invalid",)) is None


@respx.mock
async def test_heartbeat_failure_is_only_logged() -> None:
    route = respx.get("https://hc.invalid/ping/secret").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        await ping(http, "https://hc.invalid/ping/secret")
        route.mock(side_effect=httpx.ConnectError("down"))
        await ping(http, "https://hc.invalid/ping/secret")
    assert route.call_count == 2


def test_split_text_keeps_lines_whole() -> None:
    text = "\n".join(f"line {i:03}" for i in range(100))
    chunks = split_text(text, limit=50)
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert "\n".join(chunks) == text
    assert split_text("x" * 120, limit=50) == ["x" * 50, "x" * 50, "x" * 20]
    assert split_text("short") == ["short"]
