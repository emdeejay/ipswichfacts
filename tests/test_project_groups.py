"""Distinct Council records that share one pinned project URL.

The fixture is synthetic, shaped like the four observed shared-URL groups
(4/2/2/2 records) with contradictory phases, statuses and dates. Each record
must survive separately at the unchanged URL; nothing may pick a winner.
"""
import copy
import html as htmllib
import itertools
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import build_site as b

FIXTURE = Path(__file__).parent / "fixtures" / "project_groups.json"
GROUP_SIZES = {
    "synthetic-plains-road-road-resurfacing-303": 4,
    "synthetic-street-road-resurfacing-270": 2,
    "synthetic-avenue-road-resurfacing-293": 2,
    "synthetic-lane-road-safety-improvements-churchill": 2,
}
SINGLE = "synthetic-single-footpath"


def records():
    return copy.deepcopy(json.loads(FIXTURE.read_text())["projects"])


def graph(ps, meetings=None, news=None, capworks=()):
    return b.build_graph(ps, {}, meetings or {}, news or {}, list(capworks), [], [])


def build(tmp_path, ps, **kw):
    out = tmp_path / "out"
    g = graph(ps, **kw)
    urls = b.write_site(out, ps, {}, kw.get("meetings") or {}, kw.get("news") or {}, g, [], [], [])
    return out, urls, g


def page(out, slug):
    return (out / f"project/{slug}/index.html").read_text()


def sections(html):
    """{anchor: section html} for every per-record section on a page."""
    found = re.findall(r'<section class="project-record" id="([^"]+)">(.*?)</section>', html, re.S)
    return dict(found)


def head(html):
    return html.split("</head>", 1)[0]


class _Checker(HTMLParser):
    VOID = {"meta", "link", "br", "img", "input", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.ids, self.errors = [], [], []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k == "id":
                self.ids.append(v)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append((tag, list(self.stack[-3:])))
        else:
            self.stack.pop()


def test_every_record_survives_once_at_its_unchanged_url(tmp_path):
    ps = records()
    out, urls, _ = build(tmp_path, ps)
    for slug, n in GROUP_SIZES.items():
        html = page(out, slug)
        secs = sections(html)
        members = [p for p in ps if p["slug"] == slug]
        assert len(secs) == n == len(members)
        for p in members:
            sec = secs[f"record-{p['id']}"]
            assert f"Map record {p['id']}" in sec
            for field in ("status", "description", "ref", "managed_by"):
                assert htmllib.escape(p[field], quote=True) in sec
            assert b.format_ymd(p["updated"]) in sec
            assert htmllib.escape(p["phase"], quote=True) in sec
            assert f'href="{p["source_url"]}"' in sec
            assert html.count(f'id="record-{p["id"]}"') == 1
        assert [u for u, _ in urls].count(f"/project/{slug}/") == 1
    assert len([u for u, _ in urls if u.startswith("/project/")]) == len(GROUP_SIZES) + 1


def test_contradictory_records_have_no_winner_or_combined_facts(tmp_path):
    ps = records()
    out, _, _ = build(tmp_path, ps)
    html = page(out, "synthetic-plains-road-road-resurfacing-303")
    secs = sections(html)
    # Each record's own phase/status/date sits only in its own section.
    for p in [p for p in ps if p["slug"] == "synthetic-plains-road-road-resurfacing-303"]:
        own = secs[f"record-{p['id']}"]
        for other in secs.values():
            if other is not own:
                assert htmllib.escape(p["status"], quote=True) not in other
    # Page-level metadata attributes no single record's facts to the group.
    top = head(html) + html.split('<section class="project-record"', 1)[0].split("<main", 1)[1]
    for p in ps:
        if p["slug"] != "synthetic-plains-road-road-resurfacing-303":
            continue
        for field in ("status", "description", "what_to_expect"):
            if p.get(field):
                assert htmllib.escape(p[field], quote=True)[:40] not in top
        assert b.format_ymd(p["updated"]) not in top
        assert htmllib.escape(p["phase"], quote=True) not in head(html)
    assert "4 separate records" in head(html)
    assert 'application/ld+json' not in html


def test_feed_permutation_does_not_change_output(tmp_path):
    base = records()
    expected = None
    for n, order in enumerate([base, list(reversed(base)), base[3:] + base[:3]]):
        out, urls, _ = build(tmp_path / str(n), copy.deepcopy(order))
        pages = {slug: page(out, slug) for slug in [*GROUP_SIZES, SINGLE]}
        sitemap = sorted(urls)
        # List pages keep their existing feed-order tie-breaking; the links
        # they carry must not depend on it.
        lists = {p: sorted(re.findall(r'href="(/project/[^"]+)"', (out / p).read_text()))
                 for p in ["projects/index.html", "suburb/ipswich/index.html"]}
        if expected is None:
            expected = (pages, sitemap, lists)
        assert (pages, sitemap, lists) == expected
    anchors = list(sections(expected[0]["synthetic-plains-road-road-resurfacing-303"]))
    assert anchors == ["record-303", "record-304", "record-412", "record-413"]


def test_grouped_page_is_escaped_well_formed_and_uniquely_anchored(tmp_path):
    ps = records()
    ps[0]["status"] = '</p><script>alert("x")</script>'
    ps[0]["name"] = ps[1]["name"]  # unchanged group name
    out, _, _ = build(tmp_path, ps)
    for slug in GROUP_SIZES:
        html = page(out, slug)
        assert "<script>alert" not in html
        checker = _Checker()
        checker.feed(html)
        assert checker.errors == []
        assert len(checker.ids) == len(set(checker.ids))
        # The record index links resolve to anchors on the page.
        for target in re.findall(r'href="#([^"]+)"', html):
            assert target in checker.ids
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page(
        out, "synthetic-plains-road-road-resurfacing-303")


def test_single_record_pages_keep_existing_rendering(tmp_path):
    ps = records()
    out, urls, g = build(tmp_path, ps)
    single = next(p for p in ps if p["slug"] == SINGLE)
    assert page(out, SINGLE) == b.render_project(single, {}, g)
    assert "project-record" not in page(out, SINGLE)
    assert (f"/project/{SINGLE}/", "2026-03-01") in urls
    projects_list = (out / "projects/index.html").read_text()
    assert f'href="/project/{SINGLE}/"' in projects_list


def test_identical_duplicate_rows_are_not_distinct_records(tmp_path):
    ps = records()
    single = next(p for p in ps if p["slug"] == SINGLE)
    ps.append(copy.deepcopy(single))
    out, urls, g = build(tmp_path, ps)
    assert page(out, SINGLE) == b.render_project(single, {}, g)
    assert [u for u, _ in urls].count(f"/project/{SINGLE}/") == 1


def test_list_pages_link_each_record_to_its_own_section(tmp_path):
    ps = records()
    out, _, _ = build(tmp_path, ps)
    pages = {
        "projects/index.html": ps,
        "projects/phase/historic/index.html": [p for p in ps if p["phase"] == "Historic"],
        "suburb/ipswich/index.html": [p for p in ps if p["suburb"] == "Ipswich"],
        "suburb/synthetic-plains/index.html": [p for p in ps if p["suburb"] == "Synthetic Plains"],
        "division/1/index.html": [p for p in ps if 1 in p["divisions"]],
        "division/2/index.html": [p for p in ps if 2 in p["divisions"]],
    }
    for path, expected in pages.items():
        if not (out / path).exists():
            continue  # divisions render only with councillor data
        html = (out / path).read_text()
        for p in expected:
            href = (f"/project/{p['slug']}/" if p["slug"] == SINGLE
                    else f"/project/{p['slug']}/#record-{p['id']}")
            assert html.count(f'href="{href}"') == 1, (path, href)
    suburb = (out / "suburb/synthetic-plains/index.html").read_text()
    assert "Projects (5)" in suburb


def test_divisions_list_every_grouped_record(tmp_path):
    ps = records()
    g = graph(ps)
    g["councillors"] = [{"name": "Synthetic Councillor", "division": 1, "role": "Councillor"}]
    g["councillors_by_division"] = {1: g["councillors"], 2: []}
    for d in (1, 2):
        html = b.render_division(d, ps, {}, g)
        for p in [p for p in ps if d in p["divisions"]]:
            href = (f"/project/{p['slug']}/" if p["slug"] == SINGLE
                    else f"/project/{p['slug']}/#record-{p['id']}")
            assert html.count(f'href="{href}"') == 1


def test_streets_stay_with_the_record_that_names_them(tmp_path):
    ps = records()
    out, _, g = build(tmp_path, ps)
    slug = "synthetic-plains-road-road-resurfacing-303"
    alpha = (out / "street/alpha-street/index.html").read_text()
    beta = (out / "street/beta-road/index.html").read_text()
    assert f'href="/project/{slug}/#record-303"' in alpha
    assert f'href="/project/{slug}/#record-304"' not in alpha
    assert f'href="/project/{slug}/#record-304"' in beta
    assert f'href="/project/{slug}/#record-303"' not in beta
    # Council's notice for record 304 is reachable from the street it names.
    assert "Synthetic works notice B" in beta and "Synthetic works notice B" not in alpha
    # The exported mentions keep the slug → list shape, holding every record's streets.
    mentions = json.loads((out / "data/mentions.json").read_text())
    assert {"Alpha Street", "Beta Road"} <= set(mentions["project_streets"][slug])
    # Records are separate works: their streets aren't connected to each other.
    assert "Beta Road" not in g["related_streets"].get("Alpha Street", [])
    secs = sections(page(out, slug))
    assert "Alpha Street" in secs["record-303"] and "Alpha Street" not in secs["record-304"]


def test_cross_source_links_attach_once_to_the_shared_name(tmp_path):
    ps = records()
    meetings = {"meetings": [{"slug": "synthetic-meeting", "id": "M1", "doc_type": "MIN", "committee": "Synthetic Committee",
                              "date": "2026-02-01", "items": [{"title": "Synthetic Plains Road Road Resurfacing update",
                                                                "anchor": "item-1", "text": ""}]}]}
    news = {"posts": [{"slug": "synthetic-news", "id": 1, "date": "2026-03-01",
                       "title": "Synthetic Plains Road- Road Resurfacing begins", "url": "https://example.org/n"}]}
    out, _, g = build(tmp_path, ps, meetings=meetings, news=news)
    slug = "synthetic-plains-road-road-resurfacing-303"
    html = page(out, slug)
    shared = html.split('id="linked-by-name"', 1)[1]
    assert shared.count('href="/meeting/synthetic-meeting/#item-1"') >= 1
    assert shared.count('href="/news/synthetic-news/"') >= 1
    for sec in sections(html).values():
        assert "/meeting/synthetic-meeting/" not in sec and "/news/synthetic-news/" not in sec
    meeting = (out / "meeting/synthetic-meeting/index.html").read_text()
    assert meeting.count(f'href="/project/{slug}/"') == 1
    news_page = (out / "news/synthetic-news/index.html").read_text()
    assert news_page.count(f'href="/project/{slug}/"') == 1


def test_grouped_funding_keeps_the_money_disclaimer(tmp_path):
    ps = records()
    capworks = [{"cycle": "2025-2026", "fy_columns": ["2025-2026", "2026-2027", "2027-2028"],
                 "amounts_published": True, "source_url": "https://example.org/cw.pdf",
                 "programs": [{"section": "Synthetic", "rows": [
                     {"project": "Synthetic Plains Road- Road Resurfacing", "page": 2,
                      "amounts": {"2025-2026": 100, "2026-2027": 0, "2027-2028": 0}, "total": 100}]}]}]
    g = graph(ps, capworks=capworks)
    groups = g["project_groups"]["synthetic-plains-road-road-resurfacing-303"]
    html = b.render_project_group(groups, {}, g)
    shared = html.split('id="linked-by-name"', 1)[1]
    assert "$100k" in shared or "$100,000" in shared
    assert 'class="disclaimer"' in shared


def test_exported_json_and_registry_identity_unchanged(tmp_path):
    ps = records()
    reg = b.load_registry(tmp_path)
    for p in ps:
        p.pop("slug")
    b.assign_stable_slugs(reg, "project", ps, b._project_registry_key, lambda p: p.get("name"),
                          lambda p: [p.get("suburb")], "2026-10-25")
    before_reg = copy.deepcopy(reg)
    before = copy.deepcopy(ps)
    # The registry key is shared within a group, so the group shares one slug.
    assert len({p["slug"] for p in ps if p["ref"] == "SYN-0001"}) == 1
    out, _, _ = build(tmp_path, ps)
    assert json.loads((out / "data/projects.json").read_text()) == before == ps
    assert reg == before_reg


@pytest.mark.parametrize("change", ["body", "date"])
def test_unrelated_conflicting_page_writes_still_preserve_prior_output(tmp_path, monkeypatch, change):
    """The global guard stays: grouping is deliberate for projects only."""
    ms = [{"slug": "synthetic-meet", "id": "A", "doc_type": "MIN", "committee": "One", "date": "2026-01-01", "items": []},
          {"slug": "synthetic-meet", "id": "B", "doc_type": "MIN", "committee": "Two", "date": "2026-01-01", "items": []}]
    if change == "date":
        ms[1]["date"] = "2026-01-02"
        monkeypatch.setattr(b, "render_meeting", lambda *a: "same body")
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "sentinel"
    sentinel.write_bytes(b"previous successful output\x00")
    ps = records()
    with pytest.raises(ValueError, match="meeting/synthetic-meet") as e:
        b.write_site(out, ps, {}, {"meetings": ms}, {}, graph(ps, meetings={"meetings": ms}), [], [], [])
    assert "'A'" in str(e.value) and "'B'" in str(e.value)
    assert list(out.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"previous successful output\x00"


def test_group_sitemap_lastmod_is_page_level_only(tmp_path):
    ps = records()
    _, urls, _ = build(tmp_path, ps)
    lastmod = dict(urls)
    # Page modification time: the latest record update. It is not shown as a
    # project fact anywhere on the page.
    assert lastmod["/project/synthetic-plains-road-road-resurfacing-303/"] == "2026-08-19"
    assert lastmod["/project/synthetic-lane-road-safety-improvements-churchill/"] == "2026-10-19"


def test_street_matches_are_not_presented_as_name_matches():
    """Street-derived meeting items and road impacts are labelled as street
    matches, never as matches to the project name or to one record."""
    ps = records()
    meetings = {"meetings": [{"slug": "synthetic-meeting", "id": "M1", "doc_type": "MIN",
                              "committee": "Synthetic Committee", "date": "2026-02-01", "items": [
                                  {"title": "Synthetic Plains Road- Road Resurfacing update", "anchor": "item-1", "text": ""},
                                  {"title": "Footpath works on Alpha Street", "anchor": "item-2", "text": ""}]}]}
    closures = {"closures": [{"road_name": "Alpha Street", "suburb": "Synthetic Plains", "status": "Active",
                              "start_time": "2026-04-01T00:00:00", "event_type": "Roadworks",
                              "impact": "Synthetic lane closure"}]}
    g = b.build_graph(ps, closures, meetings, {}, [], [], [])
    group = g["project_groups"]["synthetic-plains-road-road-resurfacing-303"]
    html = b.render_project_group(group, closures, g)
    by_name = html.split('id="linked-by-name"', 1)[1].split('id="linked-by-street"', 1)[0]
    by_street = html.split('id="linked-by-street"', 1)[1]
    assert "/meeting/synthetic-meeting/#item-1" in by_name
    assert "/meeting/synthetic-meeting/#item-2" not in by_name
    assert "Synthetic lane closure" not in by_name
    assert "/meeting/synthetic-meeting/#item-2" in by_street
    assert "Synthetic lane closure" in by_street
    assert "Matched by street" in by_street and "not through the project name" in by_street
    assert "project name shared" not in by_street
    for sec in sections(html).values():
        assert "Synthetic lane closure" not in sec and "#item-2" not in sec
    # The single-record page keeps its existing combined timeline wording.
    single = next(p for p in ps if p["slug"] == SINGLE)
    assert "linked-by-street" not in b.render_project(single, closures, g)


@pytest.mark.parametrize("order", [[0, 1], [1, 0]])
def test_differing_names_on_one_slug_keep_every_name_match(order):
    """Every record's name core matches, whatever the feed order, and the
    shared page is linked once. Wording names the records' own names."""
    base = [dict(id=1, slug="shared", name="Alpha Street Footpath Upgrade", suburb="Ipswich", divisions=[1],
                 ref="SYN-A", phase="Current Program", status="Synthetic A", description="Synthetic A"),
            dict(id=2, slug="shared", name="Beta Road Drainage Upgrade", suburb="Ipswich", divisions=[1],
                 ref="SYN-A", phase="Current Program", status="Synthetic B", description="Synthetic B")]
    ps = [copy.deepcopy(base[i]) for i in order]
    meetings = {"meetings": [{"slug": "synthetic-meeting", "id": "M1", "doc_type": "MIN", "committee": "C",
                              "date": "2026-02-01", "items": [
                                  {"title": "Alpha Street Footpath Upgrade update", "anchor": "a", "text": ""},
                                  {"title": "Beta Road Drainage Upgrade update", "anchor": "b", "text": ""}]}]}
    news = {"posts": [{"slug": "n", "id": 1, "date": "2026-03-01", "url": "https://example.org/n",
                       "title": "Alpha Street Footpath Upgrade and Beta Road Drainage Upgrade"}]}
    g = b.build_graph(ps, {}, meetings, news, [], [], [])
    assert [r["anchor"] for r in g["project_meeting_items"]["shared"]] == ["a", "b"]
    assert len(g["project_news_items"]["shared"]) == 1
    assert len(g["news_projects"]["n"]) == 1
    meeting = b.render_meeting(meetings["meetings"][0], g)
    assert meeting.count('href="/project/shared/"') == 1
    html = b.render_project_group(g["project_groups"]["shared"], {}, g)
    assert "2 different names" in html and "under this name" not in html
    assert "project name shared" not in html
    desc = re.search(r'name="description" content="([^"]*)"', html).group(1)
    assert "Alpha Street Footpath Upgrade / Beta Road Drainage Upgrade" not in desc
    assert "different names" in desc
