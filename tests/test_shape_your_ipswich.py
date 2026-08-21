"""
Parser tests for the Shape Your Ipswich consultation scraper.

Two things matter here beyond "does it parse":
  1. Enumeration is via the load-more block routes discovered on /projects, not
     hardcoded numeric ids — so route discovery must survive markup.
  2. Invariant 8 (no user-generated content). EngagementHQ is where residents
     leave survey responses, comments and forum posts. The normaliser must
     whitelist Council's own project metadata and carry through nothing else,
     even if the upstream feed grows a field that holds resident content.

Fixtures in tests/fixtures/ are trimmed but otherwise verbatim Council output.

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

from scrape.shape_your_ipswich import (  # noqa: E402
    normalise_project,
    parse_block_routes,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(errors="replace")


# ---------------------------------------------------------------------------
# Enumeration: block load-more routes


def test_parse_block_routes_finds_every_list_block():
    routes = parse_block_routes(_fixture("syi_projects_list.html"))
    assert len(routes) == 3, "expected one load-more route per project-list block"
    for r in routes:
        assert r.startswith("https://www.shapeyouripswich.com.au/ccm/")
        assert "/load_more/" in r
    # De-duplicated and in document order.
    assert len(set(routes)) == len(routes)


def test_parse_block_routes_empty_when_markup_changes():
    """If the listing stops carrying load-more routes the scraper raises rather
    than silently producing nothing — this proves the discovery is what breaks
    first, not the whole dataset going quietly empty."""
    assert parse_block_routes("<html><body>no routes here</body></html>") == []


# ---------------------------------------------------------------------------
# Normalisation of the load-more JSON


def _load_more_records():
    return json.loads(_fixture("syi_load_more.json"))["result"]


def test_normalise_parses_locations_and_categories_as_lists():
    recs = [normalise_project(r) for r in _load_more_records()]
    recs = [r for r in recs if r]
    assert recs, "no records normalised — feed shape probably changed"
    ros = next(r for r in recs if r["slug"] == "rosewood-place-plan")
    # projectLocationArray is the primary suburb join key.
    assert "Rosewood" in ros["suburbs"]
    assert "Walloon" in ros["suburbs"]
    assert all(isinstance(s, str) for s in ros["suburbs"])
    # Categories come through as a clean list, including the escaped-slash one.
    assert "Waste/Resource Recovery" in ros["categories"]


def test_normalise_keeps_council_status_and_stable_ids():
    recs = [normalise_project(r) for r in _load_more_records() if r]
    for r in recs:
        assert r is None or isinstance(r["id"], int)
        assert r is None or r["status"] in ("Open", "Active", "Closed")
        # Slug is the stable EngagementHQ path segment.
        assert r is None or (r["slug"] and "/" not in r["slug"])


def test_normalise_summary_is_the_council_blurb():
    recs = {r["slug"]: r for r in map(normalise_project, _load_more_records()) if r}
    art = recs["ipswich-art-awards-2025"]
    assert art["summary"].startswith("Thank you to everyone")


# ---------------------------------------------------------------------------
# Invariant 8: NO user-generated content is ever carried through.


_ALLOWED_KEYS = {
    "id", "slug", "name", "summary", "status", "date", "date_str",
    "suburbs", "categories", "url", "source_url",
}


def test_normalise_only_emits_whitelisted_fields():
    for raw in _load_more_records():
        rec = normalise_project(raw)
        if rec is None:
            continue
        assert set(rec.keys()) == _ALLOWED_KEYS


def test_normalise_drops_any_ugc_field_even_if_upstream_adds_one():
    """The defamation-surface guard: even if Council's feed one day carries
    resident comments/submissions, a whitelist means none of it reaches us."""
    poisoned = {
        "projectID": 999,
        "projectPath": "https://www.shapeyouripswich.com.au/example",
        "projectName": "Example",
        "projectDescription": "A summary.",
        "projectStatus": "Open",
        "projectLocationArray": ["Ipswich"],
        "projectCategoryArray": ["Community"],
        # None of these must survive normalisation.
        "comments": [{"author": "Resident A", "body": "defamatory claim"}],
        "submissions": ["survey response text"],
        "guestbook": "user post",
        "forumPosts": [1, 2, 3],
    }
    rec = normalise_project(poisoned)
    assert set(rec.keys()) == _ALLOWED_KEYS
    blob = json.dumps(rec).lower()
    for leak in ("resident a", "defamatory", "survey response", "user post", "forumposts"):
        assert leak not in blob


def test_normalise_skips_malformed_rows_instead_of_dying():
    assert normalise_project({}) is None
    assert normalise_project({"projectID": 1}) is None  # no path/name
    assert normalise_project(
        {"projectID": 1, "projectPath": "https://x/y", "projectName": "  "}
    ) is None
