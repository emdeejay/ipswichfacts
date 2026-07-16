"""
URL stability.

CLAUDE.md: "Don't rename URL slugs once published — they get indexed by
Google." Slugs derive from Council-supplied names, which change; Council's ids
don't. The registry pins id -> slug on first sight and keeps it forever, so a
rename upstream changes a page's title and not its address.

It also settles collisions: Council's map publishes several distinct works
under one name (seven "Redbank Plains Road- Road Resurfacing" jobs across three
suburbs) which all slugified to a single URL and silently overwrote each other.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build.build_site import assign_stable_slugs, load_registry  # noqa: E402


def _registry():
    return {"version": 1, "entities": {}}


def _projects(*names_ids):
    return [{"id": i, "name": n, "suburb": s} for n, i, s in names_ids]


def test_slug_survives_a_rename():
    """The invariant. Council renames a project; the URL must not move."""
    reg = _registry()
    items = _projects(("Gordon Street Pedestrian Link", 42, "Ipswich"))
    assign_stable_slugs(reg, "project", items, lambda p: p["id"], lambda p: p["name"])
    first = items[0]["slug"]

    renamed = _projects(("Gordon Street Active Transport Connection Stage 2", 42, "Ipswich"))
    assign_stable_slugs(reg, "project", renamed, lambda p: p["id"], lambda p: p["name"])
    assert renamed[0]["slug"] == first == "gordon-street-pedestrian-link"


def test_identical_names_get_distinct_stable_slugs():
    """Seven real jobs, one name. Every one must get its own page."""
    reg = _registry()
    items = _projects(
        ("Redbank Plains Road- Road Resurfacing", 685, "Bellbird Park"),
        ("Redbank Plains Road- Road Resurfacing", 795, "Swanbank"),
        ("Redbank Plains Road- Road Resurfacing", 796, "Redbank Plains"),
    )
    assign_stable_slugs(
        reg, "project", items, lambda p: p["id"], lambda p: p["name"],
        hint_fn=lambda p: [p.get("suburb")],
    )
    slugs = [p["slug"] for p in items]
    assert len(set(slugs)) == 3, f"collision: {slugs}"


def test_discriminator_is_stable_not_positional():
    """A counter would reshuffle when the feed reorders or a record leaves —
    silently moving indexed URLs. Slugs must depend only on stable data."""
    reg_a, reg_b = _registry(), _registry()
    a = _projects(
        ("Foo Road Resurfacing", 1, "Alpha"),
        ("Foo Road Resurfacing", 2, "Beta"),
        ("Foo Road Resurfacing", 3, "Gamma"),
    )
    b = list(reversed(_projects(
        ("Foo Road Resurfacing", 1, "Alpha"),
        ("Foo Road Resurfacing", 2, "Beta"),
        ("Foo Road Resurfacing", 3, "Gamma"),
    )))
    for reg, items in ((reg_a, a), (reg_b, b)):
        assign_stable_slugs(reg, "project", items, lambda p: p["id"], lambda p: p["name"],
                            hint_fn=lambda p: [p.get("suburb")])
    by_id_a = {p["id"]: p["slug"] for p in a}
    by_id_b = {p["id"]: p["slug"] for p in b}
    assert by_id_a == by_id_b, "feed order changed the slugs"


def test_removing_a_record_does_not_move_its_neighbours():
    """If one of a colliding group disappears, the survivors keep their URLs."""
    reg = _registry()
    items = _projects(
        ("Foo Road Resurfacing", 1, "Alpha"),
        ("Foo Road Resurfacing", 2, "Beta"),
    )
    assign_stable_slugs(reg, "project", items, lambda p: p["id"], lambda p: p["name"],
                        hint_fn=lambda p: [p.get("suburb")])
    kept = items[1]["slug"]

    remaining = _projects(("Foo Road Resurfacing", 2, "Beta"))
    assign_stable_slugs(reg, "project", remaining, lambda p: p["id"], lambda p: p["name"],
                        hint_fn=lambda p: [p.get("suburb")])
    assert remaining[0]["slug"] == kept


def test_new_entity_never_steals_a_pinned_slug():
    reg = _registry()
    first = _projects(("Foo Road Upgrade", 1, "Alpha"))
    assign_stable_slugs(reg, "project", first, lambda p: p["id"], lambda p: p["name"],
                        hint_fn=lambda p: [p.get("suburb")])
    later = _projects(("Foo Road Upgrade", 2, "Beta"))
    assign_stable_slugs(reg, "project", later, lambda p: p["id"], lambda p: p["name"],
                        hint_fn=lambda p: [p.get("suburb")])
    assert later[0]["slug"] != first[0]["slug"]
    assert first[0]["slug"] == "foo-road-upgrade"


def test_registry_reports_whether_it_changed():
    """CI only commits when something new was minted — no daily churn."""
    reg = _registry()
    items = _projects(("Foo Road Upgrade", 1, "Alpha"))
    assert assign_stable_slugs(reg, "project", items, lambda p: p["id"], lambda p: p["name"]) is True
    assert assign_stable_slugs(reg, "project", items, lambda p: p["id"], lambda p: p["name"]) is False


def test_entities_without_a_stable_id_are_left_alone():
    reg = _registry()
    items = [{"id": None, "name": "Mystery", "slug": "preexisting"}]
    assign_stable_slugs(reg, "project", items, lambda p: p.get("id"), lambda p: p["name"])
    assert items[0]["slug"] == "preexisting"


def test_live_registry_has_no_duplicate_slugs():
    """Two entities sharing a URL means one page silently overwrites the other
    — the bug this whole mechanism exists to kill."""
    reg = load_registry(Path("data"))
    for kind, entries in reg.get("entities", {}).items():
        slugs = [v["slug"] for v in entries.values()]
        dupes = {s for s in slugs if slugs.count(s) > 1}
        assert not dupes, f"{kind}: duplicate slugs {list(dupes)[:3]}"
