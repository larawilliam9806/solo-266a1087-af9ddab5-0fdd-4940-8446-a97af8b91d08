"""Monthly parameters: journals in scope and the libraries that must report."""

from __future__ import annotations

import sqlite3

from .db import utc_now

MAX_JOURNALS = 30
MAX_LIBRARIES = 8


class PeriodError(ValueError):
    pass


class NotFound(LookupError):
    pass


def define_period(
    connection: sqlite3.Connection,
    period: str,
    journals: list[str],
    libraries: list[str],
) -> None:
    """Create the month, or redefine it while nobody has reported yet.

    Once a report exists the question is frozen: letting the scope change
    afterwards would silently alter what earlier libraries were reporting on.
    """
    if not journals:
        raise PeriodError("at least one journal is required")
    if not libraries:
        raise PeriodError("at least one library is required")
    if len(set(journals)) != len(journals):
        raise PeriodError("journal numbers must be unique")
    if len(set(libraries)) != len(libraries):
        raise PeriodError("library numbers must be unique")

    exists = connection.execute(
        "SELECT 1 FROM periods WHERE period = ?", (period,)
    ).fetchone()
    if exists is not None:
        reported = connection.execute(
            "SELECT 1 FROM reports WHERE period = ? LIMIT 1", (period,)
        ).fetchone()
        if reported is not None:
            raise PeriodError(
                "reports already exist for this month, so its parameters are frozen"
            )
        connection.execute("DELETE FROM results WHERE period = ?", (period,))
        connection.execute("DELETE FROM period_journals WHERE period = ?", (period,))
        connection.execute("DELETE FROM period_libraries WHERE period = ?", (period,))
    else:
        connection.execute(
            "INSERT INTO periods (period, created_at) VALUES (?, ?)",
            (period, utc_now()),
        )
    connection.executemany(
        "INSERT INTO period_journals (period, journal_no) VALUES (?, ?)",
        [(period, journal) for journal in journals],
    )
    connection.executemany(
        "INSERT INTO period_libraries (period, library_no) VALUES (?, ?)",
        [(period, library) for library in libraries],
    )


def require_period(connection: sqlite3.Connection, period: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM periods WHERE period = ?", (period,)
    ).fetchone()
    if row is None:
        raise NotFound(f"month {period} has not been set up")


def require_library(connection: sqlite3.Connection, period: str, library: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM period_libraries WHERE period = ? AND library_no = ?",
        (period, library),
    ).fetchone()
    if row is None:
        raise NotFound(f"library {library} does not report for {period}")


def journals_of(connection: sqlite3.Connection, period: str) -> list[str]:
    return [
        row["journal_no"]
        for row in connection.execute(
            "SELECT journal_no FROM period_journals WHERE period = ?"
            " ORDER BY journal_no",
            (period,),
        )
    ]


def libraries_of(connection: sqlite3.Connection, period: str) -> list[str]:
    return [
        row["library_no"]
        for row in connection.execute(
            "SELECT library_no FROM period_libraries WHERE period = ?"
            " ORDER BY library_no",
            (period,),
        )
    ]
