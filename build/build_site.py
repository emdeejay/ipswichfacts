"""
Turn scraped JSON into a static site.

Reads:
    data/projects.json      (from scrape.civic_projects)
    data/closures.json      (from scrape.road_closures)
    data/meetings.json      (from scrape.council_meetings)

Writes:
    site/index.html
    site/project/<slug>/index.html
    site/suburb/<slug>/index.html
    site/street/<slug>/index.html
    site/meeting/<slug>/index.html
    site/data/projects.json         (client widget data)
    site/data/closures.json
    site/data/meetings.json         (slim: no item text)
    site/data/mentions.json
    site/data/streets.json
    site/data/suburbs.json
    site/sitemap.xml
    site/robots.txt
    site/css/site.css
    site/js/widget.js

Every page ships pre-rendered HTML with a canonical URL Google can index,
plus embeds the widget that hydrates against /data/*.json for live drill-down.

Usage:
    python -m build.build_site
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scrape.extract_mentions import Extractor

# ---------------------------------------------------------------------------
# Config

BASE_URL = "https://ipswichfacts.au"

# Tip jar. Set to None to hide the coffee link entirely. Update after you sign
# up at buymeacoffee.com / ko-fi.com / github.com/sponsors.
COFFEE_URL = "https://buymeacoffee.com/mdj.au"
COFFEE_LABEL = "Buy me a coffee"

# Google Search Console ownership verification (meta-tag method; the DNS
# host doesn't allow TXT records). Set to None to omit the tag.
GOOGLE_SITE_VERIFICATION = "ECKRlA4paFCOzc3-zwWE3wORqBHl6LWTEr_8z4WblYA"

COUNCILLORS = {
    1: "Cr Pye Augustine",
    2: "Cr Jacob Madsen",
    3: "Cr Marnie Doyle",
    4: "Cr Paul Tully",
    # (Divisions 1-4 shown on the map. Full LGA has more councillors;
    # extend when we scrape the councillor pages.)
}

# ---------------------------------------------------------------------------
# Helpers


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:80] or "x"


def h(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s))


def phase_class(phase: str | None) -> str:
    return "phase-" + slugify(phase or "unknown")


def format_ymd(ymd: str | None) -> str:
    if not ymd:
        return ""
    # Accept both "YYYY-MM-DD" (normalised) and "YYYYMMDD" (raw upstream)
    if re.fullmatch(r"\d{8}", ymd):
        ymd = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return ymd


def dedupe(items: list[Any], key) -> list[Any]:
    seen: set = set()
    out: list = []
    for x in items:
        k = key(x)
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


# ---------------------------------------------------------------------------
# Load


def load(inp: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    projects = json.loads((inp / "projects.json").read_text())
    closures_path = inp / "closures.json"
    closures = json.loads(closures_path.read_text()) if closures_path.exists() else {"closures": []}
    meetings_path = inp / "meetings.json"
    meetings = json.loads(meetings_path.read_text()) if meetings_path.exists() else {"meetings": []}
    _assign_meeting_slugs(meetings.get("meetings", []))
    return projects, closures, meetings


def _assign_meeting_slugs(meetings: list[dict[str, Any]]) -> None:
    """Slug = committee-name-YYYY-MM-DD; disambiguate collisions with the
    meeting id. Deterministic because the scraper sorts by (date, id)."""
    taken: set[str] = set()
    for m in sorted(meetings, key=lambda x: (x.get("date") or "", x.get("id") or "")):
        slug = slugify(f"{m.get('committee') or m.get('committee_code')}-{m.get('date')}")
        if slug in taken:
            slug = slugify(f"{slug}-{m.get('id')}")
        taken.add(slug)
        m["slug"] = slug


# ---------------------------------------------------------------------------
# Extract streets from project descriptions


STREET_TYPES = (
    "Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Parade|Pde|Terrace|Tce|"
    "Highway|Hwy|Lane|Ln|Boulevard|Blvd|Court|Ct|Crescent|Cres|Close|Cl|"
    "Place|Pl|Way|Circuit|Cct|Bikeway|Motorway|Mwy|Trail"
)
STREET_RE = re.compile(
    r"\b([A-Z][A-Za-z']+(?:\s+[A-Z][A-Za-z']+){0,3})\s+(" + STREET_TYPES + r")\b"
)


def extract_streets_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    names = set()
    for m in STREET_RE.finditer(text):
        name = f"{m.group(1)} {_normalise_type(m.group(2))}"
        names.add(name)
    return sorted(names)


def _normalise_type(t: str) -> str:
    mapping = {
        "St": "Street", "Rd": "Road", "Ave": "Avenue", "Dr": "Drive",
        "Pde": "Parade", "Tce": "Terrace", "Hwy": "Highway", "Ln": "Lane",
        "Blvd": "Boulevard", "Ct": "Court", "Cres": "Crescent",
        "Cl": "Close", "Pl": "Place", "Cct": "Circuit", "Mwy": "Motorway",
    }
    return mapping.get(t, t)


# ---------------------------------------------------------------------------
# Build the entity graph


def build_graph(projects, closures, meetings) -> dict[str, Any]:
    streets_set: set[str] = set()
    suburbs_set: set[str] = set()

    # Gather all street mentions from project name + description + status + what_to_expect,
    # and suburb from the SUBURB field.
    project_streets: dict[str, list[str]] = {}
    project_suburbs: dict[str, str] = {}

    for p in projects:
        text_blob = " . ".join(filter(None, [p.get("name"), p.get("description"), p.get("status"), p.get("what_to_expect")]))
        streets = extract_streets_from_text(text_blob)
        project_streets[p["slug"]] = streets
        streets_set.update(streets)

        if p.get("suburb"):
            project_suburbs[p["slug"]] = p["suburb"]
            suburbs_set.add(p["suburb"])

    # Same for closures: pull road_name/suburb directly.
    closure_streets: dict[str, list[str]] = {}
    closure_suburbs: dict[str, str] = {}

    for i, c in enumerate(closures.get("closures", [])):
        cid = f"c{i}"
        rn = c.get("road_name")
        streets = extract_streets_from_text(rn) if rn else []
        if rn and not streets and rn.strip():
            streets = [rn.strip()]
        closure_streets[cid] = streets
        streets_set.update(streets)
        if c.get("suburb"):
            closure_suburbs[cid] = c["suburb"]
            suburbs_set.add(c["suburb"])

    # Meetings: streets via the regex extractor; suburbs matched against the
    # gazetteer of suburb names already known from projects + closures
    # (deliberately no separate suburb list — see docs/notes.md).
    suburb_ex = Extractor(streets=[], suburbs=sorted(suburbs_set))
    meeting_streets: dict[str, list[str]] = {}
    meeting_suburbs: dict[str, list[str]] = {}
    street_meeting_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    suburb_meeting_items: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for m in meetings.get("meetings", []):
        m_streets: set[str] = set()
        m_suburbs: set[str] = set()
        for item in m.get("items", []):
            blob = " . ".join(filter(None, [item.get("title"), item.get("text")]))
            i_streets = extract_streets_from_text(blob)
            i_suburbs = [f["value"] for f in suburb_ex.find(blob) if f["kind"] == "suburb"]
            ref = {
                "slug": m["slug"],
                "committee": m.get("committee"),
                "date": m.get("date"),
                "title": item.get("title"),
                "anchor": item.get("anchor"),
            }
            for s in i_streets:
                street_meeting_items[s].append(ref)
            for s in i_suburbs:
                suburb_meeting_items[s].append(ref)
            item["streets"] = i_streets
            item["suburbs"] = i_suburbs
            m_streets.update(i_streets)
            m_suburbs.update(i_suburbs)
        meeting_streets[m["slug"]] = sorted(m_streets)
        meeting_suburbs[m["slug"]] = sorted(m_suburbs)
        streets_set.update(m_streets)

    return {
        "streets": sorted(streets_set),
        "suburbs": sorted(suburbs_set),
        "project_streets": project_streets,
        "project_suburbs": project_suburbs,
        "closure_streets": closure_streets,
        "closure_suburbs": closure_suburbs,
        "meeting_streets": meeting_streets,
        "meeting_suburbs": meeting_suburbs,
        "street_meeting_items": dict(street_meeting_items),
        "suburb_meeting_items": dict(suburb_meeting_items),
    }


# ---------------------------------------------------------------------------
# Render


def render_layout(title: str, description: str, path: str, body: str) -> str:
    canonical = f"{BASE_URL}{path}"
    gsc_meta = (
        f'\n<meta name="google-site-verification" content="{h(GOOGLE_SITE_VERIFICATION)}">'
        if GOOGLE_SITE_VERIFICATION else ""
    )
    coffee_footer = ""
    if COFFEE_URL:
        coffee_footer = (
            f'<p class="coffee">This is a volunteer project. If it saved you '
            f'time, <a href="{h(COFFEE_URL)}" rel="noopener">'
            f'☕ {h(COFFEE_LABEL)}</a>.</p>'
        )
    return f"""<!doctype html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<title>{h(title)} — Ipswich Facts</title>
<meta name="description" content="{h(description)}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="canonical" href="{h(canonical)}">{gsc_meta}
<link rel="stylesheet" href="/css/site.css">
<meta property="og:title" content="{h(title)}">
<meta property="og:description" content="{h(description)}">
<meta property="og:url" content="{h(canonical)}">
<meta property="og:site_name" content="Ipswich Facts">
</head>
<body>
<header class="site-header">
  <a href="/" class="brand">Ipswich Facts</a>
  <nav>
    <a href="/">Search</a>
    <a href="/about/">About</a>
  </nav>
</header>
<main>
{body}
</main>
<footer class="site-footer">
  <p><strong>Unofficial.</strong> This site reproduces public data from
  Ipswich City Council so it can be searched and cross-referenced.
  Council's own systems are the source of truth. Data licensed
  <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.
  Every item links back to its Council source.</p>
  {coffee_footer}
</footer>
<script type="module" src="/js/widget.js"></script>
</body>
</html>
"""


def render_index(projects, closures, meetings, graph) -> str:
    by_phase: dict[str, int] = defaultdict(int)
    for p in projects:
        by_phase[p.get("phase") or "Unknown"] += 1

    phase_html = "\n".join(
        f'<li><a href="/projects/phase/{slugify(k)}/">'
        f'<span class="{phase_class(k)}">{h(k)}</span> <b>{v}</b></a></li>'
        for k, v in sorted(by_phase.items(), key=lambda x: -x[1])
    )

    active_closures = [c for c in closures.get("closures", []) if c.get("status") != "Archived"]

    closure_html = ""
    if active_closures:
        rows = []
        for c in active_closures[:20]:
            rows.append(
                f'<tr><td>{h(c.get("road_name"))}</td>'
                f'<td>{h(c.get("suburb"))}</td>'
                f'<td>{h(c.get("event_type"))}</td>'
                f'<td>{h(c.get("impact"))}</td></tr>'
            )
        closure_html = "<h2>Active road impacts</h2><table class='data'>" \
            + "<thead><tr><th>Road</th><th>Suburb</th><th>Type</th><th>Impact</th></tr></thead>" \
            + "<tbody>" + "\n".join(rows) + "</tbody></table>"

    body = f"""
<section class="hero">
  <h1>Find out what your Council is doing on your street.</h1>
  <p>Type a street name, suburb, or project name.</p>
  <div data-ipswichfacts-search></div>
</section>

<section class="stats">
  <h2>By the numbers</h2>
  <ul class="grid">
    <li><b>{len(projects)}</b><span>Civic projects tracked</span></li>
    <li><b>{len(graph['streets'])}</b><span>Streets with mentions</span></li>
    <li><b>{len(graph['suburbs'])}</b><span>Suburbs</span></li>
    <li><b>{len(active_closures)}</b><span>Active road impacts</span></li>
    <li><b>{len(meetings.get('meetings', []))}</b><span>Council meetings indexed</span></li>
  </ul>

  <h2>Projects by phase of work</h2>
  <ul class="phases">{phase_html}</ul>
</section>

{closure_html}

<section>
  <h2>Explore</h2>
  <p><a href="/suburbs/">All suburbs</a> · <a href="/streets/">All streets with mentions</a> · <a href="/projects/">All projects</a> · <a href="/meetings/">Council meetings</a></p>
</section>
"""
    return render_layout(
        title="Ipswich Council data, joined up",
        description="Live road closures, civic projects, and Council decisions for Ipswich, Queensland — all in one place, searchable, cross-referenced.",
        path="/",
        body=body,
    )


def render_project(p, closures, graph) -> str:
    slug = p["slug"]
    divisions = p.get("divisions") or []
    div_html = ", ".join(
        f'<a href="/division/{d}/">Division {d} — {h(COUNCILLORS.get(d, "?"))}</a>' for d in divisions
    ) or "—"

    extras_html = ""
    if p.get("extras"):
        items = "".join(
            f'<li><a href="{h(e["url"])}" rel="noopener">{h(e.get("title") or e["url"])}</a></li>'
            for e in p["extras"]
        )
        extras_html = f"<h3>Council links</h3><ul>{items}</ul>"

    streets = graph["project_streets"].get(slug, [])
    streets_html = ""
    if streets:
        items = "".join(f'<li><a href="/street/{slugify(s)}/">{h(s)}</a></li>' for s in streets)
        streets_html = f"<h3>Streets mentioned</h3><ul>{items}</ul>"

    what = p.get("what_to_expect")
    what_html = f"<h3>What to expect</h3><p>{h(what)}</p>" if what else ""

    body = f"""
<article class="project">
  <p class="crumbs"><a href="/">Home</a> › <a href="/projects/">Projects</a> › {h(p.get("name"))}</p>
  <h1>{h(p.get("name"))}</h1>
  <p class="meta">
    <span class="{phase_class(p.get('phase'))}">{h(p.get('phase'))}</span>
    · Suburb: <a href="/suburb/{slugify(p.get('suburb') or '')}/">{h(p.get('suburb'))}</a>
    · Ref: <code>{h(p.get('ref'))}</code>
    · Last updated: {h(format_ymd(p.get('updated')))}
  </p>

  <h3>Description</h3>
  <p>{h(p.get("description"))}</p>

  <h3>Status</h3>
  <p>{h(p.get("status"))}</p>

  {what_html}

  <h3>Division</h3>
  <p>{div_html}</p>

  <h3>Managed by</h3>
  <p>{h(p.get("managed_by"))}</p>

  {extras_html}
  {streets_html}

  <p class="attribution">Source: <a href="{h(p.get('source_url'))}">Ipswich City Council Civic Projects Map</a> (CC BY 4.0).</p>

  <div data-ipswichfacts-related data-project="{h(slug)}"></div>
</article>
"""
    return render_layout(
        title=p.get("name") or "Project",
        description=(p.get("description") or "")[:200],
        path=f"/project/{slug}/",
        body=body,
    )


DOC_TYPE_LABELS = {"MIN": "Minutes", "AGN": "Agenda"}


def render_meeting(m, graph) -> str:
    slug = m["slug"]
    doc_label = DOC_TYPE_LABELS.get(m.get("doc_type"), m.get("doc_type"))
    title = f"{m.get('committee')} — {format_ymd(m.get('date'))}"

    sections = []
    for item in m.get("items", []):
        paras = "".join(f"<p>{h(p)}</p>" for p in (item.get("text") or "").split("\n") if p)
        res_html = ""
        if item.get("resolution"):
            res_html = f'<p class="resolution">Resolution: {h(item["resolution"])}</p>'
        mention_links = [
            f'<a href="/street/{slugify(s)}/">{h(s)}</a>' for s in item.get("streets", [])
        ] + [
            f'<a href="/suburb/{slugify(s)}/">{h(s)}</a>' for s in item.get("suburbs", [])
        ]
        mentions_html = (
            f'<p class="muted">Mentions: {" · ".join(mention_links)}</p>' if mention_links else ""
        )
        sections.append(f"""
  <section class="meeting-item" id="{h(item.get('anchor'))}">
    <h2><a href="#{h(item.get('anchor'))}">{h(item.get('title'))}</a></h2>
    {res_html}
    {paras}
    {mentions_html}
    <p class="muted"><a href="{h(m.get('source_url'))}" rel="noopener">View this item in the Council {h(doc_label.lower())}</a></p>
  </section>""")

    body = f"""
<article class="meeting">
  <p class="crumbs"><a href="/">Home</a> › <a href="/meetings/">Meetings</a> › {h(title)}</p>
  <h1>{h(title)}</h1>
  <p class="meta">
    <span class="doc-type doc-type-{h((m.get('doc_type') or '').lower())}">{h(doc_label)}</span>
    · {len(m.get('items', []))} item{"s" if len(m.get('items', [])) != 1 else ""}
  </p>
  {"".join(sections) or '<p>No agenda items in this document — see the Council source for the full paper (some meetings are cancelled or record only procedural resolutions).</p>'}
  <p class="attribution">Source: <a href="{h(m.get('source_url'))}">Ipswich City Council meeting {h(doc_label.lower())}</a> (CC BY 4.0).</p>
  <div data-ipswichfacts-related data-meeting="{h(slug)}"></div>
</article>
"""
    first_item = (m.get("items") or [{}])[0].get("title") or ""
    return render_layout(
        title=title,
        description=f"{doc_label} of the {m.get('committee')} meeting of {format_ymd(m.get('date'))}, Ipswich City Council. {first_item}"[:200],
        path=f"/meeting/{slug}/",
        body=body,
    )


def render_meetings_index(meetings_list) -> str:
    by_committee: dict[str, list] = defaultdict(list)
    for m in meetings_list:
        by_committee[m.get("committee") or "Unknown"].append(m)

    groups = []
    for committee in sorted(by_committee):
        ms = sorted(by_committee[committee], key=lambda x: x.get("date") or "", reverse=True)
        lis = "".join(
            f'<li><a href="/meeting/{x["slug"]}/">{h(format_ymd(x.get("date")))}</a> '
            f'<span class="doc-type doc-type-{h((x.get("doc_type") or "").lower())}">'
            f'{h(DOC_TYPE_LABELS.get(x.get("doc_type"), x.get("doc_type")))}</span> '
            f'<span class="muted">{len(x.get("items", []))} items</span></li>'
            for x in ms
        )
        groups.append(f"<h2>{h(committee)}</h2><ul class='biglist'>{lis}</ul>")

    body = f"""
<p class="crumbs"><a href="/">Home</a> › Meetings</p>
<h1>Council meetings</h1>
<p class="meta">Agendas and minutes republished from Ipswich City Council's business papers, newest first. Minutes shown where published; agendas otherwise.</p>
{"".join(groups)}
<p class="attribution">Source: <a href="https://ipswich.infocouncil.biz/">Ipswich City Council business papers</a> (CC BY 4.0).</p>
"""
    return render_layout(
        title="Council meetings — agendas and minutes",
        description="Every Ipswich City Council meeting agenda and minutes, searchable and cross-referenced by street and suburb.",
        path="/meetings/",
        body=body,
    )


def render_street(name, projects, closures, graph) -> str:
    slug = slugify(name)
    matching_projects = [p for p in projects if name in graph["project_streets"].get(p["slug"], [])]
    matching_closures = []
    for i, c in enumerate(closures.get("closures", [])):
        if name in graph["closure_streets"].get(f"c{i}", []):
            matching_closures.append(c)

    proj_html = ""
    if matching_projects:
        rows = "".join(
            f'<tr><td><a href="/project/{p["slug"]}/">{h(p["name"])}</a></td>'
            f'<td><span class="{phase_class(p.get("phase"))}">{h(p.get("phase"))}</span></td>'
            f'<td>{h(p.get("suburb"))}</td></tr>'
            for p in matching_projects
        )
        proj_html = "<h2>Projects</h2><table class='data'><thead>" \
            "<tr><th>Project</th><th>Phase</th><th>Suburb</th></tr></thead><tbody>" \
            f"{rows}</tbody></table>"

    clos_html = ""
    if matching_closures:
        rows = "".join(
            f'<tr><td>{h(c.get("event_type"))}</td><td>{h(c.get("impact"))}</td>'
            f'<td>{h(c.get("suburb"))}</td><td>{h(c.get("description"))}</td></tr>'
            for c in matching_closures
        )
        clos_html = "<h2>Active road impacts</h2><table class='data'><thead>" \
            "<tr><th>Type</th><th>Impact</th><th>Suburb</th><th>Description</th></tr></thead><tbody>" \
            f"{rows}</tbody></table>"

    meet_html = _meeting_mentions_html(graph["street_meeting_items"].get(name, []))

    empty = "" if (matching_projects or matching_closures or meet_html) else "<p>No projects, road impacts or Council meeting mentions recorded on this street.</p>"

    body = f"""
<article>
  <p class="crumbs"><a href="/">Home</a> › <a href="/streets/">Streets</a> › {h(name)}</p>
  <h1>{h(name)}</h1>
  <p class="meta">Everything Council has published for this street.</p>
  {proj_html}
  {clos_html}
  {meet_html}
  {empty}
  <div data-ipswichfacts-related data-street="{h(name)}"></div>
</article>
"""
    return render_layout(
        title=f"{name} — projects, closures and Council mentions",
        description=f"Every civic project and road impact recorded by Ipswich City Council for {name}.",
        path=f"/street/{slug}/",
        body=body,
    )


def _meeting_mentions_html(refs: list[dict[str, Any]]) -> str:
    """Shared 'Council meeting mentions' section for street/suburb pages."""
    if not refs:
        return ""
    rows = "".join(
        f'<tr><td>{h(r.get("title"))}</td>'
        f'<td><a href="/meeting/{r["slug"]}/#{h(r.get("anchor"))}">'
        f'{h(r.get("committee"))} — {h(format_ymd(r.get("date")))}</a></td></tr>'
        for r in sorted(refs, key=lambda r: r.get("date") or "", reverse=True)
    )
    return (
        f"<h2>Council meeting mentions ({len(refs)})</h2>"
        "<table class='data'><thead><tr><th>Item</th><th>Meeting</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_suburb(name, projects, closures, graph) -> str:
    slug = slugify(name)
    matching_projects = [p for p in projects if p.get("suburb") == name]
    matching_closures = [c for c in closures.get("closures", []) if c.get("suburb") == name]

    proj_html = ""
    if matching_projects:
        rows = "".join(
            f'<tr><td><a href="/project/{p["slug"]}/">{h(p["name"])}</a></td>'
            f'<td><span class="{phase_class(p.get("phase"))}">{h(p.get("phase"))}</span></td>'
            f'<td>{h(p.get("ref"))}</td></tr>'
            for p in matching_projects
        )
        proj_html = f"<h2>Projects ({len(matching_projects)})</h2>" \
            "<table class='data'><thead>" \
            "<tr><th>Project</th><th>Phase</th><th>Ref</th></tr></thead>" \
            f"<tbody>{rows}</tbody></table>"

    clos_html = ""
    if matching_closures:
        rows = "".join(
            f'<tr><td>{h(c.get("road_name"))}</td><td>{h(c.get("event_type"))}</td>'
            f'<td>{h(c.get("impact"))}</td></tr>'
            for c in matching_closures
        )
        clos_html = f"<h2>Active road impacts ({len(matching_closures)})</h2>" \
            "<table class='data'><thead>" \
            "<tr><th>Road</th><th>Type</th><th>Impact</th></tr></thead>" \
            f"<tbody>{rows}</tbody></table>"

    meet_html = _meeting_mentions_html(graph["suburb_meeting_items"].get(name, []))

    body = f"""
<article>
  <p class="crumbs"><a href="/">Home</a> › <a href="/suburbs/">Suburbs</a> › {h(name)}</p>
  <h1>{h(name)}</h1>
  {proj_html or "<p>No projects recorded for this suburb.</p>"}
  {clos_html}
  {meet_html}
  <div data-ipswichfacts-related data-suburb="{h(name)}"></div>
</article>
"""
    return render_layout(
        title=f"{name} — Ipswich Council projects and impacts",
        description=f"All Ipswich City Council projects and road impacts in {name}.",
        path=f"/suburb/{slug}/",
        body=body,
    )


def render_list(title, kind, items) -> str:
    lis = "".join(
        f'<li><a href="/{kind}/{slugify(n)}/">{h(n)}</a></li>' for n in items
    )
    body = f"<h1>{h(title)}</h1><ul class='biglist'>{lis}</ul>"
    return render_layout(title=title, description=title, path=f"/{kind}s/", body=body)


def render_projects_list(projects) -> str:
    lis = "".join(
        f'<li><a href="/project/{p["slug"]}/">{h(p["name"])}</a> '
        f'<a href="/projects/phase/{slugify(p.get("phase") or "Unknown")}/" '
        f'class="{phase_class(p.get("phase"))}">{h(p.get("phase"))}</a></li>'
        for p in sorted(projects, key=lambda p: (p.get("phase") or "", p.get("name") or ""))
    )
    body = f"<h1>All projects</h1><ul class='biglist'>{lis}</ul>"
    return render_layout("All projects", "Every civic project on file.", "/projects/", body)


def render_phase_list(phase: str, projects) -> str:
    slug = slugify(phase)
    matching = sorted(
        (p for p in projects if (p.get("phase") or "Unknown") == phase),
        key=lambda p: p.get("name") or "",
    )
    lis = "".join(
        f'<li><a href="/project/{p["slug"]}/">{h(p["name"])}</a>'
        + (f' <span class="muted">{h(p.get("suburb"))}</span>' if p.get("suburb") else "")
        for p in matching
    )
    body = f"""
<p class="crumbs"><a href="/">Home</a> › <a href="/projects/">Projects</a> › {h(phase)}</p>
<h1><span class="{phase_class(phase)}">{h(phase)}</span> projects</h1>
<p class="meta">{len(matching)} project{"s" if len(matching) != 1 else ""} in this phase of work.</p>
<ul class='biglist'>{lis}</ul>
"""
    return render_layout(
        title=f"{phase} — Ipswich civic projects",
        description=f"All Ipswich City Council civic projects in the '{phase}' phase of work.",
        path=f"/projects/phase/{slug}/",
        body=body,
    )


def render_about() -> str:
    body = """
<h1>About Ipswich Facts</h1>
<p>Ipswich Facts is an unofficial mirror of public data published by Ipswich City Council, joined up so a resident can search by street or project name and find every Council decision, project, closure, and mention in one place.</p>

<h2>Sources</h2>
<ul>
  <li><a href="https://maps.ipswich.qld.gov.au/civicprojects">Civic Projects Map</a> — every planned, in-progress and historic capital project.</li>
  <li><a href="https://traffic.ipswich.qld.gov.au/">Road Closures dashboard</a> — live road impacts, with data from Ipswich City Council and QLDTraffic.</li>
  <li><a href="https://ipswich.infocouncil.biz/">Council business papers</a> — meeting agendas and minutes, item by item.</li>
</ul>

<p>More sources — Capital Works Programs, Ipswich First media releases — will be added.</p>

<h2>Licence</h2>
<p>Council content is published under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. This site preserves attribution and links back to the Council source for every item reproduced.</p>

<h2>Bugs, gaps, suggestions</h2>
<p>Open an issue on the project's GitHub repository.</p>

<h2>Support the project</h2>
<p>Ipswich Facts is run by one person in their spare time. Hosting is free and there are no ads — if the site's saved you a phone call or a trip through five Council tabs, a coffee keeps the lights on and the caffeine flowing.</p>
<p><a class="coffee-btn" href="{COFFEE_URL_PLACEHOLDER}" rel="noopener">☕ {COFFEE_LABEL_PLACEHOLDER}</a></p>
"""
    if COFFEE_URL:
        body = body.replace("{COFFEE_URL_PLACEHOLDER}", h(COFFEE_URL))
        body = body.replace("{COFFEE_LABEL_PLACEHOLDER}", h(COFFEE_LABEL))
    else:
        # No tip jar configured — strip that section.
        body = body.split("<h2>Support the project</h2>")[0]
    return render_layout("About", "About Ipswich Facts.", "/about/", body)


# ---------------------------------------------------------------------------
# Write


def write_site(out: Path, projects, closures, meetings, graph) -> list[str]:
    """Write all pages. Returns the list of URL paths for sitemap."""
    # Best-effort clean (overwrites are always fine; deletion may fail on
    # read-only mounts, in which case we just overwrite in place).
    if out.exists():
        for child in out.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except (PermissionError, OSError):
                pass
    else:
        out.mkdir(parents=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "css").mkdir(exist_ok=True)
    (out / "js").mkdir(exist_ok=True)

    urls: list[str] = []

    def write(path: str, body: str) -> None:
        target = out / path.strip("/") / "index.html" if path != "/" else out / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        urls.append(path)

    # Landing
    write("/", render_index(projects, closures, meetings, graph))

    # Projects
    write("/projects/", render_projects_list(projects))
    for phase in sorted({p.get("phase") or "Unknown" for p in projects}):
        write(f"/projects/phase/{slugify(phase)}/", render_phase_list(phase, projects))
    for p in projects:
        write(f"/project/{p['slug']}/", render_project(p, closures, graph))

    # Suburbs
    write("/suburbs/", render_list("All suburbs", "suburb", graph["suburbs"]))
    for s in graph["suburbs"]:
        write(f"/suburb/{slugify(s)}/", render_suburb(s, projects, closures, graph))

    # Streets
    write("/streets/", render_list("All streets with mentions", "street", graph["streets"]))
    for s in graph["streets"]:
        write(f"/street/{slugify(s)}/", render_street(s, projects, closures, graph))

    # Meetings
    meetings_list = meetings.get("meetings", [])
    write("/meetings/", render_meetings_index(meetings_list))
    for m in meetings_list:
        write(f"/meeting/{m['slug']}/", render_meeting(m, graph))

    # About
    write("/about/", render_about())

    # ---- Client-side data files ----
    (out / "data" / "projects.json").write_text(json.dumps(projects))
    (out / "data" / "closures.json").write_text(json.dumps(closures))
    (out / "data" / "streets.json").write_text(json.dumps(graph["streets"]))
    (out / "data" / "suburbs.json").write_text(json.dumps(graph["suburbs"]))
    # Slim per-meeting chunks for the widget — item titles only, never text.
    slim_meetings = [
        {
            "slug": m["slug"],
            "committee": m.get("committee"),
            "date": m.get("date"),
            "items": [
                {"title": i.get("title"), "anchor": i.get("anchor")}
                for i in m.get("items", [])
            ],
        }
        for m in meetings_list
    ]
    (out / "data" / "meetings.json").write_text(json.dumps(slim_meetings))
    mentions = {
        "project_streets": graph["project_streets"],
        "project_suburbs": graph["project_suburbs"],
        "closure_streets": graph["closure_streets"],
        "closure_suburbs": graph["closure_suburbs"],
        "meeting_streets": graph["meeting_streets"],
        "meeting_suburbs": graph["meeting_suburbs"],
    }
    (out / "data" / "mentions.json").write_text(json.dumps(mentions))

    # ---- CSS / JS ----
    (out / "css" / "site.css").write_text(_CSS)
    (out / "js" / "widget.js").write_text(_WIDGET_JS)

    # ---- Sitemap + robots ----
    (out / "sitemap.xml").write_text(_sitemap(urls))
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n"
    )
    return urls


def _sitemap(urls: list[str]) -> str:
    entries = "\n".join(
        f"  <url><loc>{BASE_URL}{u}</loc></url>" for u in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>
"""


# ---------------------------------------------------------------------------
# Static assets

_CSS = """
:root {
  --fg: #1a1a1a;
  --muted: #666;
  --line: #e2e2e2;
  --bg: #fdfdfd;
  --accent: #005238;
  --warn: #b34700;
}
* { box-sizing: border-box; }
body { font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       color: var(--fg); background: var(--bg); margin: 0; }
.site-header { display: flex; gap: 1.5rem; align-items: baseline;
               padding: 1rem 1.5rem; border-bottom: 1px solid var(--line); }
.brand { font-weight: 700; color: var(--accent); text-decoration: none; font-size: 1.15rem; }
.site-header nav a { margin-right: 1rem; color: var(--fg); text-decoration: none; }
.site-header nav a:hover { text-decoration: underline; }
main { max-width: 950px; margin: 0 auto; padding: 1.5rem; }
h1 { font-size: 1.75rem; margin-top: 0; }
h2 { border-bottom: 1px solid var(--line); padding-bottom: 0.25rem; margin-top: 2rem; }
.hero { padding: 2rem 0; }
.hero h1 { font-size: 2rem; }
.crumbs { color: var(--muted); font-size: 0.9rem; }
.meta { color: var(--muted); font-size: 0.9rem; }
.attribution { color: var(--muted); font-size: 0.85rem; border-top: 1px dashed var(--line);
               padding-top: 0.75rem; margin-top: 2rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 1rem; list-style: none; padding: 0; }
.grid li { border: 1px solid var(--line); border-radius: 6px; padding: 1rem; text-align: center; }
.grid b { display: block; font-size: 2rem; color: var(--accent); }
.grid span { display: block; color: var(--muted); font-size: 0.9rem; margin-top: 0.25rem; }
.phases { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 0.5rem; }
.phases li { padding: 0.35rem 0.75rem; border: 1px solid var(--line); border-radius: 999px; }
.phases li a { color: inherit; text-decoration: none; }
.phases li:hover { border-color: var(--accent); }
a[class^="phase-"] { text-decoration: none; }
.muted { color: var(--muted); font-size: 0.9rem; }
.biglist { column-count: 2; column-gap: 2rem; list-style: none; padding: 0; }
.biglist li { break-inside: avoid; padding: 0.25rem 0; }
table.data { width: 100%; border-collapse: collapse; margin: 1rem 0; }
table.data th, table.data td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line); font-size: 0.95rem; }
table.data th { color: var(--muted); font-weight: 600; }
[class^="phase-"] { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 3px; font-size: 0.8rem; }
.phase-under-construction { background: #fff4e5; color: #b34700; }
.phase-current-program { background: #e5f5ff; color: #003f88; }
.phase-whats-being-planned { background: #f0e5ff; color: #4c00b3; }
.phase-completed { background: #e8f6ec; color: #005238; }
.phase-survey-underway { background: #fffbe5; color: #665600; }
.phase-on-hold { background: #f5f5f5; color: #444; }
.phase-historic { background: #efefef; color: #666; }
.doc-type { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 3px; font-size: 0.8rem; }
.doc-type-min { background: #e8f6ec; color: #005238; }
.doc-type-agn { background: #e5f5ff; color: #003f88; }
.meeting-item { border-top: 1px solid var(--line); margin-top: 1.5rem; }
.meeting-item h2 { border-bottom: none; font-size: 1.15rem; }
.meeting-item h2 a { color: inherit; text-decoration: none; }
.resolution { background: #e8f6ec; border-left: 4px solid var(--accent);
  padding: 0.5rem 0.75rem; }
.site-footer { max-width: 950px; margin: 3rem auto 2rem; padding: 1.5rem;
               font-size: 0.85rem; color: var(--muted); border-top: 1px solid var(--line); }
.site-footer a { color: var(--accent); }
.site-footer .coffee { margin-top: 0.6rem; font-size: 0.9rem; }
.coffee-btn { display: inline-block; padding: 0.5rem 1rem; background: #ffdd00;
  color: #1a1a1a; border-radius: 6px; text-decoration: none;
  font-weight: 600; border: 1px solid #d4b800; }
.coffee-btn:hover { background: #ffe533; }
[data-ipswichfacts-search] input { width: 100%; padding: 0.75rem 1rem;
  font-size: 1.1rem; border: 2px solid var(--line); border-radius: 6px; }
[data-ipswichfacts-search] input:focus { outline: none; border-color: var(--accent); }
[data-ipswichfacts-search] .results { border: 1px solid var(--line); border-top: none;
  border-radius: 0 0 6px 6px; max-height: 60vh; overflow: auto; }
[data-ipswichfacts-search] .results a { display: block; padding: 0.6rem 1rem;
  border-bottom: 1px solid var(--line); text-decoration: none; color: var(--fg); }
[data-ipswichfacts-search] .results a:hover { background: #f5f5f5; }
[data-ipswichfacts-search] .results .kind { color: var(--muted); font-size: 0.8rem;
  text-transform: uppercase; letter-spacing: 0.03em; }
[data-ipswichfacts-related] { margin-top: 2rem; padding: 1rem; background: #f8f8f8;
  border-radius: 6px; }
[data-ipswichfacts-related] h3 { margin: 0 0 0.5rem; }
[data-ipswichfacts-related] ul { margin: 0; padding-left: 1.5rem; }
"""

_WIDGET_JS = r"""
// Ipswich Facts search + related-items widget. No framework, no build step.
const DATA_BASE = '/data';

async function loadData() {
  const [projects, streets, suburbs, mentions, closures, meetings] = await Promise.all([
    fetch(`${DATA_BASE}/projects.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/streets.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/suburbs.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/mentions.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/closures.json`).then(r => r.json()).catch(() => ({ closures: [] })),
    fetch(`${DATA_BASE}/meetings.json`).then(r => r.json()).catch(() => []),
  ]);
  return { projects, streets, suburbs, mentions, closures, meetings };
}

function slugify(s) {
  return s.toLowerCase().replace(/[^\w\s-]/g, '').replace(/[\s_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
}

function buildIndex(data) {
  const items = [];
  for (const p of data.projects) {
    items.push({ kind: 'project', label: p.name, href: `/project/${p.slug}/`, hay: (p.name + ' ' + (p.description || '') + ' ' + (p.suburb || '')).toLowerCase() });
  }
  for (const s of data.streets) items.push({ kind: 'street', label: s, href: `/street/${slugify(s)}/`, hay: s.toLowerCase() });
  for (const s of data.suburbs) items.push({ kind: 'suburb', label: s, href: `/suburb/${slugify(s)}/`, hay: s.toLowerCase() });
  for (const m of data.meetings) {
    const titles = m.items.map(i => i.title).join(' ');
    items.push({ kind: 'meeting', label: `${m.committee} — ${m.date}`, href: `/meeting/${m.slug}/`, hay: (m.committee + ' ' + m.date + ' ' + titles).toLowerCase() });
  }
  return items;
}

function mountSearch(el, index) {
  el.innerHTML = `
    <input type="search" placeholder="Type a street, suburb, project name…" autocomplete="off" autofocus>
    <div class="results" hidden></div>
  `;
  const input = el.querySelector('input');
  const results = el.querySelector('.results');

  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    if (!q) { results.hidden = true; results.innerHTML = ''; return; }
    const matches = index
      .filter(i => i.hay.includes(q))
      .sort((a, b) => a.hay.indexOf(q) - b.hay.indexOf(q))
      .slice(0, 30);
    results.hidden = matches.length === 0;
    results.innerHTML = matches
      .map(m => `<a href="${m.href}"><span class="kind">${m.kind}</span> ${m.label}</a>`)
      .join('');
  });
}

function mountRelated(el, data) {
  const project = el.dataset.project;
  const street  = el.dataset.street;
  const suburb  = el.dataset.suburb;
  const meeting = el.dataset.meeting;

  const meetingLabel = m => `${m.committee} — ${m.date}`;

  let items = [];
  if (project) {
    const streets = data.mentions.project_streets[project] || [];
    for (const s of streets) items.push({ kind: 'street', label: s, href: `/street/${slugify(s)}/` });
    const p = data.projects.find(x => x.slug === project);
    if (p && p.suburb) items.push({ kind: 'suburb', label: p.suburb, href: `/suburb/${slugify(p.suburb)}/` });
  }
  if (street || suburb) {
    for (const p of data.projects) {
      const streets = data.mentions.project_streets[p.slug] || [];
      if ((street && streets.includes(street)) || (suburb && p.suburb === suburb)) {
        items.push({ kind: 'project', label: p.name, href: `/project/${p.slug}/` });
      }
    }
    for (const m of data.meetings) {
      const mStreets = data.mentions.meeting_streets[m.slug] || [];
      const mSuburbs = data.mentions.meeting_suburbs[m.slug] || [];
      if ((street && mStreets.includes(street)) || (suburb && mSuburbs.includes(suburb))) {
        items.push({ kind: 'meeting', label: meetingLabel(m), href: `/meeting/${m.slug}/` });
      }
    }
  }
  if (meeting) {
    for (const s of data.mentions.meeting_streets[meeting] || []) {
      items.push({ kind: 'street', label: s, href: `/street/${slugify(s)}/` });
    }
    for (const s of data.mentions.meeting_suburbs[meeting] || []) {
      items.push({ kind: 'suburb', label: s, href: `/suburb/${slugify(s)}/` });
    }
  }

  if (!items.length) { el.remove(); return; }
  el.innerHTML = '<h3>Related</h3><ul>' +
    items.slice(0, 20).map(i => `<li><span class="kind">${i.kind}</span> <a href="${i.href}">${i.label}</a></li>`).join('') +
    '</ul>';
}

(async () => {
  const searchEls = document.querySelectorAll('[data-ipswichfacts-search]');
  const relatedEls = document.querySelectorAll('[data-ipswichfacts-related]');
  if (!searchEls.length && !relatedEls.length) return;

  const data = await loadData();
  const index = buildIndex(data);
  searchEls.forEach(el => mountSearch(el, index));
  relatedEls.forEach(el => mountRelated(el, data));
})();
"""


# ---------------------------------------------------------------------------
# Entry point


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("site"))
    args = parser.parse_args()

    projects, closures, meetings = load(args.data)
    print(
        f"Loaded {len(projects)} projects, {len(closures.get('closures', []))} closures, "
        f"{len(meetings.get('meetings', []))} meetings",
        file=sys.stderr,
    )

    graph = build_graph(projects, closures, meetings)
    print(f"Extracted {len(graph['streets'])} streets, {len(graph['suburbs'])} suburbs", file=sys.stderr)

    urls = write_site(args.out, projects, closures, meetings, graph)
    print(f"Wrote {len(urls)} pages to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
