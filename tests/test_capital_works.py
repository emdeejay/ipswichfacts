"""
Capital works PDF parsing tests.

This is the most brittle parser in the project: the PDFs have no ruling lines,
so rows and columns are recovered from word x/y positions. The classic failure
is silent column drift — an amount landing in the wrong financial year, which
looks perfectly plausible on the page and is wrong by a year and millions of
dollars. The arithmetic assertions below are the real defence: if columns
drift, per-row amounts stop summing to the row totals Council printed.

Fixtures are 2-page slices of the real PDFs (see docs/notes.md).

    python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("pdfplumber")

from scrape.capital_works import parse_pdf  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def cycle_2025():
    """2025-2026 program: per-project dollar amounts published."""
    return parse_pdf(FIXTURES / "capworks_2025_p2-4.pdf", "2025-2026", "http://example/2025.pdf")


@pytest.fixture(scope="module")
def cycle_2026():
    """2026-2027 program: Council switched to dots — funded years only."""
    return parse_pdf(FIXTURES / "capworks_2026_p2-4.pdf", "2026-2027", "http://example/2026.pdf")


def _rows(parsed):
    return [r for p in parsed["programs"] for r in p["rows"]]


# ---------------------------------------------------------------------------
# Shape


def test_fy_columns_derived_from_the_document(cycle_2025, cycle_2026):
    assert cycle_2025["fy_columns"] == ["2025-2026", "2026-2027", "2027-2028"]
    assert cycle_2026["fy_columns"] == ["2026-2027", "2027-2028", "2028-2029"]


def test_rows_are_parsed(cycle_2025, cycle_2026):
    assert len(_rows(cycle_2025)) > 10, "2025 fixture yielded almost no rows"
    assert len(_rows(cycle_2026)) > 10, "2026 fixture yielded almost no rows"


def test_every_row_has_a_project_name(cycle_2025, cycle_2026):
    for parsed in (cycle_2025, cycle_2026):
        for r in _rows(parsed):
            assert r["project"].strip(), f"nameless row: {r}"
            assert r["page"] >= 1


def test_amounts_published_flag_tracks_the_document(cycle_2025, cycle_2026):
    # Council stopped publishing per-project dollars in the 2026-27 cycle.
    assert cycle_2025["amounts_published"] is True
    assert cycle_2026["amounts_published"] is False


# ---------------------------------------------------------------------------
# The column-drift defence


def test_row_amounts_sum_to_row_totals(cycle_2025):
    """If x-clustering drifts, this is what catches it."""
    mismatches = []
    for r in _rows(cycle_2025):
        if not r.get("amounts") or r.get("total") is None:
            continue
        parts = [v for v in r["amounts"].values() if v is not None]
        if parts and sum(parts) != r["total"]:
            mismatches.append((r["project"], parts, r["total"]))
    assert not mismatches, f"amounts don't sum to totals: {mismatches[:3]}"


def test_known_row_parses_exactly(cycle_2025):
    """Pinned against the printed page: Parking Meter Upgrade, $1.2M in
    2025-26 only."""
    row = next(r for r in _rows(cycle_2025) if "Parking Meter Upgrade" in r["project"])
    assert row["amounts"]["2025-2026"] == 1200
    assert row["amounts"]["2026-2027"] is None
    assert row["total"] == 1200
    assert "Pay By Plate" in row["description"]


def test_amounts_are_thousands_not_strings(cycle_2025):
    for r in _rows(cycle_2025):
        for v in (r.get("amounts") or {}).values():
            assert v is None or isinstance(v, int)
        assert r["total"] is None or isinstance(r["total"], int)


# ---------------------------------------------------------------------------
# Dots cycle: funded years, never invented amounts


def test_dots_cycle_records_funded_years_without_amounts(cycle_2026):
    funded = [r for r in _rows(cycle_2026) if r["funded_years"]]
    assert funded, "no funded years read from the dot markers"
    for r in funded:
        for fy in r["funded_years"]:
            assert fy in cycle_2026["fy_columns"]


def test_dots_cycle_never_fabricates_dollar_figures(cycle_2026):
    """The whole point: absent data must stay absent."""
    for r in _rows(cycle_2026):
        assert not r.get("amounts"), f"invented amounts for {r['project']}: {r['amounts']}"
        assert r.get("total") is None


def test_dots_row_known_example(cycle_2026):
    row = next(r for r in _rows(cycle_2026) if "Regents Drive" in r["project"])
    assert row["funded_years"], "Regents Drive should be funded in at least one year"
    assert "Fernbrooke" in row["description"] or "safety" in row["description"].lower()


# ---------------------------------------------------------------------------
# Headings and aggregates must not become projects


def test_totals_rows_are_not_projects(cycle_2025, cycle_2026):
    for parsed in (cycle_2025, cycle_2026):
        for r in _rows(parsed):
            name = r["project"].strip().lower()
            assert not name.endswith("total"), f"aggregate leaked in as a project: {r['project']}"
            assert name != "grand total"


def test_legend_and_footnotes_excluded(cycle_2026):
    """The KEY: legend's dots would otherwise attach to a row."""
    for r in _rows(cycle_2026):
        assert not r["project"].strip().upper().startswith("KEY")
