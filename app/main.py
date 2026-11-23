"""Journal ranking verification API.

One question per month: after every library has filled in the journals it did
not put on its own leaderboard, which journals could be the most downloaded in
the city?  Ties share first place.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .db import initialize, read_snapshot, transaction
from .periods import (
    NotFound,
    PeriodError,
    define_period,
    journals_of,
    libraries_of,
    require_library,
    require_period,
)
from .ranking import champions, load_library_inputs, load_results, save_results
from .reports import (
    ReportError,
    ReportNotFound,
    get_report,
    list_reports,
    missing_libraries,
    save_report,
)
from .schemas import (
    ChampionsOut,
    LibraryNo,
    LibraryReportOut,
    MissingOut,
    Period,
    PeriodCreate,
    ReportIn,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield


app = FastAPI(
    title="Electronic journal ranking verification",
    version="1.0.0",
    lifespan=lifespan,
)


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


@app.exception_handler(NotFound)
async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
    return _error(404, str(exc))


@app.exception_handler(ReportNotFound)
async def _report_not_found(_: Request, exc: ReportNotFound) -> JSONResponse:
    return _error(404, str(exc))


@app.exception_handler(PeriodError)
async def _period_error(_: Request, exc: PeriodError) -> JSONResponse:
    return _error(422, str(exc))


@app.exception_handler(ReportError)
async def _report_error(_: Request, exc: ReportError) -> JSONResponse:
    return _error(422, str(exc))


@app.post("/periods", status_code=201)
def create_period(request: PeriodCreate) -> dict[str, object]:
    """Set the month's journals (at most 30) and libraries (at most 8)."""
    with transaction() as connection:
        define_period(connection, request.period, request.journals, request.libraries)
        return _period_body(connection, request.period)


@app.get("/periods/{period}")
def read_period(period: Period) -> dict[str, object]:
    with read_snapshot() as connection:
        require_period(connection, period)
        return _period_body(connection, period)


def _period_body(connection, period: str) -> dict[str, object]:
    return {
        "period": period,
        "journals": journals_of(connection, period),
        "libraries": libraries_of(connection, period),
    }


@app.put(
    "/periods/{period}/libraries/{library_no}/report",
    response_model=LibraryReportOut,
)
def submit_report(
    period: Period, library_no: LibraryNo, request: ReportIn
) -> dict[str, object]:
    """Submit a report; a resubmission replaces the whole previous report.

    Once the last library reports, the verdict is computed from every stored
    report in the same transaction, so the report and the result that counts
    it become visible together.  If the report cannot be completed or saving
    fails anywhere, the transaction rolls back and the previous report and
    result stay in place.
    """
    with transaction() as connection:
        require_period(connection, period)
        require_library(connection, period, library_no)
        report = save_report(connection, period, library_no, request)
        if not missing_libraries(connection, period):
            journals, inputs = load_library_inputs(connection, period)
            save_results(connection, period, champions(journals, inputs))
        return report


@app.get(
    "/periods/{period}/libraries/{library_no}/report",
    response_model=LibraryReportOut,
)
def read_library_report(period: Period, library_no: LibraryNo) -> dict[str, object]:
    with read_snapshot() as connection:
        require_period(connection, period)
        require_library(connection, period, library_no)
        return get_report(connection, period, library_no)


@app.get("/periods/{period}/reports", response_model=list[LibraryReportOut])
def read_reports(period: Period) -> list[dict[str, object]]:
    with read_snapshot() as connection:
        require_period(connection, period)
        return list_reports(connection, period)


@app.get("/periods/{period}/missing", response_model=MissingOut)
def read_missing(period: Period) -> dict[str, object]:
    """Only the libraries that have not reported yet."""
    with read_snapshot() as connection:
        require_period(connection, period)
        return {
            "period": period,
            "missing_libraries": missing_libraries(connection, period),
        }


@app.get("/periods/{period}/champions", response_model=ChampionsOut)
def read_champions(period: Period) -> dict[str, object]:
    """All journals that can rank first, each with one supporting completion.

    While any library is still missing, only the missing libraries come back;
    no partial verdict is offered.  The verdict is served from storage, so it
    stays readable and unchanged while a correction is being considered.
    """
    with read_snapshot() as connection:
        require_period(connection, period)
        missing = missing_libraries(connection, period)
        if missing:
            return {"period": period, "missing_libraries": missing, "champions": []}
        return {
            "period": period,
            "missing_libraries": [],
            "champions": load_results(connection, period),
        }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
