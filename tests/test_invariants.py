"""
Invariants from CLAUDE.md that are cheap to state and easy to lose.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build.build_site import (  # noqa: E402
    PLANNINGALERTS_URL,
    _planningalerts_html,
    render_404,
    render_about,
    render_layout,
)


# ---------------------------------------------------------------------------
# Invariant 7: PlanningAlerts owns DAs — don't scrape them, DO link out.


def test_street_and_suburb_pages_point_at_planningalerts():
    html = _planningalerts_html("Gordon Street")
    assert PLANNINGALERTS_URL in html
    assert "development application" in html.lower()
    assert "Gordon Street" in html


def test_about_page_states_the_da_boundary():
    html = render_about()
    assert PLANNINGALERTS_URL in html
    assert "not scraped or republished" in html


def test_we_never_claim_to_cover_das():
    """Invariant 7 is 'link out', not 'do it ourselves'."""
    html = _planningalerts_html("Somewhere")
    assert "doesn't cover development applications" in html


# ---------------------------------------------------------------------------
# Accessibility: the search box is the site's primary control.


def test_layout_has_skip_link_and_landmark():
    html = render_layout("T", "D", "/", "<p>body</p>")
    assert 'class="skip-link"' in html
    assert 'href="#main"' in html
    assert 'id="main"' in html
    assert 'lang="en-AU"' in html


def test_404_offers_search_not_a_dead_end():
    html = render_404()
    assert "data-ipswichfacts-search" in html
    assert 'href="/projects/"' in html


def test_search_input_is_labelled_and_describes_its_listbox():
    """A placeholder is not a label. Checked in the widget source, which is
    embedded in the build."""
    from build.build_site import _WIDGET_JS

    assert 'for="if-search"' in _WIDGET_JS
    assert 'id="if-search"' in _WIDGET_JS
    assert 'role="combobox"' in _WIDGET_JS
    assert 'aria-controls="if-search-results"' in _WIDGET_JS
    assert 'role="listbox"' in _WIDGET_JS


def test_search_announces_result_count_and_tracks_expanded_state():
    """aria-expanded that never updates is a lie to a screen reader."""
    from build.build_site import _WIDGET_JS

    assert "aria-live=\"polite\"" in _WIDGET_JS
    assert "setAttribute('aria-expanded', 'false')" in _WIDGET_JS
    assert "setAttribute('aria-expanded', String(!results.hidden))" in _WIDGET_JS
    assert "No results" in _WIDGET_JS


def test_visually_hidden_utility_exists_and_is_not_display_none():
    """display:none would hide it from screen readers too, defeating the point."""
    from build.build_site import _CSS

    assert ".visually-hidden" in _CSS
    block = _CSS.split(".visually-hidden", 1)[1].split("}", 1)[0]
    assert "display: none" not in block
    assert "position: absolute" in block


# ---------------------------------------------------------------------------
# Tip jar: exactly one ask per page.


def test_footer_does_not_repeat_an_ask_the_page_already_makes():
    """The homepage and About carry their own support section; a second
    button in the footer just reads as nagging."""
    with_support = render_layout("T", "D", "/", '<a class="coffee-btn" href="#">Buy me a coffee</a>')
    assert with_support.count("coffee-btn") == 1

    plain = render_layout("T", "D", "/street/x/", "<p>no ask here</p>")
    assert plain.count("coffee-btn") == 1  # footer supplies it


def test_about_page_asks_exactly_once():
    assert render_about().count("coffee-btn") == 1


def test_404_asks_exactly_once():
    assert render_404().count("coffee-btn") == 1


# ---------------------------------------------------------------------------
# Payload discipline: the widget adds interactivity, not information.


def test_pages_without_search_fetch_no_data():
    """The related panel used to make every page download the whole dataset to
    render links the static HTML already had. Only search pages pay now."""
    from build.build_site import _WIDGET_JS

    assert "data-ipswichfacts-related" not in _WIDGET_JS
    assert "mountRelated" not in _WIDGET_JS
    assert "if (!searchEls.length) return;" in _WIDGET_JS


def test_widget_only_fetches_what_the_index_reads():
    from build.build_site import _WIDGET_JS

    # mentions.json is still published as open data, but a visitor typing in a
    # search box shouldn't download the cross-reference graph. Check for the
    # fetch, not the word — the comments name these files deliberately.
    assert "/mentions.json`" not in _WIDGET_JS
    assert "/closures.json`" not in _WIDGET_JS
    for needed in ("projects.json", "streets.json", "suburbs.json", "meetings.json", "news.json"):
        assert f"/{needed}`" in _WIDGET_JS


def test_archive_is_opt_in_not_default():
    from build.build_site import _WIDGET_JS

    # The archive must never be in the initial Promise.all.
    initial = _WIDGET_JS.split("async function loadData()", 1)[1].split("}", 1)[0]
    assert "archive" not in initial
    assert "meetings-archive.json" in _WIDGET_JS  # but reachable on click
    assert "news-archive.json" in _WIDGET_JS


# ---------------------------------------------------------------------------
# What's-on listings aren't news about a street.


def test_whats_on_listings_are_detected():
    from build.build_site import _is_whats_on_listing

    assert _is_whats_on_listing({"title": "December 10: Trivia Night", "categories": ["Now"]})
    assert _is_whats_on_listing({"title": "January 22: Celebration of wine", "categories": ["Now"]})


def test_real_council_stories_are_never_filtered():
    """'Now' is the site's default category (3,527 of 4,900 posts) — filtering
    on it alone would bury real stories."""
    from build.build_site import _is_whats_on_listing

    assert not _is_whats_on_listing(
        {"title": "Council secures funding for safer roads in Ipswich",
         "categories": ["Council", "Featured", "Now", "Work"]})
    # Only 'Now', but not a listing title.
    assert not _is_whats_on_listing({"title": "Mayor supports USQ funding calls", "categories": ["Now"]})
    # Listing-shaped title but categorised as Council business.
    assert not _is_whats_on_listing({"title": "March 3: Council meeting", "categories": ["Council", "Now"]})
