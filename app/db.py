"""SQLite access.

Changing a month (its parameters, a report, its stored results) happens in one
``BEGIN IMMEDIATE`` transaction, so everything either takes effect together or
the previous state survives untouched.  Long reads use a deferred transaction,
which in WAL mode sees a stable snapshot without blocking writers of other
months.

Download counts are arbitrary-precision integers: a monthly total or a
city-wide total can exceed SQLite's signed 64-bit INTEGER range.  They are
therefore stored as exact decimal strings in TEXT columns (the JSON payloads
already keep every digit), and databases created by older versions with
INTEGER columns are rebuilt automatically on startup.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS periods (
    period TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS period_journals (
    period TEXT NOT NULL REFERENCES periods(period) ON DELETE CASCADE,
    journal_no TEXT NOT NULL,
    PRIMARY KEY (period, journal_no)
);

CREATE TABLE IF NOT EXISTS period_libraries (
    period TEXT NOT NULL REFERENCES periods(period) ON DELETE CASCADE,
    library_no TEXT NOT NULL,
    PRIMARY KEY (period, library_no)
);

CREATE TABLE IF NOT EXISTS reports (
    period TEXT NOT NULL,
    library_no TEXT NOT NULL,
    top_json TEXT NOT NULL,
    total_downloads TEXT NOT NULL,
    tail_downloads TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (period, library_no),
    FOREIGN KEY (period, library_no)
        REFERENCES period_libraries(period, library_no) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS results (
    period TEXT NOT NULL REFERENCES periods(period) ON DELETE CASCADE,
    journal_no TEXT NOT NULL,
    total_downloads TEXT NOT NULL,
    support_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (period, journal_no)
);
"""

# Tables whose download-count columns used to be INTEGER.  The rebuild DDL
# mirrors the table above, only the download columns gain TEXT affinity, and the
# column order is unchanged so ``INSERT ... SELECT *`` copies every row as-is.
_BIGINT_TABLES: dict[str, str] = {
    "reports": """
CREATE TABLE reports_new (
    period TEXT NOT NULL,
    library_no TEXT NOT NULL,
    top_json TEXT NOT NULL,
    total_downloads TEXT NOT NULL,
    tail_downloads TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (period, library_no),
    FOREIGN KEY (period, library_no)
        REFERENCES period_libraries(period, library_no) ON DELETE CASCADE
)
""",
    "results": """
CREATE TABLE results_new (
    period TEXT NOT NULL REFERENCES periods(period) ON DELETE CASCADE,
    journal_no TEXT NOT NULL,
    total_downloads TEXT NOT NULL,
    support_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (period, journal_no)
)
""",
}

_BIGINT_COLUMNS: dict[str, tuple[str, ...]] = {
    "reports": ("total_downloads", "tail_downloads"),
    "results": ("total_downloads",),
}


def database_path() -> Path:
    return Path(os.environ.get("JOURNAL_DB_PATH", "journals.sqlite3"))


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 60000")
    return connection


def initialize() -> None:
    connection = connect()
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        connection.commit()
        _migrate_bigint_columns(connection)
    finally:
        connection.close()


def _column_types(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        row["name"]: (row["type"] or "").upper()
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _migrate_bigint_columns(connection: sqlite3.Connection) -> None:
    """Rebuild legacy tables so download counts keep every digit.

    Older releases declared the download columns as INTEGER, which cannot hold
    values past the signed 64-bit range.  TEXT affinity stores the exact
    decimal string; existing INTEGER values are converted by SQLite itself, so
    old reports and verdicts stay queryable with their original values.
    """
    stale = [
        table
        for table, columns in _BIGINT_COLUMNS.items()
        if any(
            _column_types(connection, table).get(column) != "TEXT"
            for column in columns
        )
    ]
    if not stale:
        return

    # foreign_keys cannot be toggled inside a transaction, so settle any open
    # one first; the whole rebuild is then one atomic transaction.
    connection.commit()
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in stale:
            connection.execute(_BIGINT_TABLES[table])
            connection.execute(
                f"INSERT INTO {table}_new SELECT * FROM {table}"
            )
            connection.execute(f"DROP TABLE {table}")
            connection.execute(
                f"ALTER TABLE {table}_new RENAME TO {table}"
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute(
            f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}"
        )


@contextmanager
def read_snapshot() -> Iterator[sqlite3.Connection]:
    """A consistent, non-blocking read view; writers keep working."""
    connection = connect()
    try:
        connection.execute("BEGIN DEFERRED")
        yield connection
    finally:
        connection.rollback()
        connection.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """One atomic unit of work; any exception undoes everything.

    The commit itself sits inside the guarded block: a failed commit rolls
    back as well, so a save error cannot leave a half-applied state.
    """
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
