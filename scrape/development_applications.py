"""
Scrape development-application (DA) metadata from Ipswich City Council's own
"Development.i" portal.

    https://developmenti.ipswich.qld.gov.au

This is a deliberate, owner-approved evolution of Design Invariant 7. Ipswich
Facts does NOT reproduce development applications in full. What it surfaces is a
LINK + FACTUAL-METADATA layer straight from Council's own register:

    application number, the property's locality (suburb), the lodged/received
    date, the current status, Council's own one-line proposal description, and a
    deep link to Council's own detail page for that application.

Everything else Council's feed carries is DELIBERATELY DROPPED and can never
reach the site, because normalise_feature() WHITELISTS its output rather than
passing raw properties through. In particular these are never stored or shown:

    project_officer      (the assessing officer's name)
    decision_desc        (the outcome narrative / characterisation)
    appeal_result        (appeal outcomes)
    submissionindicator  (public-submission signal)
    publicnotification   (notification signal)

The safe line is: "here's that a DA exists, its basic facts, and a link to
Council; go to Council for the detail." PlanningAlerts stays as the "email me
about DAs near my place" complement; Council's Development.i is the authoritative
factual source this layer surfaces.

Endpoint (see docs/notes.md for the full contract):

    POST /Geo/GetApplicationFilterResults   (Content-Type: application/json)
        Returns a GeoJSON FeatureCollection. IMPORTANT: this is an ASP.NET
        anti-forgery-protected endpoint. You MUST first GET "/" to obtain the
        `.AspNetCore.Antiforgery.*` cookie AND the hidden __RequestVerificationToken,
        then send that token in a `RequestVerificationToken` header (and the
        cookie) on the POST. Without it the endpoint returns 500.

    GET  /Geo/GetLocality    -> FeatureCollection of the ~82 suburb polygons;
                                each feature.id is the gazetted locality name.

Enumeration: the geo feed carries no locality on each feature, so we iterate the
82 localities and set `LocalityId` per request — that both scopes the query and
gives us the suburb bucket for free (no geocoding). A single whole-of-LGA
ViewPort with the LocalityId set returns the same locality-filtered result as no
ViewPort at all (verified), so we send ViewPort:null and just vary LocalityId.

IMPORTANT COMPLETENESS NOTE (see docs/notes.md): this GeoJSON endpoint is the
map layer and returns only the mapped/current subset of each locality's register
(~4% of the full historical count). It is faithful to Council's map, not a claim
of completeness — so every page carries a link to Council's full Development.i
register and never asserts a total DA count. See the notes for the full-register
list endpoint if that policy is ever revisited.

Refresh cadence: daily (DAs move often, but not hourly). Rate limit: 1 req/sec.

Usage:
    python -m scrape.development_applications [--out data/development_applications.json]

Attribution: Development.i, Ipswich City Council, CC BY 4.0.
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
from urllib.parse import quote

import httpx

HOST = "https://developmenti.ipswich.qld.gov.au"
HOME_URL = f"{HOST}/"
LOCALITY_URL = f"{HOST}/Geo/GetLocality"
FILTER_URL = f"{HOST}/Geo/GetApplicationFilterResults"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"
REQUEST_DELAY = 1.0  # seconds between requests — project invariant, keep it.
MAX_PER_LOCALITY = 2000  # paging guard; the geo layer returns far fewer.
PAGE_SIZE = 200

# The record-type the portal's own detail modal uses to key a DA by its
# application number. Verified against the live app's result tiles.
_DETAIL_TYPE = "plan_development_apps_unique"

# Fields that must NEVER leave this module (see the module docstring). Listed
# here only for documentation and the belt-and-braces assertion in
# normalise_feature(); normalisation is a whitelist, so absence is the default.
EXCLUDED_FIELDS = (
    "project_officer",   # assessing officer's name
    "decision_desc",     # outcome narrative / characterisation
    "appeal_result",     # appeal outcomes
    "submissionindicator",
    "publicnotification",
)

_TOKEN_RE = re.compile(
    r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
)


def detail_url(application_number: str) -> str:
    """Council's own detail view for one application, keyed by its application
    number. This is the 'go to Council for the detail' pointer that keeps this
    layer a pointer, not a replacement."""
    return (
        f"{HOST}/Home/ApplicationDetail?type={_DETAIL_TYPE}"
        f"&id={quote(application_number, safe='')}"
    )


def _clean(s: Any) -> str | None:
    if s is None:
        return None
    text = re.sub(r"\s+", " ", htmllib.unescape(str(s))).strip()
    # Council pads some fields with a trailing "; " (uselevel*). Trim stray
    # separators so nothing ragged reaches a page.
    text = text.strip(" ;")
    return text or None


def _date(s: Any) -> str | None:
    """Council sends ISO timestamps like '2026-08-04T00:00:00Z'. Keep the date
    only — a DA has a lodged day, not a lodged second."""
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s))
    return m.group(1) if m else None


def _filter_body(locality: str, start: int = 0) -> dict[str, Any]:
    """The exact request the live app sends, with DA-only toggles. `Progress`
    'all' spans In Progress + Decided + Past. ViewPort:null relies on
    LocalityId to scope the query (verified equivalent to a whole-LGA ViewPort)."""
    return {
        "Progress": "all",
        "StartDateUnixEpochNumber": None,
        "EndDateUnixEpochNumber": None,
        "DateRangeField": "submitted",
        "DateRangeDescriptor": None,
        "LotPlan": None,
        "LandNumber": None,
        "PropNumber": None,
        "DANumber": None,
        "BANumber": None,
        "PlumbNumber": None,
        "IncludeDA": True,       # development applications only —
        "IncludeBA": False,      # not building
        "IncludePlumb": False,   # not plumbing
        "LocalityId": locality,
        "DivisionId": None,
        "ApplicationTypeId": None,
        "SubCategoryUseId": None,
        "AssessmentLevels": [],
        "ShowCode": True,
        "ShowImpact": True,
        "ShowOther": True,
        "ShowIAGA": True,
        "ShowIAGI": True,
        "ShowNotifiableCode": True,
        "ShowReferralResponse": True,
        "ShowRequest": True,
        "PagingStartIndex": start,
        "MaxRecords": PAGE_SIZE,
        "Boundary": None,
        "ViewPort": None,
        "IncludeAroundMe": False,
        "SortField": "submitted",
        "SortAscending": False,
        "BBox": None,
        "PixelWidth": 800,
        "PixelHeight": 800,
    }


def normalise_feature(feature: dict[str, Any], suburb: str | None) -> dict[str, Any] | None:
    """One raw GeoJSON feature -> a flat, WHITELISTED DA record.

    Whitelist, not passthrough: only the fields listed below are ever emitted.
    Council's assessment-report, officer, decision-narrative, appeal and
    submission fields are structurally unreachable from here (Invariant 7)."""
    p = feature.get("properties") or {}
    app_no = _clean(p.get("application_number"))
    if not app_no:
        return None  # skip malformed rows rather than dying

    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None

    rec = {
        "application_number": app_no,
        "id": p.get("pdonline_id"),
        "description": _clean(p.get("description")),
        "status": _clean(p.get("progress")),            # In Progress | Decided | Past
        "application_type": _clean(p.get("application_type")),
        "assessment_level": _clean(p.get("assessment_level")),
        "date_received": _date(p.get("date_received")),
        "suburb": suburb,
        "coords": coords,
        "source_url": detail_url(app_no),
    }
    # Belt and braces: an excluded field must never have leaked into output.
    assert not (set(rec) & set(EXCLUDED_FIELDS)), "excluded DA field leaked"
    return rec


def _get_localities(client: httpx.Client, delay: float) -> list[str]:
    resp = _request(client, "GET", LOCALITY_URL, None, delay)
    data = resp.json()
    names = [
        f.get("id")
        for f in data.get("features", [])
        if isinstance(f.get("id"), str) and f.get("id").strip()
    ]
    return sorted(set(names))


def _request(client, method, url, json_body, delay, token=None, retries=4):
    headers = {}
    if token is not None:
        headers["RequestVerificationToken"] = token
        headers["X-Requested-With"] = "XMLHttpRequest"
    for attempt in range(retries):
        try:
            resp = client.request(method, url, json=json_body, headers=headers)
            resp.raise_for_status()
            time.sleep(delay)
            return resp
        except Exception:  # noqa: BLE001 — retry with backoff, then re-raise
            if attempt == retries - 1:
                raise
            time.sleep(2 * 2 ** attempt)
    raise RuntimeError("unreachable")


def _antiforgery_token(client: httpx.Client, delay: float) -> str:
    """The applications endpoint is anti-forgery protected: GET the home page
    to receive the antiforgery cookie (httpx keeps it) and read the matching
    hidden token from the HTML."""
    resp = _request(client, "GET", HOME_URL, None, delay)
    m = _TOKEN_RE.search(resp.text)
    if not m:
        raise RuntimeError(
            "no __RequestVerificationToken on the Development.i home page — "
            "the anti-forgery markup probably changed (see docs/notes.md)"
        )
    return m.group(1)


def fetch_locality(client, locality, token, delay) -> list[dict[str, Any]]:
    """All DA features for one locality, paged defensively (the geo layer
    returns well under one page in practice)."""
    out: list[dict[str, Any]] = []
    start = 0
    while start < MAX_PER_LOCALITY:
        resp = _request(client, "POST", FILTER_URL, _filter_body(locality, start),
                        delay, token=token)
        feats = resp.json().get("features", [])
        if not feats:
            break
        out.extend(feats)
        if len(feats) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return out


def scrape(delay: float = REQUEST_DELAY, limit: int | None = None) -> dict[str, Any]:
    """Enumerate every locality and collect its DA metadata. `limit` caps the
    number of localities (used to build a small offline sample)."""
    scraped_at = datetime.now(timezone.utc).isoformat()
    by_app: dict[str, dict[str, Any]] = {}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
    ) as client:
        token = _antiforgery_token(client, delay)
        localities = _get_localities(client, delay)
        if limit is not None:
            localities = localities[:limit]
        for locality in localities:
            try:
                feats = fetch_locality(client, locality, token, delay)
            except Exception as e:  # noqa: BLE001 — skip one suburb, don't die
                print(f"skip locality {locality}: {e}", file=sys.stderr)
                continue
            for feat in feats:
                rec = normalise_feature(feat, locality)
                if rec is None:
                    continue
                # The geo layer returns one feature per land parcel, so an
                # application spanning parcels repeats; de-dupe by its number.
                by_app.setdefault(rec["application_number"], rec)

    applications = sorted(
        by_app.values(),
        key=lambda a: (a.get("date_received") or "", a["application_number"]),
        reverse=True,
    )
    return {"scraped_at": scraped_at, "applications": applications}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("data/development_applications.json"))
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help="seconds between requests")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap localities scraped (for building a sample)")
    parser.add_argument("--compact", action="store_true",
                        help="write minified JSON")
    args = parser.parse_args()

    print(f"Fetching Development.i DAs (delay={args.delay}s) ...", file=sys.stderr)
    snapshot = scrape(delay=args.delay, limit=args.limit)
    print(f"Got {len(snapshot['applications'])} development applications",
          file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    indent = None if args.compact else 2
    args.out.write_text(json.dumps(snapshot, indent=indent, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
