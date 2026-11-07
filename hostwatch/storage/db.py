"""SQLite connection and schema migrations.

Each migration is one SQL script. The applied version is kept in `PRAGMA user_version`.
Add new migrations to the end of the list; never edit the ones already released.
"""

import sqlite3
from pathlib import Path

MIGRATIONS: list[str] = [
    """
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        plugin_id TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok'
            CHECK (status IN ('ok', 'relogin', 'error', 'disabled')),
        last_error TEXT,
        last_ok_at TEXT,
        secrets BLOB NOT NULL,
        UNIQUE (plugin_id, label)
    );

    CREATE TABLE servers (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        external_id TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        ip TEXT,
        plan TEXT,
        status TEXT NOT NULL,
        raw_status TEXT NOT NULL,
        price_amount TEXT,
        price_currency TEXT,
        period_months INTEGER,
        next_charge_at TEXT,
        autorenew TEXT NOT NULL,
        billing TEXT NOT NULL,
        actions TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_id, external_id)
    );

    CREATE TABLE balance_history (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL
    );
    CREATE INDEX balance_history_account_at ON balance_history (account_id, at);

    CREATE TABLE usage_cache (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        measured_at TEXT NOT NULL,
        cpu_percent REAL,
        ram_percent REAL,
        disk_percent REAL,
        traffic_gb REAL,
        PRIMARY KEY (account_id, server_id)
    );

    CREATE TABLE alerts (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        sent_at TEXT NOT NULL
    );

    CREATE TABLE journal (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (actor IN ('owner', 'autopay')),
        account_id INTEGER REFERENCES accounts (id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        params TEXT NOT NULL,
        response TEXT NOT NULL,
        ok INTEGER NOT NULL
    );
    CREATE INDEX journal_at ON journal (at);

    CREATE TABLE payments (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        method TEXT NOT NULL,
        url TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL
    );

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # The hoster's own estimate of how long the balance lasts (Timeweb), kept with the balance.
    """
    ALTER TABLE balance_history ADD COLUMN hours_left REAL;
    """,
]


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    return version


def migrate(conn: sqlite3.Connection) -> None:
    current = schema_version(conn)
    if current > len(MIGRATIONS):
        raise RuntimeError(f"database schema {current} is newer than this hostwatch knows")
    for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except BaseException:
            conn.rollback()
            raise
