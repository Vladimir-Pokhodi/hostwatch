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
    # Top-up invoices: the hoster's invoice id, and the balance when «Paid» was pressed.
    """
    ALTER TABLE payments ADD COLUMN external_id TEXT NOT NULL DEFAULT '';
    ALTER TABLE payments ADD COLUMN balance_before TEXT;
    """,
    # Servers the owner stopped through the bot: no «stopped» alert for them (stage 10).
    """
    CREATE TABLE owner_stops (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (account_id, server_id)
    );
    """,
    # 5. Autopay (stage 11): servers the owner marked, and invoices autopay told the owner
    # about, so a held invoice is reported once, not every hour.
    """
    CREATE TABLE autopay_servers (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (account_id, server_id)
    );

    CREATE TABLE autopay_notices (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        invoice_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (account_id, invoice_id)
    );
    """,
    # 6. History (1.1, part 4), kept forever: load samples (the last 30 days, then rolled
    # into hours), hourly load, server changes, money in and out, and which servers had
    # their past load read from the hoster. A balance row is now written only on a
    # change; `checked_at` says when the same balance was last read.
    """
    CREATE TABLE usage_samples (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        at TEXT NOT NULL,
        cpu REAL,
        ram REAL,
        disk REAL,
        traffic_gb REAL,
        net_in_mbits REAL,
        net_out_mbits REAL,
        PRIMARY KEY (account_id, server_id, at)
    );
    CREATE INDEX usage_samples_at ON usage_samples (at);

    CREATE TABLE usage_hourly (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        hour TEXT NOT NULL,
        cpu_avg REAL,
        cpu_max REAL,
        ram_avg REAL,
        ram_max REAL,
        disk_avg REAL,
        disk_max REAL,
        traffic_gb_avg REAL,
        traffic_gb_max REAL,
        net_in_mbits_avg REAL,
        net_in_mbits_max REAL,
        net_out_mbits_avg REAL,
        net_out_mbits_max REAL,
        samples INTEGER NOT NULL,
        PRIMARY KEY (account_id, server_id, hour)
    );

    CREATE TABLE server_events (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        old TEXT,
        new TEXT
    );
    CREATE INDEX server_events_server ON server_events (account_id, server_id, at);
    CREATE INDEX server_events_at ON server_events (at);

    CREATE TABLE money_events (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('topup', 'charge')),
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        server_id TEXT,
        source TEXT NOT NULL CHECK (source IN ('observed', 'hoster')),
        external_id TEXT,
        description TEXT NOT NULL DEFAULT '',
        UNIQUE (account_id, source, external_id)
    );
    CREATE INDEX money_events_account_at ON money_events (account_id, at);

    CREATE TABLE usage_backfills (
        account_id INTEGER NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
        server_id TEXT NOT NULL,
        at TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        done INTEGER NOT NULL,
        points INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (account_id, server_id)
    );

    ALTER TABLE balance_history ADD COLUMN checked_at TEXT;
    """,
    # 7. The journal also takes the bot's own actions (actor 'system'). SQLite cannot
    # change a CHECK constraint, so the table is rebuilt with every row kept.
    """
    CREATE TABLE journal_new (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (actor IN ('owner', 'autopay', 'system')),
        account_id INTEGER REFERENCES accounts (id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        params TEXT NOT NULL,
        response TEXT NOT NULL,
        ok INTEGER NOT NULL
    );
    INSERT INTO journal_new (id, at, actor, account_id, action, params, response, ok)
        SELECT id, at, actor, account_id, action, params, response, ok FROM journal;
    DROP TABLE journal;
    ALTER TABLE journal_new RENAME TO journal;
    CREATE INDEX journal_at ON journal (at);
    CREATE INDEX journal_account_at ON journal (account_id, at);
    CREATE INDEX journal_action ON journal (action);
    """,
    # 8. When the history began, for the Mini App: «no data yet, kept since 09.11».
    """
    INSERT OR IGNORE INTO settings (key, value)
        VALUES ('history.started', strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));
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
