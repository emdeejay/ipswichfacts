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


# ---------------------------------------------------------------------------
# Funding revisions — the same-financial-year comparison
#
# The rule these protect: compare a project's amount for THE SAME financial
# year across programs. Never its three-year total, which covers a different
# rolling window each cycle (a finishing project would look like a cut one).

from build.build_site import (  # noqa: E402
    _funding_matrix,
    collect_funding_revisions,
    fmt_dollars_exact,
    fmt_kdollars,
)


def _entry(cycle, fy_cols, amounts, published=True):
    return {
        "cycle": cycle,
        "fy_columns": fy_cols,
        "amounts_published": published,
        "source_url": f"http://example/{cycle}.pdf",
        "section": "Roads",
        "row": {"project": "Test Road Upgrade", "amounts": amounts, "page": 3,
                "total": sum(v for v in amounts.values() if v)},
    }


def test_matrix_flags_only_genuinely_revised_years():
    entries = [
        _entry("2024-2025", ["2024-2025", "2025-2026", "2026-2027"],
               {"2024-2025": 100, "2025-2026": 200, "2026-2027": 300}),
        # Same FY2025-2026, different figure → a revision. FY2026-2027 unchanged.
        _entry("2025-2026", ["2025-2026", "2026-2027", "2027-2028"],
               {"2025-2026": 250, "2026-2027": 300, "2027-2028": 400}),
    ]
    m = _funding_matrix(entries)
    assert m["revised_fys"] == ["2025-2026"]


def test_matrix_marks_out_of_window_years_not_zero():
    """A blank must never read as 'Council budgeted nothing'.

    Amounts dicts carry every year in the program's window, with None for the
    '-' Council prints for nil — mirroring the parser's real output.
    """
    entries = [
        _entry("2024-2025", ["2024-2025", "2025-2026", "2026-2027"],
               {"2024-2025": 100, "2025-2026": None, "2026-2027": None}),
        _entry("2025-2026", ["2025-2026", "2026-2027", "2027-2028"],
               {"2025-2026": None, "2026-2027": None, "2027-2028": 400}),
    ]
    m = _funding_matrix(entries)
    # FY2024-2025 predates the 2025-2026 program's window entirely.
    assert m["cells"][("2025-2026", "2024-2025")]["kind"] == "outside"
    # Published-as-nil is a different state from out-of-window.
    assert m["cells"][("2024-2025", "2025-2026")]["kind"] == "nil"
    # And a year no program covered at all isn't invented.
    assert ("2024-2025", "2027-2028") in m["cells"]
    assert m["cells"][("2024-2025", "2027-2028")]["kind"] == "outside"


def test_matrix_ignores_dots_cycles():
    """The 2026-27 program publishes no per-project figure — nothing to compare."""
    entries = [
        _entry("2025-2026", ["2025-2026"], {"2025-2026": 100}),
        _entry("2026-2027", ["2026-2027"], {}, published=False),
    ]
    m = _funding_matrix(entries)
    assert m["cycles"] == ["2025-2026"]
    assert m["revised_fys"] == []


def test_revisions_never_merge_different_stages():
    """Stage 1 and Stage 2 are different works. Merging them would invent a
    revision that doesn't exist."""
    capworks = [
        {"cycle": "2024-2025", "amounts_published": True, "source_url": "u",
         "programs": [{"section": "Roads", "rows": [
             {"project": "Foo Road Upgrade – Stage 1", "amounts": {"2025-2026": 100}, "page": 1},
             {"project": "Foo Road Upgrade – Stage 2", "amounts": {"2025-2026": 900}, "page": 1},
         ]}]},
    ]
    assert collect_funding_revisions(capworks) == []


def test_revisions_detects_real_change():
    capworks = [
        {"cycle": "2024-2025", "amounts_published": True, "source_url": "a",
         "programs": [{"section": "Roads", "rows": [
             {"project": "Foo Road Upgrade", "amounts": {"2025-2026": 100}, "page": 1}]}]},
        {"cycle": "2025-2026", "amounts_published": True, "source_url": "b",
         "programs": [{"section": "Roads", "rows": [
             {"project": "Foo Road Upgrade", "amounts": {"2025-2026": 250}, "page": 2}]}]},
    ]
    revs = collect_funding_revisions(capworks)
    assert len(revs) == 1
    assert set(revs[0]["revised"]["2025-2026"]) == {"2024-2025", "2025-2026"}
    assert revs[0]["spread"] == 150


def test_exact_formatter_distinguishes_what_rounding_hides():
    """The bug this guards: $2,450k and $2,500k both render '$2.5M', making a
    real revision look like a display error."""
    assert fmt_kdollars(2450) == fmt_kdollars(2500)  # the trap
    assert fmt_dollars_exact(2450) != fmt_dollars_exact(2500)
    assert fmt_dollars_exact(2450) == "$2,450,000"


def test_no_revised_row_displays_identical_values(cycle_2025):
    """End-to-end: nothing marked 'revised' may render as two equal strings."""
    import glob
    import json as _json
    capworks = [_json.load(open(f)) for f in sorted(glob.glob("data/capital_works/*.json"))]
    for item in collect_funding_revisions(capworks):
        for fy, by_cycle in item["revised"].items():
            shown = [fmt_dollars_exact(c["amount"]) for c in by_cycle.values()]
            assert len(set(shown)) > 1, f"{item['name']} {fy} renders identically: {shown}"
