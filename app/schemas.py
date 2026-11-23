"""Pydantic request/response models.

A library submits its own leaderboard (journal numbers with download counts,
at least one entry) and its whole-month download total.  The leaderboard's
last count is the tail: every journal outside the leaderboard has between
zero and tail downloads, both inclusive.  A resubmission replaces the whole
earlier report.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

Period = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
JournalNo = Annotated[str, Field(min_length=1, max_length=64)]
LibraryNo = Annotated[str, Field(min_length=1, max_length=64)]

MAX_JOURNALS = 30
MAX_LIBRARIES = 8


class PeriodCreate(BaseModel):
    period: Period
    journals: list[JournalNo] = Field(min_length=1, max_length=MAX_JOURNALS)
    libraries: list[LibraryNo] = Field(min_length=1, max_length=MAX_LIBRARIES)

    @field_validator("journals", "libraries")
    @classmethod
    def _unique(cls, value: list[str], info) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError(f"{info.field_name} must be unique")
        return value


class TopEntry(BaseModel):
    journal_no: JournalNo
    downloads: int = Field(ge=0, strict=True)


class ReportIn(BaseModel):
    """One library's leaderboard and its total downloads for the month."""

    top: list[TopEntry] = Field(min_length=1)
    total_downloads: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _coherent(self) -> "ReportIn":
        numbers = [entry.journal_no for entry in self.top]
        if len(set(numbers)) != len(numbers):
            raise ValueError("the leaderboard repeats a journal number")
        # Rank the leaderboard from high to low; the tail is its last count.
        self.top = sorted(self.top, key=lambda entry: entry.downloads, reverse=True)
        return self

    @property
    def tail_downloads(self) -> int:
        return self.top[-1].downloads


class LibraryReportOut(BaseModel):
    library_no: str
    top: list[TopEntry]
    total_downloads: int
    tail_downloads: int
    revision: int
    updated_at: str


class MissingOut(BaseModel):
    period: str
    missing_libraries: list[str]


class JournalDownloadOut(BaseModel):
    journal_no: str
    downloads: int


class LibraryCompletionOut(BaseModel):
    library_no: str
    downloads: list[JournalDownloadOut]


class ChampionOut(BaseModel):
    journal_no: str
    total_downloads: int
    support: list[LibraryCompletionOut]


class ChampionsOut(BaseModel):
    period: str
    missing_libraries: list[str] = Field(default_factory=list)
    champions: list[ChampionOut] = Field(default_factory=list)
