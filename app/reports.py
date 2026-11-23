"""Report intake, validation and the missing libraries view."""

from __future__ import annotations

import json
import sqlite3

from .db import utc_now
from .periods import journals_of
from .schemas import ReportIn, TopEntry


class ReportError(ValueError):
    pass


class ReportNotFound(LookupError):
    pass


def _check_fillable(
    top: list[TopEntry],
    total_downloads: int,
    tail_downloads: int,
    journals: list[str],
) -> None:
    """The submitted report must be completable within the month's scope.

    A leaderboard may name journals that are not taking part this month; they
    belong to the library's own list and are kept, but they neither count
    towards the participant total nor receive a fill.  Among the participating
    journals the leaderboard does not list, the leftover
    ``total_downloads - listed participant counts`` is spread between zero and
    the tail count each.  If the leaderboard names every participant there is
    nothing left to absorb.
    """
    scope = set(journals)
    listed = {entry.journal_no for entry in top} & scope
    outside = len(scope) - len(listed)
    remaining = total_downloads - sum(
        entry.downloads for entry in top if entry.journal_no in scope
    )
    if remaining < 0:
        raise ReportError("the leaderboard sum exceeds total_downloads")
    if outside == 0 and remaining != 0:
        raise ReportError(
            "the leaderboard names every participating journal, so"
            " total_downloads must equal the listed participant counts"
        )
    if outside > 0 and remaining > outside * tail_downloads:
        raise ReportError(
            "the participating journals outside the leaderboard cannot absorb"
            " total_downloads within the tail count"
        )


def _encode(report: ReportIn) -> str:
    return json.dumps(
        {
            "top": [[entry.journal_no, entry.downloads] for entry in report.top],
            "total_downloads": report.total_downloads,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode(row: sqlite3.Row) -> dict[str, object]:
    payload = json.loads(row["top_json"])
    return {
        "library_no": row["library_no"],
        "top": [
            {"journal_no": journal, "downloads": count}
            for journal, count in payload["top"]
        ],
        "total_downloads": payload["total_downloads"],
        "tail_downloads": payload["top"][-1][1],
        "revision": row["revision"],
        "updated_at": row["updated_at"],
    }


def save_report(
    connection: sqlite3.Connection,
    period: str,
    library_no: str,
    report: ReportIn,
) -> dict[str, object]:
    """First submission or a correction; a correction replaces everything."""
    tail = report.tail_downloads
    _check_fillable(report.top, report.total_downloads, tail, journals_of(connection, period))
    previous = connection.execute(
        "SELECT revision FROM reports WHERE period = ? AND library_no = ?",
        (period, library_no),
    ).fetchone()
    revision = 1 if previous is None else previous["revision"] + 1
    connection.execute(
        "INSERT INTO reports (period, library_no, top_json, total_downloads,"
        " tail_downloads, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(period, library_no) DO UPDATE SET"
        " top_json = excluded.top_json,"
        " total_downloads = excluded.total_downloads,"
        " tail_downloads = excluded.tail_downloads,"
        " revision = excluded.revision,"
        " updated_at = excluded.updated_at",
        (
            period,
            library_no,
            _encode(report),
            report.total_downloads,
            tail,
            revision,
            utc_now(),
        ),
    )
    return get_report(connection, period, library_no)


def list_reports(connection: sqlite3.Connection, period: str) -> list[dict[str, object]]:
    return [
        _decode(row)
        for row in connection.execute(
            "SELECT * FROM reports WHERE period = ? ORDER BY library_no", (period,)
        )
    ]


def get_report(
    connection: sqlite3.Connection, period: str, library_no: str
) -> dict[str, object]:
    row = connection.execute(
        "SELECT * FROM reports WHERE period = ? AND library_no = ?",
        (period, library_no),
    ).fetchone()
    if row is None:
        raise ReportNotFound(f"library {library_no} has not reported for {period}")
    return _decode(row)


def missing_libraries(connection: sqlite3.Connection, period: str) -> list[str]:
    """Only the libraries that still owe a report, in library order."""
    return [
        row["library_no"]
        for row in connection.execute(
            "SELECT l.library_no FROM period_libraries AS l"
            " LEFT JOIN reports AS r"
            " ON r.period = l.period AND r.library_no = l.library_no"
            " WHERE l.period = ? AND r.library_no IS NULL"
            " ORDER BY l.library_no",
            (period,),
        )
    ]
