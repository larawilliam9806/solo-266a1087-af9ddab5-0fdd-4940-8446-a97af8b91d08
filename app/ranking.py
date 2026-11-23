"""Exactly how far each journal can go once every library has filled in.

Inside one library the leaderboard counts are fixed.  The downloads left over
(``total_downloads`` minus the leaderboard sum) are spread over the journals
the library did not list, each between zero and the tail count, and every such
split is allowed.  Hence one unlisted journal can take at most
``min(tail, remainder)``, the others still fit because their minimum is zero.

So a journal's best city-wide total is the sum of its fixed counts plus that
maximum in every library that did not list it.  A journal can be number one
(ties included) when some completion keeps every journal at or below a bar it
can itself reach.  Whether that completion exists is decided by one max-flow
per journal, never by enumerating the completions one by one.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass
from typing import Iterable

from .db import utc_now
from .periods import journals_of


@dataclass(frozen=True)
class LibraryInput:
    library_no: str
    top: tuple[tuple[str, int], ...]
    total_downloads: int

    @property
    def listed(self) -> dict[str, int]:
        return dict(self.top)

    @property
    def tail(self) -> int:
        """The leaderboard's last count; the cap for every unlisted journal."""
        return self.top[-1][1]

    @property
    def remainder(self) -> int:
        """Downloads to spread over the journals this library did not list."""
        return self.total_downloads - sum(count for _, count in self.top)


class FlowError(RuntimeError):
    """A completion produced by the flow did not add up or left the tail range."""


class _Dinic:
    """Dinic max-flow on a tiny graph (<= 8 libraries, <= 30 journals).

    Capacities are arbitrary-precision integers.  Each blocking-flow push is
    seeded with the full amount leaving the source, so a push always saturates
    an edge and the number of augmentations depends on the graph's size, never
    on the magnitude of the counts flowing through it.
    """

    __slots__ = ("size", "graph", "level", "cursor")

    def __init__(self, size: int) -> None:
        self.size = size
        self.graph: list[list[list[int]]] = [[] for _ in range(size)]
        self.level: list[int] = []
        self.cursor: list[int] = []

    def add_edge(self, source: int, target: int, capacity: int) -> None:
        forward = [target, capacity, len(self.graph[target])]
        backward = [source, 0, len(self.graph[source])]
        self.graph[source].append(forward)
        self.graph[target].append(backward)

    def _push(self, node: int, limit: int, sink: int) -> int:
        if node == sink:
            return limit
        while self.cursor[node] < len(self.graph[node]):
            edge = self.graph[node][self.cursor[node]]
            target, capacity, reverse = edge
            if capacity > 0 and self.level[target] == self.level[node] + 1:
                amount = self._push(target, min(limit, capacity), sink)
                if amount:
                    edge[1] -= amount
                    self.graph[target][reverse][1] += amount
                    return amount
            self.cursor[node] += 1
        return 0

    def max_flow(self, source: int, sink: int) -> int:
        flow = 0
        # The network can never move more than the source can give out; using
        # that bound (instead of a fixed 2**60) keeps every push saturating.
        supply = sum(capacity for _, capacity, _ in self.graph[source])
        while True:
            self.level = [-1] * self.size
            self.level[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                for target, capacity, _ in self.graph[node]:
                    if capacity > 0 and self.level[target] < 0:
                        self.level[target] = self.level[node] + 1
                        queue.append(target)
            if self.level[sink] < 0:
                return flow
            self.cursor = [0] * self.size
            while True:
                amount = self._push(source, supply, sink)
                if not amount:
                    break
                flow += amount


def best_totals(
    libraries: Iterable[LibraryInput], journals: list[str]
) -> dict[str, int]:
    """The largest total each journal can reach, per journal independently."""
    best = {journal: 0 for journal in journals}
    for library in libraries:
        listed = library.listed
        room = min(library.tail, library.remainder)
        for journal in journals:
            best[journal] += listed.get(journal, room)
    return best


def completion_at(
    winner: str,
    bar: int,
    libraries: list[LibraryInput],
    journals: list[str],
) -> list[dict[str, object]] | None:
    """Fill in every library so ``winner`` hits ``bar`` and nobody beats it.

    The winner takes its maximum in every library that did not list it -- the
    only way to reach its best total, and enough for a tie.  That library's
    remaining downloads then flow to the other unlisted journals.  The rest is
    feasibility of a capped transportation problem: source -> library (the
    library remainder), library -> unlisted journal (tail), journal -> sink
    (headroom between the fixed counts and the bar).  Saturating the source
    proves a completion exists, and the flow itself is the supporting table.
    """
    fixed = {journal: 0 for journal in journals}
    for library in libraries:
        for journal, count in library.top:
            fixed[journal] += count

    headroom = {journal: bar - fixed[journal] for journal in journals}
    if any(value < 0 for value in headroom.values()):
        return None

    grants: list[int] = []
    for library in libraries:
        if winner in library.listed:
            grants.append(library.remainder)
        else:
            maximum = min(library.tail, library.remainder)
            fixed[winner] += maximum
            grants.append(library.remainder - maximum)
    if fixed[winner] != bar:
        return None

    slot = {journal: index for index, journal in enumerate(journals)}
    journal_base = 1 + len(libraries)
    source = 0
    sink = journal_base + len(journals)
    network = _Dinic(sink + 1)

    demand = 0
    for index, library in enumerate(libraries):
        node = 1 + index
        network.add_edge(source, node, grants[index])
        demand += grants[index]
        for journal in journals:
            if journal in library.listed or journal == winner:
                continue
            network.add_edge(node, journal_base + slot[journal], library.tail)
    for journal in journals:
        network.add_edge(journal_base + slot[journal], sink, headroom[journal])

    if network.max_flow(source, sink) != demand:
        return None

    support: list[dict[str, object]] = []
    for index, library in enumerate(libraries):
        counts = {journal: 0 for journal in journals}
        counts.update(library.listed)
        counts[winner] = library.listed.get(winner, min(library.tail, library.remainder))
        node = 1 + index
        for edge in network.graph[node]:
            target = edge[0]
            if journal_base <= target < journal_base + len(journals):
                used = network.graph[target][edge[2]][1]
                if used:
                    counts[journals[target - journal_base]] = used
        if sum(counts.values()) != library.total_downloads:
            raise FlowError(
                f"completion for library {library.library_no} misses its total"
            )
        support.append(
            {
                "library_no": library.library_no,
                "downloads": [
                    {"journal_no": journal, "downloads": counts[journal]}
                    for journal in journals
                ],
            }
        )
    return support


def champions(
    journals: list[str], libraries: list[LibraryInput]
) -> list[dict[str, object]]:
    """Every journal that can rank first, ties included, with one support table.

    The bar is each journal's own best total, so a journal below the city-wide
    maximum can still be first when the strongest rival is held down to the
    same value.  Every journal is therefore tested, not just the bound leaders.
    """
    best = best_totals(libraries, journals)
    found: list[dict[str, object]] = []
    for journal in journals:
        support = completion_at(journal, best[journal], libraries, journals)
        if support is None:
            continue
        found.append(
            {
                "journal_no": journal,
                "total_downloads": best[journal],
                "support": support,
            }
        )
    return found


def load_library_inputs(
    connection: sqlite3.Connection, period: str
) -> tuple[list[str], list[LibraryInput]]:
    journals = journals_of(connection, period)
    libraries: list[LibraryInput] = []
    for row in connection.execute(
        "SELECT * FROM reports WHERE period = ? ORDER BY library_no", (period,)
    ):
        payload = json.loads(row["top_json"])
        libraries.append(
            LibraryInput(
                library_no=row["library_no"],
                top=tuple((journal, count) for journal, count in payload["top"]),
                total_downloads=payload["total_downloads"],
            )
        )
    return journals, libraries


def save_results(
    connection: sqlite3.Connection, period: str, verdicts: list[dict[str, object]]
) -> None:
    now = utc_now()
    connection.execute("DELETE FROM results WHERE period = ?", (period,))
    connection.executemany(
        "INSERT INTO results (period, journal_no, total_downloads, support_json,"
        " computed_at) VALUES (?, ?, ?, ?, ?)",
        [
            (
                period,
                verdict["journal_no"],
                str(verdict["total_downloads"]),
                json.dumps(verdict["support"], separators=(",", ":")),
                now,
            )
            for verdict in verdicts
        ],
    )


def load_results(connection: sqlite3.Connection, period: str) -> list[dict[str, object]]:
    return [
        {
            "journal_no": row["journal_no"],
            "total_downloads": int(row["total_downloads"]),
            "support": json.loads(row["support_json"]),
        }
        for row in connection.execute(
            "SELECT * FROM results WHERE period = ? ORDER BY journal_no", (period,)
        )
    ]
