"""
Parser tests — the fragile surface.

Every scraper here reads HTML or PDFs that Council can restructure without
notice. The dangerous failure isn't a crash (that fails the workflow, which
is safe) — it's a parser quietly matching nothing and the build publishing an
empty site over an indexed one. These tests pin the parsers against real
saved responses so that kind of break shows up here first.

Fixtures in tests/fixtures/ are trimmed but otherwise verbatim Council output.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

# The scrape modules import httpx at module level; the parse functions under
# test don't touch the network, so a stub keeps the suite dependency-light.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build.build_site import (  # noqa: E402
    check_data_sanity,
    classify_traffic_impact,
    extract_streets_from_text,
    slugify,
)
from scrape.council_meetings import (  # noqa: E402
    parse_bmk,
    parse_frameset,
    parse_index,
    parse_items,
)
from scrape.road_closures import _flatten_impact, _normalise_tmr  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(errors="replace")


# ---------------------------------------------------------------------------
# infocouncil (council meetings)


def test_parse_index_finds_meetings_with_committee_names():
    meetings = parse_index(fixture("infocouncil_index.html"))
    assert meetings, "no meetings parsed — index markup probably changed"
    for m in meetings:
        assert m["committee_code"].isupper()
        assert len(m["date"]) == 10 and m["date"][4] == "-"
        assert m["docs"], f"{m['id']} has no AGN/MIN docs"
    # Committee full names come from the row, not a hardcoded map.
    assert any(m["committee"] for m in meetings)


def test_parse_index_prefers_minutes_and_skips_supplementary():
    meetings = parse_index(fixture("infocouncil_index.html"))
    for m in meetings:
        for doc_type, url in m["docs"].items():
            assert doc_type in ("AGN", "MIN")
            assert "_SUP" not in url


def test_parse_frameset_returns_both_frames():
    nav, paper = parse_frameset(fixture("infocouncil_frameset.html"))
    # Inner names can't be derived from the WEB.htm name — this is why we
    # fetch the frameset at all. See docs/notes.md.
    assert nav and nav.endswith("_BMK.HTM")
    assert paper and paper.endswith(".HTM") and "_WEB" not in paper


def test_parse_bmk_extracts_titled_items():
    items = parse_bmk(fixture("infocouncil_bmk.html"))
    assert len(items) > 5, "bookmark frame yielded almost nothing"
    for it in items:
        assert it["anchor"].startswith(("PDF2_ReportName_", "PDF2_NewItem_"))
        assert it["title"].strip()
    # Anchors are unique — duplicates would double-render items.
    assert len({i["anchor"] for i in items}) == len(items)


def test_parse_bmk_excludes_procedural_resolution_anchors():
    # PDF2_Resolution_* are leave-of-absence / cancellation markers, not items.
    items = parse_bmk(fixture("infocouncil_bmk.html"))
    assert not any("Resolution" in i["anchor"] for i in items)


def test_parse_items_joins_bookmark_titles_to_paper_text():
    items = parse_items(fixture("infocouncil_bmk.html"), fixture("infocouncil_paper.html"))
    assert items
    with_text = [i for i in items if i["text"]]
    assert with_text, "no item got any text — paper anchors probably changed"
    for it in with_text:
        assert "<" not in it["text"], "HTML leaked into item text"
        assert "&nbsp;" not in it["text"] and "&amp;" not in it["text"]


def test_parse_items_text_does_not_bleed_across_items():
    """Each item's text is bounded by the next anchor. If the split breaks,
    early items swallow the whole paper."""
    items = parse_items(fixture("infocouncil_bmk.html"), fixture("infocouncil_paper.html"))
    paper_len = len(fixture("infocouncil_paper.html"))
    for it in items:
        assert len(it["text"]) < paper_len * 0.9


# ---------------------------------------------------------------------------
# QLDTraffic / road closures


@pytest.mark.parametrize(
    "impact,expected",
    [
        ("Closures", "Closures"),  # already a plain string upstream
        (None, None),
        ({}, None),
        # The dict form that used to render as a raw JSON blob on the site.
        (
            {
                "direction": "Eastbound",
                "towards": "Karalee",
                "impact_type": "Lanes affected",
                "impact_subtype": "Lane or lanes reduced",
                "delay": "Delays expected",
            },
            "Lane or lanes reduced (Eastbound towards Karalee) — Delays expected",
        ),
        # Upstream writes the literal string "Unknown" for absent fields.
        ({"impact_subtype": "No blockage", "direction": "Unknown"}, "No blockage"),
        ({"impact_type": "Unknown", "direction": "Unknown", "delay": "Unknown"}, None),
    ],
)
def test_flatten_impact(impact, expected):
    assert _flatten_impact(impact) == expected


def test_normalise_tmr_drops_raw_geojson():
    """The raw feature used to ship to every client in closures.json."""
    feature = {
        "properties": {"id": 1, "impact": {"impact_type": "Closure"}},
        "geometry": {"type": "Point", "coordinates": [152.8, -27.6]},
    }
    out = _normalise_tmr(feature)
    assert "raw" not in out
    assert out["coords"] == [152.8, -27.6]
    assert out["impact"] == "Closure"


def test_normalise_tmr_reads_point_from_geometry_collection():
    feature = {
        "properties": {"id": 2},
        "geometry": {
            "type": "GeometryCollection",
            "geometries": [{"type": "Point", "coordinates": [1.0, 2.0]}],
        },
    }
    assert _normalise_tmr(feature)["coords"] == [1.0, 2.0]


# ---------------------------------------------------------------------------
# Street extraction — runs over six years of Word-mangled meeting prose


def test_extract_streets_basic():
    found = extract_streets_from_text("Works on Brisbane Street and Nicholas St, Ipswich.")
    assert "Brisbane Street" in found
    assert "Nicholas Street" in found, "abbreviation not normalised"


def test_extract_streets_splits_compound_spans():
    """'Cobalt Street and Johnson Road' is two streets, not one."""
    found = extract_streets_from_text("between Cobalt Street and Johnson Road")
    assert "Cobalt Street" in found
    assert "Johnson Road" in found
    assert not any(" And " in s for s in found)


def test_extract_streets_ignores_courts_of_law():
    found = extract_streets_from_text(
        "The matter was heard in the Planning and Environment Court last week."
    )
    assert found == []


def test_extract_streets_does_not_cross_paragraph_breaks():
    found = extract_streets_from_text("MANAGEMENT IMPLICATIONS\nThe Nicholas Street upgrade")
    assert "Nicholas Street" in found
    assert not any("\n" in s or "IMPLICATIONS" in s for s in found)


def test_extract_streets_strips_leading_grammar_words():
    found = extract_streets_from_text("works along The Terrace and near Bell Street")
    assert "Bell Street" in found
    assert not any(s.startswith(("And ", "Near ", "The The")) for s in found)


def test_extract_streets_keeps_real_multiword_names():
    found = extract_streets_from_text("upgrades to Redbank Plains Road and New Chum Road")
    assert "Redbank Plains Road" in found
    assert "New Chum Road" in found, "'New' must not be treated as a stopword"


# ---------------------------------------------------------------------------
# Traffic impact tiering (homepage construction works table)


def test_classify_traffic_impact_tiers():
    closure = {"status": "Gordon Street will be closed to all traffic until September."}
    lanes = {"status": "A temporary closure of the westbound traffic lane will be in place."}
    control = {"status": "Works underway.", "what_to_expect": "Traffic control on site."}
    assert classify_traffic_impact(closure)[0] == 3
    assert classify_traffic_impact(lanes)[0] == 2
    assert classify_traffic_impact(control)[0] == 1


def test_classify_traffic_impact_ignores_non_road_closures():
    """Park closures and lane-count specs are not traffic interruptions."""
    park = {"what_to_expect": "You may experience temporay park closures and noise."}
    spec = {"status": "Upgrade to a four-lane urban standard between Keidges Road."}
    assert classify_traffic_impact(park)[0] == 0
    assert classify_traffic_impact(spec)[0] == 0


# ---------------------------------------------------------------------------
# Slugs — published URLs must stay stable (CLAUDE.md invariant)


def test_slugify_stable_and_bounded():
    assert slugify("Redbank Plains Road Upgrade – Stage 3") == "redbank-plains-road-upgrade-stage-3"
    assert slugify("Council") == "council"
    assert len(slugify("x" * 200)) <= 80
    assert slugify("!!!") == "x"  # never empty


# ---------------------------------------------------------------------------
# The deploy guardrail itself


def test_sanity_check_flags_collapsed_dataset():
    failures = check_data_sanity(
        {"projects": 0, "meetings": 0, "news": 0, "streets": 0,
         "meeting_items": 0, "capital_works_rows": 0, "councillors": 0}
    )
    assert len(failures) == 7
    assert any("meetings" in f for f in failures)


def test_sanity_check_passes_healthy_dataset():
    assert check_data_sanity(
        {"projects": 385, "meetings": 655, "meeting_items": 4700, "news": 4922,
         "capital_works_rows": 1745, "councillors": 9, "streets": 1371}
    ) == []


def test_sanity_check_ignores_absent_keys():
    """A caller that doesn't measure something shouldn't trip its floor."""
    assert check_data_sanity({"projects": 385}) == []
