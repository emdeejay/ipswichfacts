"""
Scrape live road impact data from traffic.ipswich.qld.gov.au.

The dashboard pulls from two internal endpoints:

  /dashboard/imsRoad      — Council's own incident-management feed (QIT Plus IMS).
                            Often empty; populates during actual events / closures.
  /dashboard/tmrRoadData  — QLDTraffic (Asignit) impacts within Ipswich LGA,
                            proxied through Council. This is the more usually
                            populated feed and matches the closures table on the
                            dashboard.

Both endpoints return a JSON *string* that itself contains JSON (double-encoded).
For tmrRoadData the inner payload is a bare array of GeoJSON Features. For
imsRoad it's a FeatureCollection.

Feature properties on tmrRoadData include:
    id, status, published, source (dict with provided_by, provided_by_url),
    url, event_type, event_subtype, event_due_to, impact, duration,
    event_priority, description, advice, information, road_summary
    (contains road_name, suburb, etc.), last_updated, next_inspection,
    web_link, group_id

Usage:
    python -m scrape.road_closures [--out data/closures.json]

Attribution: Data sourced from Ipswich City Council and QLDTraffic (Asignit).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

IMSROAD_URL = "https://traffic.ipswich.qld.gov.au/dashboard/imsRoad"
TMRROADDATA_URL = "https://traffic.ipswich.qld.gov.au/dashboard/tmrRoadData"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"


def _double_decode(text: str) -> Any:
    """The endpoints return JSON-encoded strings that contain more JSON."""
    outer = json.loads(text)
    return json.loads(outer) if isinstance(outer, str) else outer


def _fetch(client: httpx.Client, url: str) -> Any:
    resp = client.get(url)
    resp.raise_for_status()
    return _double_decode(resp.text)


def _flatten_impact(impact: Any) -> str | None:
    """Upstream `impact` is a plain string on some events and a dict like
    {direction, towards, impact_type, impact_subtype, delay} on others.
    Flatten the dict form to a readable sentence so nothing downstream
    ever renders a JSON blob."""
    if impact is None or isinstance(impact, str):
        return impact
    if isinstance(impact, dict):
        what = impact.get("impact_subtype") or impact.get("impact_type")
        where = " ".join(
            filter(None, [impact.get("direction"),
                          f"towards {impact['towards']}" if impact.get("towards") else None])
        )
        if what and where:
            what = f"{what} ({where})"
        parts = [p for p in (what or where or None, impact.get("delay")) if p]
        if parts:
            return " — ".join(parts)
    return json.dumps(impact, ensure_ascii=False)


def _normalise_tmr(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature.get("properties", {}) or {}
    road_summary = p.get("road_summary") or {}
    source = p.get("source") or {}
    duration = p.get("duration") or {}
    geom = feature.get("geometry") or {}
    coords = None
    if geom.get("type") == "Point":
        coords = geom.get("coordinates")
    elif geom.get("type") == "GeometryCollection":
        geoms = geom.get("geometries") or []
        first = next((g for g in geoms if g.get("type") == "Point"), None)
        if first:
            coords = first.get("coordinates")

    return {
        "id": p.get("id"),
        "source": "tmrRoadData",
        "provided_by": source.get("provided_by"),
        "provided_by_url": source.get("provided_by_url"),
        "event_type": p.get("event_type"),
        "event_subtype": p.get("event_subtype"),
        "event_due_to": p.get("event_due_to"),
        "impact": _flatten_impact(p.get("impact")),
        "priority": p.get("event_priority"),
        "description": p.get("description"),
        "advice": p.get("advice"),
        "information": p.get("information"),
        "road_name": road_summary.get("road_name"),
        "suburb": road_summary.get("suburb"),
        "postcode": road_summary.get("postcode"),
        "local_government_area": road_summary.get("local_government_area"),
        "district": road_summary.get("district"),
        "start_time": duration.get("start"),
        "end_time": duration.get("end"),
        "last_updated": p.get("last_updated"),
        "published": p.get("published"),
        "status": p.get("status"),
        "url": p.get("url"),
        "web_link": p.get("web_link"),
        "coords": coords,
    }


def _normalise_ims(feature: dict[str, Any]) -> dict[str, Any]:
    p = feature.get("properties", {}) or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None
    return {
        "id": p.get("id") or p.get("ID"),
        "source": "imsRoad",
        # IMS field names vary between events; check both casings.
        "description": p.get("description") or p.get("DESCRIPTION"),
        "road_name": p.get("road_name") or p.get("ROAD_NAME"),
        "suburb": p.get("suburb") or p.get("SUBURB"),
        "start_time": p.get("start_time") or p.get("START_TIME"),
        "end_time": p.get("end_time") or p.get("END_TIME"),
        "coords": coords,
    }


def scrape() -> dict[str, Any]:
    snapshot = {"snapshot_at": datetime.now(timezone.utc).isoformat(), "closures": []}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        try:
            tmr = _fetch(client, TMRROADDATA_URL)
            if isinstance(tmr, list):
                for f in tmr:
                    snapshot["closures"].append(_normalise_tmr(f))
        except Exception as e:  # noqa: BLE001
            print(f"tmrRoadData: {e}", file=sys.stderr)
        try:
            ims = _fetch(client, IMSROAD_URL)
            for f in ims.get("features", []):
                snapshot["closures"].append(_normalise_ims(f))
        except Exception as e:  # noqa: BLE001
            print(f"imsRoad: {e}", file=sys.stderr)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/closures.json"))
    args = parser.parse_args()

    print("Fetching live road closures ...", file=sys.stderr)
    snapshot = scrape()
    print(f"Got {len(snapshot['closures'])} active items", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
