"""
Turn scraped JSON into a static site.

Reads:
    data/projects.json      (from scrape.civic_projects)
    data/closures.json      (from scrape.road_closures)
    data/meetings.json      (from scrape.council_meetings)
    data/news.json          (from scrape.ipswich_first)
    data/capital_works/capworks-*.json  (from scrape.capital_works, committed)

Writes:
    site/index.html
    site/project/<slug>/index.html
    site/suburb/<slug>/index.html
    site/street/<slug>/index.html
    site/meeting/<slug>/index.html
    site/news/<slug>/index.html
    site/capital-works/index.html
    site/capital-works/<cycle>/index.html
    site/data/projects.json         (client widget data)
    site/data/closures.json
    site/data/meetings.json         (slim: no item text)
    site/data/news.json             (slim: slug/title/date, recent years)
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

# Sanity floors for --strict (used by CI before deploying).
#
# Every scraper parses HTML or PDFs that Council can change without notice.
# An HTTP error raises and fails the workflow, which is safe — but a silent
# change (200 OK, different markup) makes a parser match nothing, and the
# per-item "skip and continue" resilience then yields a complete but EMPTY
# dataset. Publishing that would delete thousands of indexed pages and keep
# the old ones out of Google for weeks.
#
# These are floors, not targets: Council's published record doesn't shrink by
# half overnight, so anything under them means a parser broke, not that the
# data went away. Raise them as the archives grow; keep generous headroom so
# a genuine quiet week never trips the build.
MIN_EXPECTED = {
    "projects": 200,          # ~385 live on the map
    "meetings": 400,          # 598 in the committed archive alone
    "meeting_items": 3000,    # ~4,700 — guards against meetings-with-no-items
    "news": 4000,             # 4,922 posts, 2017-present
    "capital_works_rows": 1000,  # 1,745 across four cycles
    "councillors": 9,         # Mayor + 8; fixed until the 2028 election
    "streets": 300,           # ~1,371 extracted; the joins are the whole point
}
# Deliberately NOT floored: closures. An empty traffic dashboard is a real,
# common state (imsRoad is often empty), not a parser failure.

# Councillor data comes from data/councillors.json (scrape/councillors.py,
# one-off, committed — re-run after each election). Loaded in load().

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


def fmt_fy(fy: str | None) -> str:
    """'2025-2026' -> '2025–2026' (en dash, as Council prints it)."""
    return (fy or "").replace("-", "–")


def fmt_kdollars(k: int | None) -> str:
    """Format an amount given in $'000: 1200 -> $1.2M, 450 -> $450k."""
    if k is None:
        return "—"
    if k >= 1000:
        s = f"{k / 1000:,.1f}".rstrip("0").rstrip(".")
        return f"${s}M"
    return f"${k}k"


# Classify a project's traffic impact from Council's own status wording.
# Tiered so the homepage can flag and order by actual interruption; bare
# "clos"/"lane" matching is deliberately avoided ("park closures" and
# "four-lane standard" are not road impacts).
_IMPACT_TIERS = [
    (3, "Road closure", re.compile(
        r"full road closure|road closures?|closed to (?:all )?traffic|full closure", re.I)),
    (2, "Lane closures / detours", re.compile(
        r"lane closures?|lanes? (?:closed|reduced)|closure of [^.]{0,40}lane|detour", re.I)),
    (1, "Traffic control", re.compile(r"traffic control", re.I)),
]


def classify_traffic_impact(p) -> tuple[int, str | None]:
    """(severity, label) from a project's status + what_to_expect; (0, None)
    when Council's wording describes no traffic interruption."""
    text = " ".join(filter(None, [p.get("status"), p.get("what_to_expect")]))
    for severity, label, rx in _IMPACT_TIERS:
        if rx.search(text):
            return severity, label
    return 0, None


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


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


def load(inp: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    projects = json.loads((inp / "projects.json").read_text())
    closures_path = inp / "closures.json"
    closures = json.loads(closures_path.read_text()) if closures_path.exists() else {"closures": []}
    meetings_path = inp / "meetings.json"
    meetings = json.loads(meetings_path.read_text()) if meetings_path.exists() else {"meetings": []}

    # Merge in the committed historical archive (data/archive/meetings-YYYY.json,
    # one-off backfill — old minutes never change, so these are never re-scraped).
    # The live file wins on id collisions.
    seen_ids = {m.get("id") for m in meetings["meetings"]}
    for arch in sorted((inp / "archive").glob("meetings-*.json")):
        for m in json.loads(arch.read_text()).get("meetings", []):
            if m.get("id") not in seen_ids:
                seen_ids.add(m.get("id"))
                meetings["meetings"].append(m)
    meetings["meetings"].sort(key=lambda m: (m.get("date") or "", m.get("id") or ""), reverse=True)

    _assign_meeting_slugs(meetings.get("meetings", []))

    # News (Ipswich First): same live + committed-archive merge as meetings —
    # posts are immutable once published; the live file wins on id collisions.
    news_path = inp / "news.json"
    news = json.loads(news_path.read_text()) if news_path.exists() else {"posts": []}
    seen_post_ids = {p.get("id") for p in news["posts"]}
    for arch in sorted((inp / "archive").glob("news-*.json")):
        for p in json.loads(arch.read_text()).get("posts", []):
            if p.get("id") not in seen_post_ids:
                seen_post_ids.add(p.get("id"))
                news["posts"].append(p)
    news["posts"].sort(key=lambda p: (p.get("date") or "", p.get("id") or 0), reverse=True)

    _assign_news_slugs(news.get("posts", []))

    # Councillors: one-off committed file, refreshed after elections.
    cr_path = inp / "councillors.json"
    councillors = json.loads(cr_path.read_text()).get("councillors", []) if cr_path.exists() else []

    # Capital Works Programs: committed one file per budget cycle
    # (scrape/capital_works.py, re-run each June/July when the new budget
    # drops — budgets don't change during the year). Newest cycle first.
    capworks = [
        json.loads(f.read_text())
        for f in sorted((inp / "capital_works").glob("capworks-*.json"), reverse=True)
    ] if (inp / "capital_works").exists() else []

    return projects, closures, meetings, news, councillors, capworks


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


def _assign_news_slugs(posts: list[dict[str, Any]]) -> None:
    """WordPress slugs are already kebab-case; run them through slugify
    anyway and disambiguate collisions (including with /news/YYYY/ year
    index pages) using the post id. Deterministic because posts are
    processed in (date, id) order."""
    taken: set[str] = set()
    for p in sorted(posts, key=lambda x: (x.get("date") or "", x.get("id") or 0)):
        slug = slugify(p.get("slug") or p.get("title") or f"post-{p.get('id')}")
        if slug in taken or re.fullmatch(r"\d{4}", slug):
            slug = slugify(f"{slug}-{p.get('id')}")
        taken.add(slug)
        p["slug"] = slug


# ---------------------------------------------------------------------------
# Extract streets from project descriptions


STREET_TYPES = (
    "Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Parade|Pde|Terrace|Tce|"
    "Highway|Hwy|Lane|Ln|Boulevard|Blvd|Court|Ct|Crescent|Cres|Close|Cl|"
    "Place|Pl|Way|Circuit|Cct|Bikeway|Motorway|Mwy|Trail"
)
# Name words must not themselves be street types (stops "Cobalt Street And
# Johnson Road" matching as one street) and matches must not cross line
# breaks (paragraph boundaries in meeting text are \n).
_NAME_WORD = r"(?!(?:" + STREET_TYPES + r")\b)[A-Z][A-Za-z']+"
STREET_RE = re.compile(
    r"\b(" + _NAME_WORD + r"(?:[^\S\n]+" + _NAME_WORD + r"){0,3})[^\S\n]+(" + STREET_TYPES + r")\b"
)

# Leading grammar words that bleed in from sentence context ("...between
# Cobalt Street and..."). NB "New" is NOT here — New Chum Road is real.
_LEADING_STOPWORDS = {
    "The", "A", "An", "And", "Or", "Of", "To", "In", "At", "On",
    "For", "From", "Between", "With", "By", "Via", "Along", "Near",
}

# Things the regex reads as streets that aren't (courts of law, event names).
_NOT_STREETS = {
    "Environment Court", "Planning And Environment Court", "Supreme Court",
    "District Court", "Magistrates Court", "Family Court", "Federal Court",
    "High Court", "Land Court", "Garage Sale Trail",
}


def extract_streets_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    names = set()
    for m in STREET_RE.finditer(text):
        words = m.group(1).split()
        while words and words[0] in _LEADING_STOPWORDS:
            words.pop(0)
        if not words:
            continue
        name = f"{' '.join(words)} {_normalise_type(m.group(2))}"
        if name in _NOT_STREETS:
            continue
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


def build_graph(projects, closures, meetings, news, capworks) -> dict[str, Any]:
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

    # Direct project↔meeting edges: a meeting item that names a project
    # outright (normalised, stage/phase suffix stripped — papers say
    # "Redbank Plains Road Upgrade" for "... – Stage 3"). Sparse (~4% of
    # projects) but high precision, and it's the big projects that match.
    def _norm(s):
        return re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()

    def _name_core(name):
        n = re.sub(r"\b(stage|phase)\s*\d+[a-z]?\b", "", _norm(name))
        return re.sub(r" +", " ", n).strip()

    project_cores = {
        p["slug"]: _name_core(p.get("name"))
        for p in projects
        if len(_name_core(p.get("name"))) >= 12
    }
    project_meeting_items: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Meetings: streets via the regex extractor; suburbs matched against the
    # gazetteer of suburb names already known from projects + closures
    # (deliberately no separate suburb list — see docs/notes.md).
    # "Ipswich" is excluded from text matching: in Council prose it almost
    # always means the LGA, not the suburb — matching it hung ~5k news items
    # off the Ipswich suburb page. Projects/closures still set it via their
    # explicit suburb field.
    suburb_ex = Extractor(streets=[], suburbs=sorted(suburbs_set - {"Ipswich"}))
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
                "paper_url": m.get("paper_url"),
                "source_url": m.get("source_url"),
            }
            for s in i_streets:
                street_meeting_items[s].append(ref)
            for s in i_suburbs:
                suburb_meeting_items[s].append(ref)
            n_blob = _norm(blob)
            for pslug, core in project_cores.items():
                if core in n_blob:
                    project_meeting_items[pslug].append(ref)
            item["streets"] = i_streets
            item["suburbs"] = i_suburbs
            m_streets.update(i_streets)
            m_suburbs.update(i_suburbs)
        meeting_streets[m["slug"]] = sorted(m_streets)
        meeting_suburbs[m["slug"]] = sorted(m_suburbs)
        streets_set.update(m_streets)

    # News posts: same street regex + suburb gazetteer over title + text,
    # and the same normalised-name matching for direct project↔news edges.
    news_streets: dict[str, list[str]] = {}
    news_suburbs: dict[str, list[str]] = {}
    street_news_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    suburb_news_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    project_news_items: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for post in news.get("posts", []):
        blob = " . ".join(filter(None, [post.get("title"), post.get("text")]))
        p_streets = extract_streets_from_text(blob)
        p_suburbs = [f["value"] for f in suburb_ex.find(blob) if f["kind"] == "suburb"]
        ref = {
            "slug": post["slug"],
            "date": post.get("date"),
            "title": post.get("title"),
            "url": post.get("url"),
        }
        for s in p_streets:
            street_news_items[s].append(ref)
        for s in p_suburbs:
            suburb_news_items[s].append(ref)
        n_blob = _norm(blob)
        for pslug, core in project_cores.items():
            if core in n_blob:
                project_news_items[pslug].append(ref)
        news_streets[post["slug"]] = p_streets
        news_suburbs[post["slug"]] = p_suburbs
        streets_set.update(p_streets)

    # Capital Works rows ↔ projects: capital works names are close to the
    # Civic Projects map names. Match on the full normalised name first
    # (keeps stages distinct), then fall back to the stage-stripped core
    # where that core identifies exactly one project.
    def _stage_tag(name):
        m = re.search(r"\b(stage|phase)\s*\d+[a-z]?\b", _norm(name))
        return m.group(0) if m else None

    proj_by_norm: dict[str, str] = {}
    proj_stage: dict[str, str | None] = {}
    for p in projects:
        proj_by_norm.setdefault(_norm(p.get("name")), p["slug"])
        proj_stage[p["slug"]] = _stage_tag(p.get("name"))
    core_owners: dict[str, set[str]] = defaultdict(set)
    for p in projects:
        c = _name_core(p.get("name"))
        if len(c) >= 12:
            core_owners[c].add(p["slug"])
    proj_by_core = {c: next(iter(s)) for c, s in core_owners.items() if len(s) == 1}

    project_capworks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    capworks_rows = 0
    capworks_matched = 0
    for cw in capworks:
        for prog in cw.get("programs", []):
            for row in prog.get("rows", []):
                capworks_rows += 1
                n = _norm(row.get("project"))
                c = _name_core(row.get("project"))
                pslug = proj_by_norm.get(n)
                if not pslug and len(c) >= 12:
                    # Stage-stripped fallback — but never bridge two
                    # *different* explicit stages ("… Stage 1" must not
                    # land on the "… Stage 2" project page).
                    cand = proj_by_core.get(c)
                    if cand:
                        rstage = _stage_tag(row.get("project"))
                        pstage = proj_stage.get(cand)
                        if not (rstage and pstage and rstage != pstage):
                            pslug = cand
                row["project_slug"] = pslug
                if pslug:
                    capworks_matched += 1
                    project_capworks[pslug].append(
                        {
                            "cycle": cw.get("cycle"),
                            "fy_columns": cw.get("fy_columns", []),
                            "amounts_published": cw.get("amounts_published"),
                            "source_url": cw.get("source_url"),
                            "section": prog.get("section"),
                            "row": row,
                        }
                    )

    return {
        "project_capworks": dict(project_capworks),
        "capworks_match": (capworks_matched, capworks_rows),
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
        "project_meeting_items": dict(project_meeting_items),
        "news_streets": news_streets,
        "news_suburbs": news_suburbs,
        "street_news_items": dict(street_news_items),
        "suburb_news_items": dict(suburb_news_items),
        "project_news_items": dict(project_news_items),
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
            f'<p class="coffee">This is a volunteer project. If it saved you time — '
            f'<a class="coffee-btn" href="{h(COFFEE_URL)}" rel="noopener">'
            f'☕ {h(COFFEE_LABEL)}</a></p>'
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


def _support_html() -> str:
    if not COFFEE_URL:
        return ""
    return (
        '<section class="support">'
        "<p>Ipswich Facts is one volunteer and zero ads. If it saved you a trip "
        "through five Council tabs, you can keep it running:</p>"
        f'<p><a class="coffee-btn" href="{h(COFFEE_URL)}" rel="noopener">☕ {h(COFFEE_LABEL)}</a></p>'
        "</section>"
    )


def render_index(projects, closures, meetings, news, graph, capworks) -> str:
    by_phase: dict[str, int] = defaultdict(int)
    for p in projects:
        by_phase[p.get("phase") or "Unknown"] += 1

    phase_html = "\n".join(
        f'<li><a href="/projects/phase/{slugify(k)}/">'
        f'<span class="{phase_class(k)}">{h(k)}</span> <b>{v}</b></a></li>'
        for k, v in sorted(by_phase.items(), key=lambda x: -x[1])
    )

    active_closures = [c for c in closures.get("closures", []) if c.get("status") != "Archived"]

    # Stat tile for the newest capital works cycle's three-year grand total.
    capworks_tile = ""
    if capworks:
        cw = capworks[0]
        gt = (cw.get("grand_total") or {}).get("total")
        if gt is not None:
            span = _capworks_span(cw.get("fy_columns", []))
            short_span = f"{span[:4]}–{span[-2:]}"
            capworks_tile = (
                f'<li><a href="/capital-works/"><b>{h(fmt_kdollars(gt))}</b>'
                f"<span>Capital program {h(short_span)}</span></a></li>"
            )

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
        closure_html = "<h2>Active road impacts (live traffic dashboard)</h2><table class='data'>" \
            + "<thead><tr><th>Road</th><th>Suburb</th><th>Type</th><th>Impact</th></tr></thead>" \
            + "<tbody>" + "\n".join(rows) + "</tbody></table>"

    # Construction closures often never make it into the traffic dashboard —
    # e.g. Gordon Street was closed for three months while the dashboard
    # showed nothing. Surface under-construction projects with traffic
    # language from the Civic Projects source alongside the live feed.
    works = []
    for p in projects:
        if p.get("phase") != "Under Construction":
            continue
        severity, label = classify_traffic_impact(p)
        if severity:
            works.append((severity, label, p))
    works_html = ""
    if works:
        works.sort(key=lambda w: (-w[0], w[2].get("name") or ""))
        rows = "".join(
            f'<tr><td><span class="impact-{sev}">{h(label)}</span></td>'
            f'<td><a href="/project/{p["slug"]}/">{h(p["name"])}</a></td>'
            f'<td>{h(p.get("suburb"))}</td>'
            f'<td>{h(_truncate(p.get("status"), 320))}</td></tr>'
            for sev, label, p in works
        )
        works_html = (
            "<h2>Construction works with traffic impacts</h2>"
            "<p class='meta'>From the Civic Projects map — construction closures "
            "don't always appear in the live dashboard above. Impact wording is "
            "Council's own.</p>"
            "<table class='data'><thead><tr><th>Impact</th><th>Project</th><th>Suburb</th>"
            f"<th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
        )

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
    <li><b>{len(news.get('posts', []))}</b><span>Ipswich First articles</span></li>
    {capworks_tile}
  </ul>

  <h2>Projects by phase of work</h2>
  <ul class="phases">{phase_html}</ul>
</section>

{closure_html}

{works_html}

<section>
  <h2>Explore</h2>
  <p><a href="/suburbs/">All suburbs</a> · <a href="/streets/">All streets with mentions</a> · <a href="/projects/">All projects</a> · <a href="/meetings/">Council meetings</a> · <a href="/news/">Ipswich First news</a> · <a href="/capital-works/">Capital works funding</a> · <a href="/councillors/">Mayor &amp; councillors</a></p>
</section>

{_support_html()}
"""
    return render_layout(
        title="Ipswich Council data, joined up",
        description="Live road closures, civic projects, and Council decisions for Ipswich, Queensland — all in one place, searchable, cross-referenced.",
        path="/",
        body=body,
    )


def _capworks_span(fy_columns: list[str]) -> str:
    """['2025-2026','2026-2027','2027-2028'] -> '2025–2028'."""
    if not fy_columns:
        return ""
    return f"{fy_columns[0][:4]}–{fy_columns[-1][-4:]}"


def _fy_list(fys: list[str]) -> str:
    parts = [fmt_fy(f) for f in fys]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _capworks_funding_html(slug: str, graph) -> str:
    """'Funding (Capital Works Program)' section for a project page: one
    entry per budget cycle in which a capital works row names this project."""
    refs = graph.get("project_capworks", {}).get(slug, [])
    if not refs:
        return ""
    blocks = []
    for ref in refs:  # newest cycle first (load order)
        row = ref["row"]
        span = _capworks_span(ref["fy_columns"])
        link = f'{h(ref["source_url"])}#page={row.get("page")}' if ref.get("source_url") else ""
        src = f'<a href="{link}" rel="noopener">Council&nbsp;source&nbsp;↗</a>' if link else ""
        if row.get("amounts") is not None:
            head = "".join(f"<th>{h(fmt_fy(fy))}</th>" for fy in ref["fy_columns"])
            cells = "".join(
                f"<td>{h(fmt_kdollars(row['amounts'].get(fy)))}</td>" for fy in ref["fy_columns"]
            )
            detail = (
                f"<table class='data'><thead><tr>{head}<th>3 Year Total</th></tr></thead>"
                f"<tbody><tr>{cells}<td><b>{h(fmt_kdollars(row.get('total')))}</b></td></tr></tbody></table>"
            )
        else:
            detail = (
                f"<p>Funded in {h(_fy_list(row.get('funded_years') or []))} — per-project "
                f"amounts not published in the {h(fmt_fy(ref['cycle']))} program.</p>"
            )
        blocks.append(
            f"<h4>Capital Works Program {h(span)} "
            f"<span class='muted'>({h(fmt_fy(ref['cycle']))} budget · "
            f"{h(ref.get('section'))} · as “{h(row.get('project'))}”)</span> {src}</h4>"
            f"{detail}"
        )
    return (
        "<h3>Funding (Capital Works Program)</h3>"
        + "".join(blocks)
        + "<p class='muted'>Amounts are as published in Council's Capital Works Program "
        "PDFs ($'000, multiplied out). See <a href='/capital-works/'>all capital works "
        "programs</a>.</p>"
    )


def render_project(p, closures, graph) -> str:
    slug = p["slug"]
    divisions = p.get("divisions") or []
    by_div = graph.get("councillors_by_division", {})
    div_html = ", ".join(
        f'<a href="/division/{d}/">Division {d}</a>'
        + (f' ({h(" & ".join(c["name"] for c in by_div[d]))})' if by_div.get(d) else "")
        for d in divisions
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

    funding_html = _capworks_funding_html(slug, graph)

    # Direct: meeting items that name this project. Transitive: items that
    # mention this project's streets (capped, deduped against direct).
    direct = graph.get("project_meeting_items", {}).get(slug, [])
    direct_keys = {(r["slug"], r.get("anchor")) for r in direct}
    meetings_html = ""
    if direct:
        rows = "".join(
            f'<tr><td><a href="/meeting/{r["slug"]}/#{h(r.get("anchor"))}">{h(r.get("title"))}</a></td>'
            f'<td><a href="/meeting/{r["slug"]}/#{h(r.get("anchor"))}">'
            f'{h(r.get("committee"))} — {h(format_ymd(r.get("date")))}</a></td>'
            f'<td><a href="{h(_council_item_url(r, r))}" rel="noopener">Council&nbsp;source&nbsp;↗</a></td></tr>'
            for r in sorted(direct, key=lambda r: r.get("date") or "", reverse=True)
        )
        meetings_html = (
            f"<h3>Discussed in Council meetings ({len(direct)})</h3>"
            "<table class='data'><thead><tr><th>Item</th><th>Meeting</th><th>Source</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    # Direct news↔project edges: Ipswich First articles that name this
    # project outright (same normalised-name matching as meetings).
    news_refs = graph.get("project_news_items", {}).get(slug, [])
    news_html = ""
    if news_refs:
        rows = "".join(
            f'<tr><td><a href="/news/{r["slug"]}/">{h(r.get("title"))}</a></td>'
            f'<td><a href="/news/{r["slug"]}/">{h(format_ymd(r.get("date")))}</a></td>'
            f'<td><a href="{h(r.get("url"))}" rel="noopener">Source&nbsp;↗</a></td></tr>'
            for r in sorted(news_refs, key=lambda r: r.get("date") or "", reverse=True)
        )
        news_html = (
            f"<h3>In the news ({len(news_refs)})</h3>"
            "<table class='data'><thead><tr><th>Article</th><th>Date</th><th>Source</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    street_refs = []
    seen = set(direct_keys)
    for s in streets:
        for r in graph.get("street_meeting_items", {}).get(s, []):
            key = (r["slug"], r.get("anchor"))
            if key not in seen:
                seen.add(key)
                street_refs.append((s, r))
    street_meetings_html = ""
    if street_refs:
        street_refs.sort(key=lambda x: x[1].get("date") or "", reverse=True)
        shown = street_refs[:15]
        rows = "".join(
            f'<tr><td>{h(r.get("title"))}</td>'
            f'<td><a href="/street/{slugify(s)}/">{h(s)}</a></td>'
            f'<td><a href="/meeting/{r["slug"]}/#{h(r.get("anchor"))}">'
            f'{h(r.get("committee"))} — {h(format_ymd(r.get("date")))}</a></td></tr>'
            for s, r in shown
        )
        more = (
            f"<p class='muted'>Showing the {len(shown)} most recent of {len(street_refs)} — "
            "see the street pages above for the rest.</p>"
            if len(street_refs) > len(shown) else ""
        )
        street_meetings_html = (
            f"<h3>Meeting items mentioning this project's streets</h3>"
            "<table class='data'><thead><tr><th>Item</th><th>Street</th><th>Meeting</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>{more}"
        )

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

  <div class="cols">
    <div>
      <h3>Description</h3>
      <p>{h(p.get("description"))}</p>

      <h3>Status</h3>
      <p>{h(p.get("status"))}</p>

      {what_html}

      {funding_html}
    </div>
    <aside class="panel">
      <h3>Division</h3>
      <p>{div_html}</p>

      <h3>Managed by</h3>
      <p>{h(p.get("managed_by"))}</p>

      {extras_html}
      {streets_html}
    </aside>
  </div>
  {meetings_html}
  {news_html}
  {street_meetings_html}

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


def _council_item_url(m, item_or_ref) -> str:
    """Deep link into Council's own paper document at this item's anchor.
    Falls back to the frameset page for records scraped before paper_url
    existed."""
    paper = m.get("paper_url") if isinstance(m, dict) else None
    anchor = item_or_ref.get("anchor")
    if paper and anchor:
        return f"{paper}#{anchor}"
    return m.get("source_url") or paper or ""


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
    <p class="muted"><a href="{h(_council_item_url(m, item))}" rel="noopener">View this item in the Council {h(doc_label.lower())}</a></p>
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


def render_news_post(p, graph) -> str:
    slug = p["slug"]
    paras = "".join(f"<p>{h(t)}</p>" for t in (p.get("text") or "").split("\n") if t)
    cats = " · ".join(h(c) for c in p.get("categories") or [])
    cats_html = f" · {cats}" if cats else ""

    mention_links = [
        f'<a href="/street/{slugify(s)}/">{h(s)}</a>'
        for s in graph.get("news_streets", {}).get(slug, [])
    ] + [
        f'<a href="/suburb/{slugify(s)}/">{h(s)}</a>'
        for s in graph.get("news_suburbs", {}).get(slug, [])
    ]
    mentions_html = (
        f'<p class="muted">Mentions: {" · ".join(mention_links)}</p>' if mention_links else ""
    )

    body = f"""
<article class="news-post">
  <p class="crumbs"><a href="/">Home</a> › <a href="/news/">News</a> › {h(p.get("title"))}</p>
  <h1>{h(p.get("title"))}</h1>
  <p class="meta">{h(format_ymd(p.get("date")))}{cats_html}</p>
  {paras}
  {mentions_html}
  <p class="attribution">Source: <a href="{h(p.get('url'))}" rel="noopener">Ipswich First (Ipswich City Council)</a> — CC BY 4.0.</p>
  <div data-ipswichfacts-related data-news="{h(slug)}"></div>
</article>
"""
    # WP excerpts here are truncated to a few words, so draw the meta
    # description from the article text itself.
    description = " ".join((p.get("text") or p.get("excerpt") or "").split())[:200]
    return render_layout(
        title=p.get("title") or "News",
        description=description,
        path=f"/news/{slug}/",
        body=body,
    )


def _news_post_li(p) -> str:
    return (
        f'<li><a href="/news/{p["slug"]}/">{h(p.get("title"))}</a> '
        f'<span class="muted">{h(format_ymd(p.get("date")))}</span></li>'
    )


def render_news_index(posts, recent_years, older_years) -> str:
    recent = [p for p in posts if (p.get("date") or "")[:4] in recent_years]
    lis = "".join(_news_post_li(p) for p in recent)
    older_html = ""
    if older_years:
        links = " · ".join(
            f'<a href="/news/{y}/">{h(y)}</a>' for y in sorted(older_years, reverse=True)
        )
        older_html = f"<h2>Older articles</h2><p>By year: {links}</p>"

    body = f"""
<p class="crumbs"><a href="/">Home</a> › News</p>
<h1>Ipswich First news</h1>
<p class="meta">Media releases republished from Ipswich First, Ipswich City Council's news site, newest first.</p>
<ul class='biglist'>{lis}</ul>
{older_html}
<p class="attribution">Source: <a href="https://www.ipswichfirst.com.au/">Ipswich First (Ipswich City Council)</a> — CC BY 4.0.</p>
"""
    return render_layout(
        title="Ipswich First news — Council media releases",
        description="Every Ipswich First media release from Ipswich City Council, searchable and cross-referenced by street, suburb and project.",
        path="/news/",
        body=body,
    )


def render_news_year(year: str, posts) -> str:
    matching = [p for p in posts if (p.get("date") or "")[:4] == year]
    lis = "".join(_news_post_li(p) for p in matching)
    body = f"""
<p class="crumbs"><a href="/">Home</a> › <a href="/news/">News</a> › {h(year)}</p>
<h1>Ipswich First news — {h(year)}</h1>
<p class="meta">{len(matching)} article{"s" if len(matching) != 1 else ""} published in {h(year)}.</p>
<ul class='biglist'>{lis}</ul>
<p class="attribution">Source: <a href="https://www.ipswichfirst.com.au/">Ipswich First (Ipswich City Council)</a> — CC BY 4.0.</p>
"""
    return render_layout(
        title=f"Ipswich First news from {year} — Council media releases",
        description=f"All {len(matching)} Ipswich City Council media releases published on Ipswich First in {year}.",
        path=f"/news/{year}/",
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
    news_html = _news_mentions_html(graph.get("street_news_items", {}).get(name, []))

    empty = "" if (matching_projects or matching_closures or meet_html or news_html) else "<p>No projects, road impacts, Council meeting or news mentions recorded on this street.</p>"

    body = f"""
<article>
  <p class="crumbs"><a href="/">Home</a> › <a href="/streets/">Streets</a> › {h(name)}</p>
  <h1>{h(name)}</h1>
  <p class="meta">Everything Council has published for this street.</p>
  {proj_html}
  {clos_html}
  {meet_html}
  {news_html}
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


# Busy entities (major roads, big suburbs) can accumulate hundreds of
# mentions; cap the static tables so no page balloons past ~100 KB.
_MENTIONS_CAP = 50


def _meeting_mentions_html(refs: list[dict[str, Any]]) -> str:
    """Shared 'Council meeting mentions' section for street/suburb pages."""
    if not refs:
        return ""
    newest = sorted(refs, key=lambda r: r.get("date") or "", reverse=True)
    shown = newest[:_MENTIONS_CAP]
    rows = "".join(
        f'<tr><td>{h(r.get("title"))}</td>'
        f'<td><a href="/meeting/{r["slug"]}/#{h(r.get("anchor"))}">'
        f'{h(r.get("committee"))} — {h(format_ymd(r.get("date")))}</a></td></tr>'
        for r in shown
    )
    more = (
        f"<p class='muted'>Showing the {len(shown)} most recent of {len(refs)} mentions.</p>"
        if len(refs) > len(shown) else ""
    )
    return (
        f"<h2>Council meeting mentions ({len(refs)})</h2>"
        "<table class='data'><thead><tr><th>Item</th><th>Meeting</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{more}"
    )


def _news_mentions_html(refs: list[dict[str, Any]]) -> str:
    """Shared 'News mentions' section for street/suburb pages — same shape
    as _meeting_mentions_html."""
    if not refs:
        return ""
    newest = sorted(refs, key=lambda r: r.get("date") or "", reverse=True)
    shown = newest[:_MENTIONS_CAP]
    rows = "".join(
        f'<tr><td><a href="/news/{r["slug"]}/">{h(r.get("title"))}</a></td>'
        f'<td><a href="/news/{r["slug"]}/">{h(format_ymd(r.get("date")))}</a></td></tr>'
        for r in shown
    )
    more = (
        f"<p class='muted'>Showing the {len(shown)} most recent of {len(refs)} mentions.</p>"
        if len(refs) > len(shown) else ""
    )
    return (
        f"<h2>News mentions ({len(refs)})</h2>"
        "<table class='data'><thead><tr><th>Article</th><th>Date</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{more}"
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
    news_html = _news_mentions_html(graph.get("suburb_news_items", {}).get(name, []))

    body = f"""
<article>
  <p class="crumbs"><a href="/">Home</a> › <a href="/suburbs/">Suburbs</a> › {h(name)}</p>
  <h1>{h(name)}</h1>
  {proj_html or "<p>No projects recorded for this suburb.</p>"}
  {clos_html}
  {meet_html}
  {news_html}
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


def _cw_key(s: str | None) -> str:
    return re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _cw_totals_cells(totals, fy_columns) -> str:
    if not totals:
        return "<td>—</td>" * (len(fy_columns) + 1)
    cells = "".join(f"<td>{h(fmt_kdollars(totals.get(fy)))}</td>" for fy in fy_columns)
    return cells + f"<td><b>{h(fmt_kdollars(totals.get('total')))}</b></td>"


def render_capworks_index(capworks) -> str:
    blocks = []
    for cw in capworks:
        fy_cols = cw.get("fy_columns", [])
        span = _capworks_span(fy_cols)
        gt = cw.get("grand_total") or {}
        note = ""
        if not cw.get("amounts_published"):
            note = (
                "<p class='meta'>This program marks which years each project is funded "
                "(●) but does not publish per-project dollar amounts — Council publishes "
                "dollar figures at program level only in this cycle.</p>"
            )
        head = "".join(f"<th>{h(fmt_fy(fy))}</th>" for fy in fy_cols)
        rows = "".join(
            f'<tr><td><a href="/capital-works/{h(cw.get("cycle"))}/#{slugify(p.get("section") or "section")}">'
            f'{h(p.get("section"))}</a> <span class="muted">{h((p.get("area") or "").title())}</span></td>'
            f"{_cw_totals_cells(p.get('totals'), fy_cols)}</tr>"
            for p in cw.get("programs", [])
        )
        blocks.append(f"""
<h2>Capital Works Program {h(span)} <span class="muted">({h(fmt_fy(cw.get('cycle')))} budget)</span></h2>
<p>Grand total <b>{h(fmt_kdollars(gt.get('total')))}</b> over three years
({" · ".join(f"{h(fmt_fy(fy))} {h(fmt_kdollars(gt.get(fy)))}" for fy in fy_cols)}).
<a href="/capital-works/{h(cw.get('cycle'))}/">All {sum(len(p.get('rows', [])) for p in cw.get('programs', []))} projects</a>
· <a href="{h(cw.get('source_url'))}" rel="noopener">Council source (PDF) ↗</a></p>
{note}
<table class='data'><thead><tr><th>Program</th>{head}<th>3 Year Total</th></tr></thead>
<tbody>{rows}</tbody></table>
""")

    body = f"""
<p class="crumbs"><a href="/">Home</a> › Capital works</p>
<h1>Capital Works Programs</h1>
<p class="meta">Council adopts a rolling three-year Capital Works Program with each
annual budget. Reproduced from Council's own PDFs; source amounts are in $'000,
shown here multiplied out. Newest program first — the same project can appear in
several cycles as funding moves between years.</p>
{"".join(blocks)}
<p class="attribution">Source: Ipswich City Council Capital Works Program PDFs
(<a href="https://www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications">Corporate Publications — Budget</a>), CC BY 4.0.</p>
"""
    return render_layout(
        title="Capital Works Programs — where the money goes",
        description="Ipswich City Council's three-year Capital Works Programs, cycle by cycle: every program's funding by financial year, with per-project detail.",
        path="/capital-works/",
        body=body,
    )


def render_capworks_cycle(cw, graph) -> str:
    cycle = cw.get("cycle")
    fy_cols = cw.get("fy_columns", [])
    span = _capworks_span(fy_cols)
    amounts = cw.get("amounts_published")
    area_totals = {_cw_key(a.get("area")): a.get("totals") for a in cw.get("area_totals") or []}

    note = ""
    if not amounts:
        note = (
            "<p class='meta'>In this cycle Council marks which years each project is "
            "funded (●) without publishing per-project dollar amounts; dollar figures "
            "appear at program level only. Reproduced as published.</p>"
        )

    head = "".join(f"<th>{h(fmt_fy(fy))}</th>" for fy in fy_cols)
    sections = []
    prev_area = object()
    for prog in cw.get("programs", []):
        area = prog.get("area")
        if area != prev_area:
            at = area_totals.get(_cw_key(area))
            at_html = (
                f' <span class="muted">— total {h(fmt_kdollars(at.get("total")))}</span>'
                if at else ""
            )
            sections.append(f"<h2>{h((area or '').title())}{at_html}</h2>")
            prev_area = area
        rows_html = []
        for row in prog.get("rows", []):
            pslug = row.get("project_slug")
            name = (
                f'<a href="/project/{h(pslug)}/">{h(row.get("project"))}</a>'
                if pslug else h(row.get("project"))
            )
            if row.get("amounts") is not None:
                cells = "".join(
                    f"<td>{h(fmt_kdollars(row['amounts'].get(fy)))}</td>" for fy in fy_cols
                ) + f"<td><b>{h(fmt_kdollars(row.get('total')))}</b></td>"
            else:
                funded = set(row.get("funded_years") or [])
                cells = "".join(
                    f"<td>{'●' if fy in funded else ''}</td>" for fy in fy_cols
                ) + "<td></td>"
            src = (
                f'<td><a href="{h(cw.get("source_url"))}#page={row.get("page")}" '
                f'rel="noopener">PDF&nbsp;p.{row.get("page")}&nbsp;↗</a></td>'
            )
            rows_html.append(
                f"<tr><td>{name}<br><span class='muted'>{h(row.get('description'))}</span></td>"
                f"{cells}{src}</tr>"
            )
        totals_row = ""
        if prog.get("totals"):
            totals_row = (
                f"<tr><td><b>{h(prog.get('section'))} Total</b></td>"
                f"{_cw_totals_cells(prog.get('totals'), fy_cols)}<td></td></tr>"
            )
        sections.append(f"""
<h3 id="{slugify(prog.get('section') or 'section')}">{h(prog.get('section'))}</h3>
<table class='data'><thead><tr><th>Project</th>{head}<th>3 Year Total</th><th>Source</th></tr></thead>
<tbody>{"".join(rows_html)}{totals_row}</tbody></table>
""")

    gt = cw.get("grand_total") or {}
    gt_html = ""
    if gt:
        gt_html = (
            f"<h2>Grand total</h2><table class='data'><thead><tr>{head}"
            f"<th>3 Year Total</th></tr></thead><tbody><tr>"
            + "".join(f"<td>{h(fmt_kdollars(gt.get(fy)))}</td>" for fy in fy_cols)
            + f"<td><b>{h(fmt_kdollars(gt.get('total')))}</b></td></tr></tbody></table>"
        )

    body = f"""
<p class="crumbs"><a href="/">Home</a> › <a href="/capital-works/">Capital works</a> › {h(span)}</p>
<h1>Capital Works Program {h(span)}</h1>
<p class="meta">Adopted with the {h(fmt_fy(cycle))} budget. Source amounts are in $'000,
shown here multiplied out. <a href="{h(cw.get('source_url'))}" rel="noopener">Council source (PDF) ↗</a></p>
{note}
{"".join(sections)}
{gt_html}
<p class="attribution">Source: <a href="{h(cw.get('source_url'))}">Ipswich City Council Capital Works Program {h(span)}</a> (CC BY 4.0).</p>
"""
    return render_layout(
        title=f"Capital Works Program {span}",
        description=f"Ipswich City Council's Capital Works Program {span}, adopted with the {fmt_fy(cycle)} budget: every project and program, by financial year.",
        path=f"/capital-works/{cycle}/",
        body=body,
    )


def _councillor_card(c) -> str:
    email_html = (
        f'<br><a href="mailto:{h(c["email"])}">{h(c["email"])}</a>' if c.get("email") else ""
    )
    role_html = f' <span class="muted">{h(c["role"])}</span>' if c.get("role") != "Councillor" else ""
    return (
        f'<li><a href="{h(c.get("url"))}" rel="noopener"><b>{h(c.get("name"))}</b></a>'
        f"{role_html}{email_html}</li>"
    )


def render_councillors(councillors) -> str:
    mayor = [c for c in councillors if c.get("division") is None]
    by_div: dict[int, list] = defaultdict(list)
    for c in councillors:
        if c.get("division") is not None:
            by_div[c["division"]].append(c)

    sections = []
    if mayor:
        sections.append("<h2>Mayor</h2><ul class='councillors'>"
                        + "".join(_councillor_card(c) for c in mayor) + "</ul>")
    for d in sorted(by_div):
        sections.append(
            f'<h2><a href="/division/{d}/">Division {d}</a></h2>'
            "<ul class='councillors'>"
            + "".join(_councillor_card(c) for c in by_div[d]) + "</ul>"
        )

    body = f"""
<p class="crumbs"><a href="/">Home</a> › Councillors</p>
<h1>Mayor and Councillors</h1>
<p class="meta">Ipswich elects a Mayor city-wide and two councillors per division. Names and contacts reproduced from Council's own profiles.</p>
{"".join(sections)}
<p class="attribution">Source: <a href="https://www.ipswich.qld.gov.au/About-Council/Mayor-Councillors">Ipswich City Council — Mayor &amp; Councillors</a> (CC BY 4.0).</p>
"""
    return render_layout(
        title="Ipswich Mayor and Councillors",
        description="Who represents you: Ipswich City Council's Mayor and all eight divisional councillors, with contacts and the projects in each division.",
        path="/councillors/",
        body=body,
    )


def render_division(d: int, projects, graph) -> str:
    crs = graph.get("councillors_by_division", {}).get(d, [])
    matching = [p for p in projects if d in (p.get("divisions") or [])]
    proj_html = ""
    if matching:
        rows = "".join(
            f'<tr><td><a href="/project/{p["slug"]}/">{h(p["name"])}</a></td>'
            f'<td><span class="{phase_class(p.get("phase"))}">{h(p.get("phase"))}</span></td>'
            f'<td>{h(p.get("suburb"))}</td></tr>'
            for p in sorted(matching, key=lambda p: (p.get("phase") or "", p.get("name") or ""))
        )
        proj_html = (
            f"<h2>Civic projects in Division {d} ({len(matching)})</h2>"
            "<table class='data'><thead><tr><th>Project</th><th>Phase</th><th>Suburb</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )

    body = f"""
<p class="crumbs"><a href="/">Home</a> › <a href="/councillors/">Councillors</a> › Division {d}</p>
<h1>Division {d}</h1>
<h2>Your councillors</h2>
<ul class='councillors'>{"".join(_councillor_card(c) for c in crs)}</ul>
{proj_html}
<p class="attribution">Councillor details from <a href="https://www.ipswich.qld.gov.au/About-Council/Mayor-Councillors">Ipswich City Council</a>; projects from the Civic Projects Map (CC BY 4.0).</p>
"""
    return render_layout(
        title=f"Ipswich Division {d} — councillors and projects",
        description=f"Division {d} of Ipswich City Council: your two councillors and every civic project in the division.",
        path=f"/division/{d}/",
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
  <li><a href="https://www.ipswichfirst.com.au/">Ipswich First</a> — Council's media releases, back to 2017.</li>
  <li><a href="https://www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications">Capital Works Program PDFs</a> — per-project funding by financial year, one program per budget cycle.</li>
</ul>

<p>More sources — Shape Your Ipswich consultations — will be added.</p>

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


def write_site(out: Path, projects, closures, meetings, news, graph, capworks) -> list[str]:
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
    write("/", render_index(projects, closures, meetings, news, graph, capworks))

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

    # News. The /news/ index lists the two most recent years and links to
    # per-year index pages for the rest.
    news_posts = news.get("posts", [])
    news_years = sorted(
        {(p.get("date") or "")[:4] for p in news_posts if p.get("date")}, reverse=True
    )
    news_recent_years, news_older_years = news_years[:2], news_years[2:]
    write("/news/", render_news_index(news_posts, news_recent_years, news_older_years))
    for y in news_older_years:
        write(f"/news/{y}/", render_news_year(y, news_posts))
    for p in news_posts:
        write(f"/news/{p['slug']}/", render_news_post(p, graph))

    # Capital works: one index page plus one page per budget cycle. The data
    # is committed (refreshed once a year), so these always build.
    if capworks:
        write("/capital-works/", render_capworks_index(capworks))
        for cw in capworks:
            write(f"/capital-works/{cw['cycle']}/", render_capworks_cycle(cw, graph))

    # Councillors + divisions
    councillors = graph.get("councillors", [])
    if councillors:
        write("/councillors/", render_councillors(councillors))
        for d in sorted(graph.get("councillors_by_division", {})):
            write(f"/division/{d}/", render_division(d, projects, graph))

    # About
    write("/about/", render_about())

    # ---- Client-side data files ----
    (out / "data" / "projects.json").write_text(json.dumps(projects))
    (out / "data" / "closures.json").write_text(json.dumps(closures))
    (out / "data" / "streets.json").write_text(json.dumps(graph["streets"]))
    (out / "data" / "suburbs.json").write_text(json.dumps(graph["suburbs"]))
    # Slim per-meeting chunks for the widget — item titles only, never text,
    # and only the two most recent years so the search payload stays small.
    # Older meetings are still fully served as static pages and sitemapped;
    # the widget adds interactivity, not information.
    recent_years = sorted({(m.get("date") or "")[:4] for m in meetings_list}, reverse=True)[:2]
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
        if (m.get("date") or "")[:4] in recent_years
    ]
    (out / "data" / "meetings.json").write_text(json.dumps(slim_meetings))
    # Slim per-post news chunks, same recent-years rule as meetings: slug,
    # title and date only — full text is only ever in the static pages.
    slim_news = [
        {"slug": p["slug"], "title": p.get("title"), "date": p.get("date")}
        for p in news_posts
        if (p.get("date") or "")[:4] in news_recent_years
    ]
    (out / "data" / "news.json").write_text(json.dumps(slim_news))
    slim_news_slugs = {p["slug"] for p in slim_news}
    mentions = {
        "project_streets": graph["project_streets"],
        "project_suburbs": graph["project_suburbs"],
        "closure_streets": graph["closure_streets"],
        "closure_suburbs": graph["closure_suburbs"],
        "meeting_streets": graph["meeting_streets"],
        "meeting_suburbs": graph["meeting_suburbs"],
        # Same slug→names pattern as meeting_streets/meeting_suburbs, limited
        # to the posts the widget actually loads (recent years) so the
        # payload stays small — older mentions live in the static pages.
        "news_streets": {
            s: v for s, v in graph["news_streets"].items() if s in slim_news_slugs
        },
        "news_suburbs": {
            s: v for s, v in graph["news_suburbs"].items() if s in slim_news_slugs
        },
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
main { max-width: 1280px; margin: 0 auto; padding: 1.5rem; }
/* Prose stays readable; tables and grids get the full width. */
article > p, section > p, .meeting-item p { max-width: 75ch; }
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
.impact-3 { background: #fde8e8; color: #a51212; padding: 0.15rem 0.55rem; border-radius: 3px; font-size: 0.8rem; white-space: nowrap; }
.impact-2 { background: #fff4e5; color: #b34700; padding: 0.15rem 0.55rem; border-radius: 3px; font-size: 0.8rem; white-space: nowrap; }
.impact-1 { background: #f0f0f0; color: #555; padding: 0.15rem 0.55rem; border-radius: 3px; font-size: 0.8rem; white-space: nowrap; }
.councillors { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 1rem; }
.councillors li { border: 1px solid var(--line); border-radius: 6px; padding: 0.75rem 1rem; min-width: 16rem; }
.biglist { columns: 18rem; column-gap: 2rem; list-style: none; padding: 0; }
.biglist li { break-inside: avoid; padding: 0.25rem 0; }
table.data { width: 100%; border-collapse: collapse; margin: 1rem 0; }
@media (max-width: 720px) {
  table.data { display: block; overflow-x: auto; }
  main { padding: 1rem; }
}
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
.site-footer .coffee { margin-top: 1rem; font-size: 1rem; }
.support { border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.25rem;
           margin: 2rem 0; background: #fffdf2; }
.support p { margin: 0.4rem 0; }
.cols { display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 2.5rem; }
.panel { border: 1px solid var(--line); border-radius: 8px; padding: 0.25rem 1.25rem 1rem;
         background: #fff; align-self: start; }
.panel h3 { font-size: 0.95rem; margin-bottom: 0.35rem; }
.panel ul { margin: 0.25rem 0; padding-left: 1.1rem; }
@media (max-width: 900px) { .cols { grid-template-columns: 1fr; gap: 0; } }
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
  const [projects, streets, suburbs, mentions, closures, meetings, news] = await Promise.all([
    fetch(`${DATA_BASE}/projects.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/streets.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/suburbs.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/mentions.json`).then(r => r.json()),
    fetch(`${DATA_BASE}/closures.json`).then(r => r.json()).catch(() => ({ closures: [] })),
    fetch(`${DATA_BASE}/meetings.json`).then(r => r.json()).catch(() => []),
    fetch(`${DATA_BASE}/news.json`).then(r => r.json()).catch(() => []),
  ]);
  return { projects, streets, suburbs, mentions, closures, meetings, news };
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
  for (const n of data.news) {
    items.push({ kind: 'news', label: `${n.title} — ${n.date}`, href: `/news/${n.slug}/`, hay: (n.title + ' ' + n.date).toLowerCase() });
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
  const news    = el.dataset.news;

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
    for (const n of data.news) {
      const nStreets = data.mentions.news_streets[n.slug] || [];
      const nSuburbs = data.mentions.news_suburbs[n.slug] || [];
      if ((street && nStreets.includes(street)) || (suburb && nSuburbs.includes(suburb))) {
        items.push({ kind: 'news', label: `${n.title} — ${n.date}`, href: `/news/${n.slug}/` });
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
  if (news) {
    for (const s of data.mentions.news_streets[news] || []) {
      items.push({ kind: 'street', label: s, href: `/street/${slugify(s)}/` });
    }
    for (const s of data.mentions.news_suburbs[news] || []) {
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


def check_data_sanity(counts: dict[str, int]) -> list[str]:
    """Return a list of failures where a dataset fell below its floor.
    See MIN_EXPECTED for why this exists."""
    return [
        f"{name}: got {counts[name]}, expected at least {floor}"
        for name, floor in MIN_EXPECTED.items()
        if name in counts and counts[name] < floor
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("site"))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail instead of writing the site if any dataset looks broken "
             "(implausibly small). Used by CI so a silently-empty scrape can "
             "never overwrite a good deploy.",
    )
    args = parser.parse_args()

    projects, closures, meetings, news, councillors, capworks = load(args.data)
    print(
        f"Loaded {len(projects)} projects, {len(closures.get('closures', []))} closures, "
        f"{len(meetings.get('meetings', []))} meetings, {len(news.get('posts', []))} news posts, "
        f"{len(councillors)} councillors, {len(capworks)} capital works cycles",
        file=sys.stderr,
    )

    graph = build_graph(projects, closures, meetings, news, capworks)
    matched, total_rows = graph.get("capworks_match", (0, 0))
    if total_rows:
        print(
            f"Capital works rows joined to projects: {matched}/{total_rows} "
            f"({matched / total_rows:.0%})",
            file=sys.stderr,
        )
    graph["councillors"] = councillors
    by_div: dict[int, list] = defaultdict(list)
    for c in councillors:
        if c.get("division") is not None:
            by_div[c["division"]].append(c)
    graph["councillors_by_division"] = dict(by_div)
    print(f"Extracted {len(graph['streets'])} streets, {len(graph['suburbs'])} suburbs", file=sys.stderr)

    if args.strict:
        counts = {
            "projects": len(projects),
            "meetings": len(meetings.get("meetings", [])),
            "meeting_items": sum(len(m.get("items", [])) for m in meetings.get("meetings", [])),
            "news": len(news.get("posts", [])),
            "capital_works_rows": total_rows,
            "councillors": len(councillors),
            "streets": len(graph["streets"]),
        }
        failures = check_data_sanity(counts)
        if failures:
            print(
                "REFUSING TO BUILD — a data source looks broken, not empty:\n  "
                + "\n  ".join(failures)
                + "\n\nA scraper is probably matching nothing after an upstream change."
                "\nThe live site keeps its last good deploy. Fix the scraper, or"
                "\nadjust MIN_EXPECTED in build/build_site.py if the drop is real.",
                file=sys.stderr,
            )
            return 1

    urls = write_site(args.out, projects, closures, meetings, news, graph, capworks)
    print(f"Wrote {len(urls)} pages to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
