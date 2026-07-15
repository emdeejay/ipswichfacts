"""
Scrape Ipswich City Council's Civic Projects dataset.

Endpoint (discovered by inspecting maps.ipswich.qld.gov.au/civicprojects):
    https://maps.ipswich.qld.gov.au/icc/data/Projects_Infrastructure.JSON

Format: GeoJSON FeatureCollection, EPSG:4326, one Point per project.
Refreshes daily. ~385 features, ~800 KB uncompressed.

Fields (per feature.properties):
    ID, SUBURB, DIVISION 1..4 ("T" if in division), PROJECT_NAME,
    COUNCIL_REFERENCE, MAJOR_PROJECT ("Yes"|"No"), PROJECT_DESCRIPTION,
    PROJECT_STATUS, WHAT_TO_EXPECT, MANAGED_BY, PHASE_OF_WORK
    ("What's Being Planned"|"Current Program"|"Under Construction"
     |"Survey Underway"|"Completed"|"On Hold"|"Historic"),
    EXTRA_INFORMATION_{1..10}_OBJ (URL), EXTRA_INFORMATION_{1..10}_TITLE,
    DATE_PUBLISHED (YYYYMMDD), DATE_UPDATED (YYYYMMDD)

Usage:
    python -m scrape.civic_projects [--out data/projects.json]

Attribution: Ipswich City Council, CC BY 4.0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

SOURCE_URL = "https://maps.ipswich.qld.gov.au/icc/data/Projects_Infrastructure.JSON"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"


def normalise_feature(feature: dict[str, Any]) -> dict[str, Any]:
    """Turn one raw GeoJSON feature into a flatter, cleaner record."""
    p = feature.get("properties", {}) or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None

    # DIVISION 1..4 come through as "T" or null; collapse to a list of ints.
    divisions = [i for i in range(1, 5) if p.get(f"DIVISION {i}") == "T"]

    # EXTRA_INFORMATION comes in numbered pairs (_OBJ = URL, _TITLE = label).
    extras = []
    for i in range(1, 11):
        url = p.get(f"EXTRA_INFORMATION_{i}_OBJ")
        title = p.get(f"EXTRA_INFORMATION_{i}_TITLE")
        if url:
            extras.append({"url": url, "title": title})

    updated = _yyyymmdd(p.get("DATE_UPDATED"))
    published = _yyyymmdd(p.get("DATE_PUBLISHED"))

    return {
        "id": p.get("ID"),
        "ref": p.get("COUNCIL_REFERENCE"),
        "name": p.get("PROJECT_NAME"),
        "description": p.get("PROJECT_DESCRIPTION"),
        "status": p.get("PROJECT_STATUS"),
        "what_to_expect": p.get("WHAT_TO_EXPECT"),
        "phase": p.get("PHASE_OF_WORK"),
        "major": p.get("MAJOR_PROJECT") == "Yes",
        "managed_by": p.get("MANAGED_BY"),
        "suburb": p.get("SUBURB"),
        "divisions": divisions,
        "coords": coords,
        "extras": extras,
        "published": published,
        "updated": updated,
        "slug": _slugify(p.get("PROJECT_NAME") or f"project-{p.get('ID')}"),
        "source_url": "https://maps.ipswich.qld.gov.au/civicprojects",
    }


def _yyyymmdd(s: str | None) -> str | None:
    if not s or len(s) != 8 or not s.isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:80] or "project"


def scrape(url: str = SOURCE_URL) -> list[dict[str, Any]]:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    features = data.get("features", [])
    return [normalise_feature(f) for f in features]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/projects.json"))
    parser.add_argument("--url", default=SOURCE_URL)
    args = parser.parse_args()

    print(f"Fetching {args.url} ...", file=sys.stderr)
    projects = scrape(args.url)
    print(f"Got {len(projects)} projects", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(projects, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
