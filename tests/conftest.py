import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DB_DIR = tempfile.mkdtemp(prefix="journal-api-tests-")
os.environ.setdefault("JOURNAL_DB_PATH", str(Path(DB_DIR) / "test.sqlite3"))

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    # A fresh database per test keeps months independent.
    path = Path(tempfile.mkdtemp(prefix="journal-api-")) / "test.sqlite3"
    os.environ["JOURNAL_DB_PATH"] = str(path)
    with TestClient(app) as test_client:
        yield test_client


def setup_period(client, period="2026-05", journals=None, libraries=None):
    journals = journals or ["J1", "J2", "J3", "J4"]
    libraries = libraries or ["L1", "L2"]
    response = client.post(
        "/periods",
        json={"period": period, "journals": journals, "libraries": libraries},
    )
    assert response.status_code == 201, response.text
    return response.json()


def report(client, library, top, total, period="2026-05"):
    return client.put(
        f"/periods/{period}/libraries/{library}/report",
        json={
            "top": [
                {"journal_no": journal, "downloads": downloads}
                for journal, downloads in top
            ],
            "total_downloads": total,
        },
    )
