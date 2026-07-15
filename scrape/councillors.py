"""
Scrape mayor + councillor profiles from ipswich.qld.gov.au.

One-off (re-run after each election, or if Council reshuffles): the
output is committed as data/councillors.json and is NOT part of the
daily scrape. Ipswich has a Mayor plus eight councillors, two per
division (four divisions), elected on four-year terms (last: 2024).

The Mayor-Councillors index page links one profile page per person;
each profile carries the name in <h1>, a "Division N" marker (except
the Mayor, who is elected city-wide), and a mailto: contact link.

Usage:
    python -m scrape.councillors [--out data/councillors.json]

Attribution: Ipswich City Council, CC BY 4.0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

INDEX_URL = "https://www.ipswich.qld.gov.au/About-Council/Mayor-Councillors"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"
REQUEST_DELAY = 1.0

PROFILE_RE = re.compile(
    r'href="(https://www\.ipswich\.qld\.gov\.au/About-Council/Mayor-Councillors/'
    r'(?:Mayor|Deputy-Mayor-[\w-]+|Cr-[\w-]+))"'
)
NAME_RE = re.compile(r"<h1[^>]*>([^<]+)</h1>")
DIVISION_RE = re.compile(r"\bDivision (\d)\b")
EMAIL_RE = re.compile(r'mailto:([\w.+-]+@[\w.-]+)')


def _fetch(client: httpx.Client, url: str) -> str:
    resp = client.get(url)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.text


def _role(url: str) -> str:
    if url.endswith("/Mayor"):
        return "Mayor"
    if "/Deputy-Mayor-" in url:
        return "Deputy Mayor"
    return "Councillor"


def scrape() -> dict[str, Any]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    people = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        index_html = _fetch(client, INDEX_URL)
        urls = sorted(set(PROFILE_RE.findall(index_html)))
        for url in urls:
            try:
                page = _fetch(client, url)
            except Exception as e:  # noqa: BLE001 — skip one profile, never die
                print(f"skip {url}: {e}", file=sys.stderr)
                continue
            name_m = NAME_RE.search(page)
            div_m = DIVISION_RE.search(page)
            email_m = EMAIL_RE.search(page)
            people.append(
                {
                    "name": name_m.group(1).strip() if name_m else url.rsplit("/", 1)[-1].replace("-", " "),
                    "role": _role(url),
                    "division": int(div_m.group(1)) if div_m else None,
                    "email": email_m.group(1) if email_m else None,
                    "url": url,
                }
            )
    # Mayor first, then by division, deputy mayor ahead of plain councillors.
    people.sort(key=lambda p: (p["division"] or 0, p["role"] != "Deputy Mayor", p["name"]))
    return {"scraped_at": scraped_at, "councillors": people}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/councillors.json"))
    args = parser.parse_args()

    print(f"Fetching councillor profiles from {INDEX_URL} ...", file=sys.stderr)
    snapshot = scrape()
    n_div = sum(1 for p in snapshot["councillors"] if p["division"])
    print(f"Got {len(snapshot['councillors'])} people ({n_div} with divisions)", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
