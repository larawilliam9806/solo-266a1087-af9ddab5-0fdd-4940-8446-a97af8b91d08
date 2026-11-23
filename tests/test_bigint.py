"""Regression tests for counts beyond SQLite's signed 64-bit INTEGER range.

A monthly total or a city-wide total may run past 2**63 - 1.  The API keeps
exchanging JSON integers (every digit preserved), storage keeps the exact
value, the ranking engine stays exact, and the flow's work does not grow with
the magnitude of the counts.
"""

import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import report, setup_period

# Far above the signed 64-bit maximum (9223372036854775807).
BIG = 10**40


def test_report_accepts_and_returns_counts_beyond_int64(client):
    setup_period(client, journals=["J1", "J2", "J3"], libraries=["L1", "L2"])
    response = report(client, "L1", [["J1", BIG]], 3 * BIG)
    assert response.status_code == 200, response.text
    body = response.json()
    assert type(body["total_downloads"]) is int
    assert body["total_downloads"] == 3 * BIG
    assert type(body["tail_downloads"]) is int
    assert body["tail_downloads"] == BIG
    assert body["top"] == [{"journal_no": "J1", "downloads": BIG}]

    # The wire format is a bare JSON integer, never a string or float.
    raw = client.get("/periods/2026-05/libraries/L1/report").content.decode()
    assert str(BIG) in raw
    assert f'"{BIG}"' not in raw
    assert "e+" not in raw and "E+" not in raw


def test_champions_keep_exact_counts_and_valid_completions(client):
    setup_period(client, journals=["J1", "J2", "J3"], libraries=["L1", "L2"])
    assert report(client, "L1", [["J1", BIG]], 3 * BIG).status_code == 200
    assert report(client, "L2", [["J2", BIG]], 3 * BIG).status_code == 200

    body = client.get("/periods/2026-05/champions").json()
    assert body["missing_libraries"] == []
    winners = {
        entry["journal_no"]: entry["total_downloads"]
        for entry in body["champions"]
    }
    # Every journal can take the tail cap in the library that did not list it.
    assert winners == {"J1": 2 * BIG, "J2": 2 * BIG, "J3": 2 * BIG}

    for entry in body["champions"]:
        assert type(entry["total_downloads"]) is int
        for library in entry["support"]:
            counts = [row["downloads"] for row in library["downloads"]]
            assert all(type(value) is int for value in counts)
            # The completion still reconciles to the exact reported total.
            assert sum(counts) == 3 * BIG

    raw = client.get("/periods/2026-05/champions").content.decode()
    assert str(2 * BIG) in raw
    assert f'"{2 * BIG}"' not in raw


def test_huge_counts_still_honour_fillable_validation(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1"])
    # One participant outside the leaderboard, tail BIG, can absorb at most BIG.
    assert report(client, "L1", [["J1", BIG]], 2 * BIG).status_code == 200
    assert report(client, "L1", [["J1", BIG]], 2 * BIG + 1).status_code == 422


def test_big_correction_rolls_back_together_on_failure(monkeypatch):
    directory = Path(tempfile.mkdtemp(prefix="journal-api-rollback-"))
    monkeypatch.setenv("JOURNAL_DB_PATH", str(directory / "rollback.sqlite3"))
    import app.main as main

    with TestClient(main.app, raise_server_exceptions=False) as client:
        setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
        assert report(client, "L1", [["J1", 5]], 5).status_code == 200
        assert report(client, "L2", [["J2", 5]], 5).status_code == 200
        before = client.get("/periods/2026-05/champions").json()

        def explode(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(main, "save_results", explode)
        response = report(client, "L1", [["J1", BIG]], BIG)
        assert response.status_code == 500

        after = client.get("/periods/2026-05/champions").json()
        assert after == before
        stored = client.get("/periods/2026-05/libraries/L1/report").json()
        assert stored["top"] == [{"journal_no": "J1", "downloads": 5}]


def test_legacy_integer_database_migrates_and_keeps_every_value(monkeypatch):
    directory = Path(tempfile.mkdtemp(prefix="journal-api-legacy-"))
    path = directory / "legacy.sqlite3"

    # Reproduce the previous release's schema: INTEGER download columns.
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE periods (
            period TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE period_journals (
            period TEXT NOT NULL, journal_no TEXT NOT NULL,
            PRIMARY KEY (period, journal_no));
        CREATE TABLE period_libraries (
            period TEXT NOT NULL, library_no TEXT NOT NULL,
            PRIMARY KEY (period, library_no));
        CREATE TABLE reports (
            period TEXT NOT NULL, library_no TEXT NOT NULL, top_json TEXT NOT NULL,
            total_downloads INTEGER NOT NULL, tail_downloads INTEGER NOT NULL,
            revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (period, library_no));
        CREATE TABLE results (
            period TEXT NOT NULL, journal_no TEXT NOT NULL,
            total_downloads INTEGER NOT NULL, support_json TEXT NOT NULL,
            computed_at TEXT NOT NULL, PRIMARY KEY (period, journal_no));
        """
    )
    connection.execute(
        "INSERT INTO periods VALUES ('2026-05', '2026-01-01T00:00:00+00:00')"
    )
    connection.executemany(
        "INSERT INTO period_journals VALUES (?, ?)",
        [("2026-05", "J1"), ("2026-05", "J2")],
    )
    connection.executemany(
        "INSERT INTO period_libraries VALUES (?, ?)",
        [("2026-05", "L1"), ("2026-05", "L2")],
    )
    connection.execute(
        "INSERT INTO reports VALUES ('2026-05','L1',?,'5','5',1,"
        "'2026-01-01T00:00:00+00:00')",
        ('{"top":[["J1",5]],"total_downloads":5}',),
    )
    connection.execute(
        "INSERT INTO results VALUES ('2026-05','J1','5','[]',"
        "'2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv("JOURNAL_DB_PATH", str(path))
    import app.main as main

    with TestClient(main.app) as migrated:
        # Old rows are directly queryable, exact values intact.
        old_report = migrated.get(
            "/periods/2026-05/libraries/L1/report"
        ).json()
        assert old_report["total_downloads"] == 5
        assert old_report["revision"] == 1
        stored = migrated.get("/periods/2026-05/reports").json()
        assert stored[0]["tail_downloads"] == 5

        # The stored verdict survived the rebuild and stays readable.
        connection = sqlite3.connect(path)
        report_types = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(reports)")
        }
        result_types = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info(results)")
        }
        assert report_types["total_downloads"] == "TEXT"
        assert report_types["tail_downloads"] == "TEXT"
        assert report_types["revision"] == "INTEGER"
        assert result_types["total_downloads"] == "TEXT"
        assert (
            connection.execute(
                "SELECT total_downloads FROM reports"
            ).fetchone()[0]
            == "5"
        )
        assert (
            connection.execute(
                "SELECT total_downloads FROM results"
            ).fetchone()[0]
            == "5"
        )
        connection.close()

        # A big correction now lands; libraries keep swapping reports.
        response = report(migrated, "L1", [["J1", BIG]], BIG)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 2
        assert response.json()["total_downloads"] == BIG
        response = report(migrated, "L2", [["J2", BIG]], BIG)
        assert response.status_code == 200
        winners = {
            entry["journal_no"]: entry["total_downloads"]
            for entry in migrated.get(
                "/periods/2026-05/champions"
            ).json()["champions"]
        }
        assert winners == {"J1": BIG, "J2": BIG}

    # Restarting must not rebuild again: values and columns stay as they are.
    with TestClient(main.app) as again:
        assert (
            again.get("/periods/2026-05/libraries/L1/report").json()[
                "total_downloads"
            ]
            == BIG
        )
        connection = sqlite3.connect(path)
        assert (
            connection.execute(
                "SELECT total_downloads FROM reports WHERE library_no = 'L1'"
            ).fetchone()[0]
            == str(BIG)
        )
        connection.close()
