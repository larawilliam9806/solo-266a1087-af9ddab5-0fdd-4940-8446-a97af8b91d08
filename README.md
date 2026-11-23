# Journal ranking verification API

A FastAPI + SQLite service that, for a given month, works out which electronic
journals could be the most downloaded in the city once every member library has
filled in the journals it left off its own leaderboard.

## Run

```bash
docker compose up --build
```

The API listens on `http://localhost:8000`; interactive docs are at `/docs`.
The SQLite file lives on the `journal-data` volume, so restarts keep state.

Locally without Docker:

```bash
pip install -r requirements.txt
JOURNAL_DB_PATH=./journals.sqlite3 uvicorn app.main:app --reload
```

## Workflow

1. **Define the month** — up to 30 journal numbers in scope and up to 8
   libraries that owe a report.
   `POST /periods` with `{"period": "2026-05", "journals": [...], "libraries": [...]}`
2. **Libraries report** — each library sends its own leaderboard (at least one
   journal, download counts; they are ranked high-to-low on the way in) and its
   download total for the month.
   `PUT /periods/{period}/libraries/{library}/report`
   ```json
   {"top": [{"journal_no": "J1", "downloads": 40},
            {"journal_no": "J2", "downloads": 21}],
    "total_downloads": 300}
   ```
   The leaderboard is the library's own ranking, so it may also name journals
   that are not taking part this month; those entries are kept but do not
   count towards the participant total. The last leaderboard count (`21`
   here) is the tail: every *participating* journal the library did not list
   has between `0` and `21` downloads inclusive, and the reported total must
   be reachable that way. A resubmission replaces the whole previous report;
   nothing is merged.
3. **Check who is still missing** — `GET /periods/{period}/missing` lists only
   the libraries that have not reported.
4. **Read the verdict** — `GET /periods/{period}/champions`. While any library
   is missing it returns the missing list and no verdict. Once every library
   has reported it returns each journal that can rank first (ties included)
   with one supporting completion: every library's download count for every
   journal in scope.

Other reads: `GET /periods/{period}` (parameters), `GET
/periods/{period}/reports` and `GET
/periods/{period}/libraries/{library}/report`.

## Guarantees

- **Every possible winner, ties included.** The verdict is exact, not a
  sample: a journal is listed when some completion makes its total at least
  every other journal's.
- **Arbitrarily large counts.** Download numbers are exact integers with no
  64-bit ceiling: a monthly total or a city-wide total may exceed
  `2**63 - 1`. The API keeps sending and receiving plain JSON integers (every
  digit preserved), while SQLite stores the counts as exact decimal strings.
  A database created by an older version with `INTEGER` columns is migrated
  automatically on startup — its reports and verdicts stay queryable with
  their original values.
- **No enumeration of completions.** Each journal's best reachable total is
  computed arithmetically; whether rivals can be held below that bar is one
  max-flow over the libraries and journals. The flow's assignment is the
  support table returned with the journal. With the journals and libraries
  fixed, the flow's work depends only on the size of that graph, never on how
  large the download counts are.
- **Same-transaction effect.** A report, the month's parameters and the
  recomputed verdict change in one SQLite transaction. If a report cannot be
  completed or anything fails to save, everything rolls back and the previous
  report and verdict remain as they were.
- **Reads stay available.** The verdict is stored, so it keeps being served
  unchanged while a correction is being prepared; other months can be
  reported, corrected or verified at the same time.
- **Parameters freeze once reporting starts**, so a library's report always
  refers to the same set of journals.
- Limits: at most 30 journals and 8 libraries per month; each report has at
  least one leaderboard journal.

## Tests

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. python -m pytest tests -q
```

`tests/test_ranking.py` additionally checks the ranking engine against a
brute-force enumeration of every completion on small random months.
