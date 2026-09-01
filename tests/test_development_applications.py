"""
Parser tests for the Development.i development-application scraper.

Two things matter here beyond "does it parse":
  1. The validation record — Council's Swanbank data-centre DA — must be
     captured with its number, description and status.
  2. Invariant 7 (revised). This is a LINK + factual-metadata layer, not full
     DA reproduction. The normaliser must WHITELIST Council's basic facts and
     carry through NONE of the sensitive fields (officer name, decision
     narrative, appeal result, submission/notification signals), even though the
     raw Council feed carries all of them.

The fixture in tests/fixtures/ is a trimmed but otherwise verbatim Swanbank
`GetApplicationFilterResults` response — trimmed to 5 features, and it
deliberately still contains the excluded fields so we can prove they're stripped.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

# The scrape module imports httpx at module level; the parse functions under
# test don't touch the network, so a stub keeps the suite dependency-light.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrape.development_applications import (  # noqa: E402
    EXCLUDED_FIELDS,
    detail_url,
    normalise_feature,
)

FIXTURES = Path(__file__).parent / "fixtures"

DATA_CENTRE = "12285/2026/MCU"

_ALLOWED_KEYS = {
    "application_number", "id", "description", "status", "application_type",
    "assessment_level", "date_received", "suburb", "coords", "source_url",
}


def _features():
    raw = json.loads((FIXTURES / "developmenti_swanbank.json").read_text())
    return raw["features"]


def _normalised():
    return [r for r in (normalise_feature(f, "Swanbank") for f in _features()) if r]


# ---------------------------------------------------------------------------
# The validation record must survive.


def test_captures_the_swanbank_data_centre_da():
    recs = {r["application_number"]: r for r in _normalised()}
    assert DATA_CENTRE in recs, "the validation DA was not captured"
    dc = recs[DATA_CENTRE]
    assert dc["description"] == "Material Change of Use - Warehouse (Data Centre)"
    assert dc["status"] == "In Progress"
    assert dc["application_type"] == "Material Change of Use"
    assert dc["suburb"] == "Swanbank"
    # Lodged date is reduced to a plain YYYY-MM-DD.
    assert dc["date_received"] == "2026-08-04"
    # The deep link points back to Council's own detail page for THIS app.
    assert dc["source_url"] == detail_url(DATA_CENTRE)
    assert "developmenti.ipswich.qld.gov.au" in dc["source_url"]
    assert "ApplicationDetail" in dc["source_url"]


# ---------------------------------------------------------------------------
# Invariant 7: the sensitive fields must NEVER leave the scraper.


def test_output_is_whitelisted_to_basic_facts_only():
    recs = _normalised()
    assert recs, "no records normalised — feed shape probably changed"
    for r in recs:
        assert set(r.keys()) == _ALLOWED_KEYS


def test_excluded_fields_are_present_in_raw_but_absent_from_output():
    # The excluded fields really are in Council's raw feed (fixture is verbatim)...
    raw_props = _features()[0]["properties"]
    present_in_raw = [f for f in EXCLUDED_FIELDS if f in raw_props]
    assert "project_officer" in present_in_raw
    assert "decision_desc" in present_in_raw
    assert "appeal_result" in raw_props  # key present even where value is null

    # ...and none of them, by key OR by value, reach our output.
    officer = raw_props.get("project_officer")
    decision = raw_props.get("decision_desc")
    for r in _normalised():
        for field in EXCLUDED_FIELDS:
            assert field not in r
        blob = json.dumps(r).lower()
        if officer:
            assert officer.lower() not in blob
        if decision:
            assert decision.lower() not in blob


def test_drops_any_new_sensitive_field_even_if_upstream_adds_one():
    """Whitelist guard: even if Council's feed grows a new officer-opinion or
    submission field, it cannot reach the site."""
    poisoned = {
        "properties": {
            "application_number": "9999/2099/MCU",
            "pdonline_id": 1,
            "description": "A proposal.",
            "progress": "Decided",
            "date_received": "2099-01-01T00:00:00Z",
            "application_type": "Material Change of Use",
            # None of these must survive.
            "project_officer": "Jane Officer",
            "decision_desc": "Approved with a scathing officer note",
            "appeal_result": "Dismissed",
            "officer_recommendation": "Refuse",
            "submissions": ["a resident objection"],
        },
        "geometry": {"type": "Point", "coordinates": [152.8, -27.6]},
    }
    rec = normalise_feature(poisoned, "Swanbank")
    assert set(rec.keys()) == _ALLOWED_KEYS
    blob = json.dumps(rec).lower()
    for leak in ("jane officer", "scathing", "dismissed", "refuse", "objection"):
        assert leak not in blob


def test_skips_malformed_rows_instead_of_dying():
    assert normalise_feature({}, "Swanbank") is None
    assert normalise_feature({"properties": {}}, "Swanbank") is None
    # No application number -> skipped, not crashed.
    assert normalise_feature({"properties": {"description": "x"}}, "Swanbank") is None
