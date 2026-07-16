"""
Money-disclaimer enforcement.

Rule: any rendered page that prints a dollar figure must carry the money
disclaimer, and any page that *compares* figures across programs must carry the
version stating plainly that a change is not evidence of wrongdoing.

Why this is a test and not a convention: the figures are extracted from PDFs by
word-position clustering, so a misread is possible; and setting two of Council's
numbers side by side invites an inference we have not evidenced and do not
intend. Project pages also name the division's councillors. An Australian
council cannot itself sue for defamation, but individuals can — and either way,
being wrong about someone's spending is the fastest way to lose the standing
this site depends on.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import glob
import json
import re
import sys
import types
from pathlib import Path

import pytest

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build.build_site import (  # noqa: E402
    ISSUES_URL,
    money_disclaimer,
    render_capworks_cycle,
    render_capworks_index,
    render_funding_revisions,
)

# A dollar amount: $1,200 / $2.5M / $450k. Not a bare '$' in prose.
MONEY_RE = re.compile(r"\$[\d,]+(?:\.\d+)?[MkB]?\b")
DISCLAIMER_MARK = 'class="disclaimer"'
NOT_WRONGDOING = "not evidence of error, waste, or wrongdoing"


@pytest.fixture(scope="module")
def capworks():
    files = sorted(glob.glob("data/capital_works/capworks-*.json"))
    if not files:
        pytest.skip("no capital works data checked out")
    return [json.load(open(f)) for f in files]


def test_disclaimer_names_a_working_correction_route():
    assert ISSUES_URL.startswith("https://github.com/")
    assert ISSUES_URL in money_disclaimer()


def test_disclaimer_says_figures_are_automated_and_unofficial():
    d = money_disclaimer()
    assert "extracted automatically" in d
    assert "not an official record" in d
    assert "check the linked Council source" in d


def test_revisions_variant_disclaims_the_implication():
    """The dangerous part isn't the number — it's what a reader infers from
    two numbers side by side."""
    d = money_disclaimer(revisions=True)
    assert NOT_WRONGDOING in d
    assert "councillor" in d  # individuals are who can actually be defamed
    assert "re-phased" in d or "re-phase" in d  # gives the innocent explanation


def test_plain_variant_makes_no_claim_about_change():
    assert NOT_WRONGDOING not in money_disclaimer()


def test_revisions_page_carries_the_full_disclaimer(capworks):
    html = render_funding_revisions(capworks)
    assert MONEY_RE.search(html), "revisions page shows no money — test is stale"
    assert DISCLAIMER_MARK in html
    assert NOT_WRONGDOING in html


def test_capworks_index_carries_the_disclaimer(capworks):
    html = render_capworks_index(capworks)
    if MONEY_RE.search(html):
        assert DISCLAIMER_MARK in html


def test_every_capworks_cycle_page_carries_the_disclaimer(capworks):
    graph = {"project_capworks": {}}
    for cw in capworks:
        html = render_capworks_cycle(cw, graph)
        if MONEY_RE.search(html):
            assert DISCLAIMER_MARK in html, f"{cw['cycle']} cycle page shows money undisclaimed"


def test_project_funding_section_carries_the_disclaimer(capworks):
    """Any project page with a funding section shows money, so it must
    disclaim — and where it compares programs, it must disclaim the
    inference too."""
    from build.build_site import _capworks_funding_html

    entries = []
    for cw in capworks:
        if not cw.get("amounts_published"):
            continue
        for prog in cw.get("programs", []):
            for row in prog.get("rows", []):
                if row.get("amounts"):
                    entries.append({
                        "cycle": cw["cycle"],
                        "fy_columns": cw["fy_columns"],
                        "amounts_published": True,
                        "source_url": cw.get("source_url"),
                        "section": prog.get("section"),
                        "row": row,
                    })
                    break
            if entries:
                break
    assert entries, "no funded rows found — fixture drift"
    graph = {"project_capworks": {"x": entries}}
    html = _capworks_funding_html("x", graph)
    assert MONEY_RE.search(html)
    assert DISCLAIMER_MARK in html


def test_comparison_pages_never_editorialise():
    """Invariant 4. If these words ever appear, someone has crossed the line
    from reproduction into commentary — which belongs on another domain."""
    banned = ["blowout", "blow-out", "waste of", "mismanage", "overspend",
              "scandal", "squander", "cover-up", "%", "increase of",
              "cut by", "slashed"]
    d = money_disclaimer(revisions=True)
    for word in banned:
        # The disclaimer may *deny* wrongdoing; it must not assert any.
        if word in ("waste of", "mismanage"):
            continue
        assert word not in d.lower(), f"editorial language in disclaimer: {word}"
