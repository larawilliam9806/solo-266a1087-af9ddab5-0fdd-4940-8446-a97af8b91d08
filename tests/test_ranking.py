"""The ranking engine is checked against brute-force enumeration.

Small months keep the number of completions small enough to list every one of
them, so the fast analytic+flow answer can be compared with the ground truth
of "which journals appear tied for the maximum in at least one completion".
"""

import itertools
import random

from app.ranking import LibraryInput, champions


def completions(journals, library):
    listed = library.listed
    outside = [journal for journal in journals if journal not in listed]
    remainder = library.remainder
    if not outside:
        return [dict(listed)] if remainder == 0 else []
    results = []

    def walk(index, left, partial):
        if index == len(outside):
            if left == 0:
                full = dict(listed)
                full.update(partial)
                results.append(full)
            return
        remaining = len(outside) - index - 1
        low = max(0, left - remaining * library.tail)
        high = min(library.tail, left)
        for value in range(low, high + 1):
            partial[outside[index]] = value
            walk(index + 1, left - value, partial)
        partial.pop(outside[index], None)

    walk(0, remainder, {})
    return results


def brute_force(journals, libraries):
    menus = [completions(journals, library) for library in libraries]
    if any(not menu for menu in menus):
        return None
    winners = set()
    for combination in itertools.product(*menus):
        totals = {
            journal: sum(menu[journal] for menu in combination)
            for journal in journals
        }
        top = max(totals.values())
        winners |= {journal for journal, value in totals.items() if value == top}
    return winners


def random_month(rng):
    journals = [f"J{index}" for index in range(rng.randint(1, 4))]
    libraries = []
    for index in range(rng.randint(1, 3)):
        size = rng.randint(1, len(journals))
        listed = rng.sample(journals, size)
        tail = rng.randint(0, 3)
        counts = sorted(
            (rng.randint(tail, tail + 3) for _ in listed), reverse=True
        )
        remaining = len(journals) - size
        extra = rng.randint(0, remaining * tail) if remaining else 0
        libraries.append(
            LibraryInput(
                library_no=f"L{index}",
                top=tuple(zip(listed, counts)),
                total_downloads=sum(counts) + extra,
            )
        )
    return journals, libraries


def test_ranking_matches_brute_force():
    rng = random.Random(20260501)
    checked = 0
    for _ in range(4000):
        journals, libraries = random_month(rng)
        expected = brute_force(journals, libraries)
        if expected is None:
            continue
        checked += 1
        got = {entry["journal_no"] for entry in champions(journals, libraries)}
        assert got == expected, (journals, libraries, got, expected)
    assert checked > 1000


def test_every_support_table_is_a_valid_completion():
    rng = random.Random(7)
    for _ in range(500):
        journals, libraries = random_month(rng)
        for entry in champions(journals, libraries):
            city = {journal: 0 for journal in journals}
            seen_libraries = set()
            for library_support in entry["support"]:
                library = next(
                    item
                    for item in libraries
                    if item.library_no == library_support["library_no"]
                )
                seen_libraries.add(library.library_no)
                counts = {
                    row["journal_no"]: row["downloads"]
                    for row in library_support["downloads"]
                }
                assert set(counts) == set(journals)
                assert sum(counts.values()) == library.total_downloads
                for journal, value in counts.items():
                    assert value >= 0
                    if journal not in library.listed:
                        assert value <= library.tail
                    else:
                        assert value == library.listed[journal]
                    city[journal] += value
            assert seen_libraries == {library.library_no for library in libraries}
            assert city[entry["journal_no"]] == entry["total_downloads"]
            assert max(city.values()) == entry["total_downloads"]
