"""
Scrape consultation/engagement projects from Shape Your Ipswich, Ipswich City
Council's community-engagement site (Granicus EngagementHQ / "the Hive").

Enumeration (see docs/notes.md):
    https://www.shapeyouripswich.com.au/projects

The public listing page renders project cards, but the authoritative, complete
list comes from the AJAX endpoint its "Show more" button calls. Each
`<section class="projects-list" data-route=".../load_more/{blockID}">` on the
listing has its own load-more route that returns JSON:

    https://www.shapeyouripswich.com.au/ccm/the_hive_projects/tools/
        the_hive_projects_list/load_more/{blockID}?page=N

    -> { "result": [ {project}, ... ], "moreToLoad": bool }

There are three list blocks — one each for the Open, Active and Closed project
groups — so all three are fetched and de-duplicated by projectID. Closed
consultations are kept deliberately: the buried history is the point.

Each `result` item is already structured JSON with everything we republish:

    projectID (stable int), projectName, projectDescription (Council's own
    one-line summary), projectStatus ("Open"|"Active"|"Closed"), projectPath
    (canonical URL; the last path segment is the stable EngagementHQ slug),
    projectDateNum / projectDateStr, projectLocationArray (gazetted Ipswich
    suburb names — the PRIMARY join key), projectCategoryArray.

Why the listing JSON and NOT a per-project page fetch:
  1. Invariant 8 (no user-generated content). EngagementHQ project pages are
     built around resident surveys, comments, guestbooks, forums and
     contributions. This listing feed is the one place the CMS exposes ONLY
     Council-authored project metadata — it structurally cannot leak UGC, so
     it is both the sufficient AND the safe choice. `normalise_project()`
     whitelists fields; nothing else is ever carried through.
  2. It's not richer to fetch the page anyway: a project page's only clean
     Council-authored text is its `<meta og:description>`, which is byte-for-
     byte the `projectDescription` already in this feed.

Refresh cadence: daily (this is not time-sensitive like road closures — it
belongs in the daily cron, not the hourly closures refresh). Rate limit: 1
request/second; ~4 requests total (listing + three blocks).

Usage:
    python -m scrape.shape_your_ipswich [--out data/consultations.json]

Attribution: Shape Your Ipswich, Ipswich City Council, CC BY 4.0.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

HOST = "https://www.shapeyouripswich.com.au"
LISTING_URL = f"{HOST}/projects"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"
REQUEST_DELAY = 1.0  # seconds between requests — project invariant, keep it.
MAX_PAGES = 60  # guard against a runaway load-more loop.

# Each project-list block on /projects carries the AJAX route its "Show more"
# button calls. Discover them from the page rather than hardcoding the numeric
# block ids, which Council can change.
_ROUTE_RE = re.compile(
    r'data-route="(' + re.escape(HOST) + r'/ccm/the_hive_projects/tools/'
    r'the_hive_projects_list/load_more/\d+)"'
)


def parse_block_routes(listing_html: str) -> list[str]:
    """The load-more route URLs, one per project-list block, de-duplicated in
    document order."""
    seen: set[str] = set()
    routes: list[str] = []
    for m in _ROUTE_RE.finditer(listing_html):
        route = m.group(1)
        if route not in seen:
            seen.add(route)
            routes.append(route)
    return routes


def _slug_from_path(path: str | None) -> str | None:
    if not path:
        return None
    return path.rstrip("/").rsplit("/", 1)[-1] or None


def _clean(s: Any) -> str | None:
    """Council writes plain strings here, but unescape entities and collapse
    whitespace defensively so nothing markup-ish reaches a page."""
    if not s:
        return None
    text = re.sub(r"\s+", " ", htmllib.unescape(str(s))).strip()
    return text or None


def normalise_project(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One raw listing record -> a flat, whitelisted consultation record.

    Whitelist, not passthrough: only these fields are ever emitted, so even if
    Council's feed grows a field carrying resident content, it cannot reach the
    site (invariant 8). No comment, submission, survey-response, forum or
    guestbook content exists in this feed, and none is derived here."""
    pid = raw.get("projectID")
    path = raw.get("projectPath")
    slug = _slug_from_path(path)
    name = _clean(raw.get("projectName"))
    if pid is None or not slug or not name:
        return None  # skip malformed rows rather than dying

    locations = [
        loc for loc in (
            _clean(x) for x in (raw.get("projectLocationArray") or [])
        ) if loc
    ]
    categories = [
        cat for cat in (
            _clean(x) for x in (raw.get("projectCategoryArray") or [])
        ) if cat
    ]
    date_num = raw.get("projectDateNum")
    date = date_num if isinstance(date_num, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", date_num) else None

    return {
        "id": pid,
        "slug": slug,
        "name": name,
        "summary": _clean(raw.get("projectDescription")),
        "status": _clean(raw.get("projectStatus")),  # Open | Active | Closed
        "date": date,
        "date_str": _clean(raw.get("projectDateStr")),
        "suburbs": locations,
        "categories": categories,
        "url": path,
        "source_url": path,
    }


def _get(client: httpx.Client, url: str, params: dict[str, Any] | None,
         delay: float, retries: int = 4) -> httpx.Response:
    for attempt in range(retries):
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            time.sleep(delay)
            return resp
        except Exception:  # noqa: BLE001 — retry with backoff, then re-raise
            if attempt == retries - 1:
                raise
            time.sleep(2 * 2 ** attempt)
    raise RuntimeError("unreachable")


def fetch_block(client: httpx.Client, route: str, delay: float) -> list[dict[str, Any]]:
    """Page through one project-list block until `moreToLoad` is false. The
    front-end starts at page=0 and increments while more remain."""
    out: list[dict[str, Any]] = []
    page = 0
    while page < MAX_PAGES:
        resp = _get(client, route, {"page": page}, delay)
        payload = resp.json()
        result = payload.get("result") or []
        if not result:
            break
        out.extend(result)
        if not payload.get("moreToLoad"):
            break
        page += 1
    return out


def scrape(delay: float = REQUEST_DELAY) -> dict[str, Any]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    by_id: dict[Any, dict[str, Any]] = {}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
    ) as client:
        listing = _get(client, LISTING_URL, None, delay).text
        routes = parse_block_routes(listing)
        if not routes:
            raise RuntimeError(
                "no project-list load-more routes found on /projects — the "
                "listing markup probably changed (see docs/notes.md)"
            )
        for route in routes:
            try:
                raws = fetch_block(client, route, delay)
            except Exception as e:  # noqa: BLE001 — skip one block, don't die
                print(f"skip block {route}: {e}", file=sys.stderr)
                continue
            for raw in raws:
                rec = normalise_project(raw)
                if rec is not None:
                    by_id[rec["id"]] = rec  # de-dupe across blocks by projectID

    consultations = sorted(
        by_id.values(),
        key=lambda c: (c.get("date") or "", str(c.get("id"))),
        reverse=True,
    )
    return {"scraped_at": scraped_at, "consultations": consultations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/consultations.json"))
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help="seconds between requests")
    parser.add_argument("--compact", action="store_true",
                        help="write minified JSON")
    args = parser.parse_args()

    print(f"Fetching Shape Your Ipswich consultations (delay={args.delay}s) ...",
          file=sys.stderr)
    snapshot = scrape(delay=args.delay)
    print(f"Got {len(snapshot['consultations'])} consultations", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    indent = None if args.compact else 2
    args.out.write_text(json.dumps(snapshot, indent=indent, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
