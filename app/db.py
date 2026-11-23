"""SQLite access.

Changing a month (its parameters, a report, its stored results) happens in one
``BEGIN IMMEDIATE`` transaction, so everything either takes effect together or
the previous state survives untouched.  Long reads use a deferred transaction,
which in WAL mode sees a stable snapshot without blocking writers of other
months.
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
    total_downloads INTEGER NOT NULL,
    tail_downloads INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (period, library_no),
    FOREIGN KEY (period, library_no)
        REFERENCES period_libraries(period, library_no) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS results (
    period TEXT NOT NULL REFERENCES periods(period) ON DELETE CASCADE,
    journal_no TEXT NOT NULL,
    total_downloads INTEGER NOT NULL,
    support_json TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (period, journal_no)
);
"""


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
    finally:
        connection.close()


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
