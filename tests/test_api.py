import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import report, setup_period


def champions(client, period="2026-05"):
    response = client.get(f"/periods/{period}/champions")
    assert response.status_code == 200, response.text
    return response.json()


def winners(client, period="2026-05"):
    return {
        entry["journal_no"]: entry["total_downloads"]
        for entry in champions(client, period)["champions"]
    }


def completion_totals(support):
    city = {}
    for library in support:
        for download in library["downloads"]:
            city[download["journal_no"]] = (
                city.get(download["journal_no"], 0) + download["downloads"]
            )
    return city


def test_parameters_limits_and_validation(client):
    response = client.post(
        "/periods",
        json={
            "period": "2026-01",
            "journals": [f"J{i}" for i in range(31)],
            "libraries": ["L1"],
        },
    )
    assert response.status_code == 422
    response = client.post(
        "/periods",
        json={
            "period": "2026-01",
            "journals": ["J1"],
            "libraries": [f"L{i}" for i in range(9)],
        },
    )
    assert response.status_code == 422
    response = client.post(
        "/periods",
        json={"period": "2026-13", "journals": ["J1"], "libraries": ["L1"]},
    )
    assert response.status_code == 422
    response = client.post(
        "/periods",
        json={"period": "2026-01", "journals": ["J1", "J1"], "libraries": ["L1"]},
    )
    assert response.status_code == 422

    setup_period(client, "2026-01", ["J1", "J2"], ["L1", "L2"])


def test_limits_are_thirty_and_eight(client):
    journals = [f"J{i:02d}" for i in range(30)]
    libraries = [f"L{i}" for i in range(8)]
    setup_period(client, "2026-02", journals, libraries)
    body = client.get("/periods/2026-02").json()
    assert len(body["journals"]) == 30
    assert len(body["libraries"]) == 8


def test_missing_lists_only_unreported_libraries(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2", "L3"])
    assert champions(client)["missing_libraries"] == ["L1", "L2", "L3"]

    assert report(client, "L2", [["J1", 5]], 5).status_code == 200
    assert client.get("/periods/2026-05/missing").json() == {
        "period": "2026-05",
        "missing_libraries": ["L1", "L3"],
    }
    assert champions(client)["missing_libraries"] == ["L1", "L3"]
    assert champions(client)["champions"] == []


def test_report_validation(client):
    setup_period(client)
    assert report(client, "L1", [], 10).status_code == 422
    # a non-participating journal is fine; four participants still owe fills
    assert report(client, "L1", [["J9", 5]], 21).status_code == 422
    assert report(client, "L1", [["J9", 5]], 20).status_code == 200
    # an unsorted leaderboard is ranked on the way in
    out_of_order = report(client, "L1", [["J2", 3], ["J1", 5]], 8)
    assert out_of_order.status_code == 200
    assert out_of_order.json()["top"] == [
        {"journal_no": "J1", "downloads": 5},
        {"journal_no": "J2", "downloads": 3},
    ]
    assert out_of_order.json()["tail_downloads"] == 3
    # repeated journal
    assert report(client, "L1", [["J1", 5], ["J1", 3]], 8).status_code == 422
    # total below leaderboard sum
    assert report(client, "L1", [["J1", 5]], 4).status_code == 422
    # three participating journals outside the leaderboard, tail 5, can absorb 15
    assert report(client, "L1", [["J1", 5]], 20).status_code == 200
    # but not 16
    assert report(client, "L1", [["J1", 5]], 21).status_code == 422


def test_report_returns_stored_form(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    response = report(client, "L1", [["J1", 5], ["J2", 3]], 8)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "library_no": "L1",
        "top": [
            {"journal_no": "J1", "downloads": 5},
            {"journal_no": "J2", "downloads": 3},
        ],
        "total_downloads": 8,
        "tail_downloads": 3,
        "revision": 1,
        "updated_at": response.json()["updated_at"],
    }


def test_completed_month_lists_all_possible_winners(client):
    setup_period(client)
    assert report(client, "L1", [["J1", 10]], 12).status_code == 200
    assert report(client, "L2", [["J2", 10]], 12).status_code == 200

    body = champions(client)
    assert body["missing_libraries"] == []
    assert {entry["journal_no"] for entry in body["champions"]} == {"J1", "J2"}

    for entry in body["champions"]:
        assert len(entry["support"]) == 2
        city = completion_totals(entry["support"])
        assert city[entry["journal_no"]] == entry["total_downloads"]
        assert max(city.values()) == entry["total_downloads"]
        for library in entry["support"]:
            assert {download["journal_no"] for download in library["downloads"]} == {
                "J1",
                "J2",
                "J3",
                "J4",
            }
            assert sum(d["downloads"] for d in library["downloads"]) == 12


def test_ties_share_first_place(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    report(client, "L1", [["J1", 5]], 9)
    report(client, "L2", [["J2", 5]], 9)
    assert winners(client) == {"J1": 9, "J2": 9}
    # a single completion shows both at the top simultaneously
    entry = next(e for e in champions(client)["champions"] if e["journal_no"] == "J1")
    assert completion_totals(entry["support"]) == {"J1": 9, "J2": 9}


def test_journal_can_win_while_below_another_journals_bound(client):
    setup_period(client, journals=["J1", "J2", "J3"], libraries=["L1", "L2"])
    # L1 fixes J1=4; J2 and J3 may each take 0..2.
    report(client, "L1", [["J1", 4]], 6)
    # L2 fixes J2=4 and J3=3; J1 may take 0..3 but the total only leaves 1.
    report(client, "L2", [["J2", 4], ["J3", 3]], 8)
    assert winners(client) == {"J1": 5, "J2": 6, "J3": 5}


def test_correction_replaces_report_and_result(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    report(client, "L1", [["J1", 5]], 5)
    report(client, "L2", [["J2", 5]], 5)
    assert winners(client) == {"J1": 5, "J2": 5}

    response = report(client, "L1", [["J2", 7]], 7)
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    stored = client.get("/periods/2026-05/libraries/L1/report").json()
    assert stored["top"] == [{"journal_no": "J2", "downloads": 7}]
    # the old J1 top entry is gone, not merged
    assert winners(client) == {"J2": 12}


def test_new_result_includes_earlier_reports(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    report(client, "L1", [["J1", 5]], 5)
    assert champions(client)["champions"] == []
    report(client, "L2", [["J2", 3]], 3)
    assert winners(client) == {"J1": 5}


def test_rejected_report_leaves_previous_state(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    assert report(client, "L1", [["J1", 5]], 5).status_code == 200
    assert report(client, "L2", [["J2", 5]], 5).status_code == 200
    before = champions(client)

    # Two outside journals, tail 2, cannot absorb 100.
    assert report(client, "L1", [["J1", 5]], 105).status_code == 422
    assert champions(client) == before
    assert client.get("/periods/2026-05/libraries/L1/report").json()["top"] == [
        {"journal_no": "J1", "downloads": 5}
    ]


def test_other_months_report_while_one_is_open(client):
    setup_period(client, "2026-05", ["J1", "J2"], ["L1", "L2"])
    setup_period(client, "2026-06", ["J1", "J2"], ["L1", "L2"])
    report(client, "L1", [["J1", 4]], 4, period="2026-05")
    report(client, "L1", [["J1", 2]], 2, period="2026-06")
    report(client, "L2", [["J2", 2]], 2, period="2026-06")
    assert client.get("/periods/2026-05/missing").json()["missing_libraries"] == ["L2"]
    assert client.get("/periods/2026-06/missing").json()["missing_libraries"] == []
    assert winners(client, "2026-06") == {"J1": 2, "J2": 2}
    assert champions(client, "2026-05")["champions"] == []


def test_results_stay_readable_and_stable(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    report(client, "L1", [["J1", 5]], 5)
    report(client, "L2", [["J2", 5]], 5)
    first = champions(client)
    assert first["champions"]
    assert champions(client) == first
    assert client.get("/periods/2026-05/reports").status_code == 200


def test_parameters_frozen_after_first_report(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    report(client, "L1", [["J1", 5]], 5)
    response = client.post(
        "/periods",
        json={"period": "2026-05", "journals": ["J3"], "libraries": ["L1"]},
    )
    assert response.status_code == 422
    assert client.get("/periods/2026-05").json()["journals"] == ["J1", "J2"]


def test_every_journal_listed_needs_exact_total(client):
    setup_period(client, journals=["J1", "J2"], libraries=["L1", "L2"])
    assert report(client, "L1", [["J1", 5], ["J2", 3]], 9).status_code == 422
    assert report(client, "L1", [["J1", 5], ["J2", 3]], 8).status_code == 200


def test_unknown_month_and_library(client):
    assert client.get("/periods/2020-01/champions").status_code == 404
    setup_period(client)
    assert report(client, "L9", [["J1", 5]], 5).status_code == 404
    assert client.get("/periods/2026-05/libraries/L9/report").status_code == 404


def test_leaderboard_may_name_non_participating_journals(client):
    """The library's own list is not limited to the journals under review.

    Such entries are kept, do not count towards the participant total, and
    still count in the library's own ranking.
    """
    setup_period(client, journals=["J1", "J2", "J3"], libraries=["L1", "L2"])
    response = report(client, "L1", [["OUT1", 8], ["J1", 5]], 5 + 2 * 5)
    assert response.status_code == 200, response.text
    assert response.json()["top"][0] == {"journal_no": "OUT1", "downloads": 8}
    assert response.json()["tail_downloads"] == 5
    # Two participants are filled, each capped by tail 5: at most 10 more.
    assert report(client, "L1", [["OUT1", 40], ["J1", 5]], 15).status_code == 200
    assert report(client, "L1", [["OUT1", 40], ["J1", 5]], 16).status_code == 422


def test_save_failure_keeps_previous_state(monkeypatch):
    path = Path(tempfile.mkdtemp(prefix="journal-api-")) / "test.sqlite3"
    monkeypatch.setenv("JOURNAL_DB_PATH", str(path))
    import app.main as main

    with TestClient(main.app, raise_server_exceptions=False) as failing_client:
        setup_period(failing_client, journals=["J1", "J2"], libraries=["L1", "L2"])
        report(failing_client, "L1", [["J1", 5]], 5)
        report(failing_client, "L2", [["J2", 5]], 5)
        before = champions(failing_client)

        def explode(*args, **kwargs):
            raise RuntimeError("disk full")

        original = main.save_results
        monkeypatch.setattr(main, "save_results", explode)
        response = report(failing_client, "L1", [["J1", 9]], 9)
        assert response.status_code == 500
        monkeypatch.setattr(main, "save_results", original)

        assert champions(failing_client) == before
        stored = failing_client.get("/periods/2026-05/libraries/L1/report").json()
        assert stored["top"] == [{"journal_no": "J1", "downloads": 5}]


def test_months_do_not_interfere_under_concurrent_use(client):
    import concurrent.futures

    setup_period(client, "2026-07", ["J1", "J2"], ["L1", "L2"])
    setup_period(client, "2026-08", ["J1", "J2"], ["L1", "L2"])

    def work(period, library):
        return report(
            client, library, [["J1", 5]], 5, period=period
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(work, period, library)
            for period in ("2026-07", "2026-08")
            for library in ("L1", "L2")
        ]
        assert all(future.result() == 200 for future in futures)

    for period in ("2026-07", "2026-08"):
        body = client.get(f"/periods/{period}/champions").json()
        assert body["missing_libraries"] == []
        assert {entry["journal_no"] for entry in body["champions"]} == {"J1"}
