"""
Scrape Council meeting agendas and minutes from ipswich.infocouncil.biz.

The meeting index at https://ipswich.infocouncil.biz/ lists the current
year's meetings in a table. Each row carries the committee's full name
(`bpsGridCommittee` cell), the meeting date (`bpsGridDate` cell), and
links (via `RedirectToDoc.aspx?URL=...`) to documents named:

    Open/YYYY/MM/{CODE}_{YYYYMMDD}_{AGN|MIN|MAT|ATT}_{ID}{SUFFIXES}_WEB.htm

Suffixes seen: _AT, _SUP (supplementary), _EXTRA (extraordinary),
_EXCLUDED. We take AGN (agenda) and MIN (minutes) docs only, skip _SUP,
and prefer MIN over AGN for the same meeting (minutes contain the
decisions).

Each `*_WEB.htm` is a frameset, not content. Its `Navigation` frame is a
`*_BMK.HTM` bookmark list (one link per agenda item, anchors starting
`PDF2_ReportName_`), and its `Paper` frame is the actual paper as
Word-filtered HTML (windows-1252). Inner filenames can't be reliably
derived from the WEB name (MIN drops `_AT`, AGN keeps it) so we fetch the
frameset and read the frame `src` attributes.

Refresh cadence: daily is plenty; papers appear a few days before/after
each meeting. Rate limit: 1 request/second (3 requests per meeting).

Usage:
    python -m scrape.council_meetings [--out data/meetings.json] [--limit N]

Attribution: Ipswich City Council, CC BY 4.0.
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

BASE_URL = "https://ipswich.infocouncil.biz/"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"
REQUEST_DELAY = 1.0  # seconds between requests — project invariant, keep it.
MAX_ITEM_TEXT = 8000

DOC_RE = re.compile(
    r"(Open/\d{4}/\d{2}/([A-Z]+)_(\d{8})_(AGN|MIN)_(\d+)((?:_[A-Z]+)*)_WEB\.htm)"
)
ROW_SPLIT_RE = re.compile(r'<tr class="bpsGridMenu(?:Alt)?Item">')
COMMITTEE_CELL_RE = re.compile(r'class="bpsGridCommittee">([^<]+)<')
FRAME_RE = re.compile(r"<frame[^>]*\bname='(\w+)'[^>]*\bsrc='([^']+)'", re.IGNORECASE)
# Agenda items are anchored PDF2_ReportName_* (papers prepared in advance)
# or PDF2_NewItem_* (items raised in the meeting). PDF2_Resolution_* anchors
# are procedural (leave of absence, meeting cancelled) — not items.
BMK_ITEM_RE = re.compile(
    r"<a\s+href='[^']*#(PDF2_(?:ReportName|NewItem)_[^'#]+)'[^>]*"
    r"\btitle='([^']*)'[^>]*class='bpsNavigationListItem'"
)
PAPER_ANCHOR_RE = re.compile(r'<a\s+name=["\'](PDF2_(?:ReportName|NewItem)_[^"\'>\s]+)["\']')
CHARSET_RE = re.compile(rb"charset=([\w-]+)")


def _decode(content: bytes) -> str:
    """Decode a response honouring the meta-declared charset (the paper
    frames declare windows-1252; httpx would otherwise guess utf-8)."""
    m = CHARSET_RE.search(content[:1024])
    charset = m.group(1).decode("ascii", "replace") if m else "utf-8"
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def _fetch(client: httpx.Client, url: str) -> str:
    resp = client.get(url)
    resp.raise_for_status()
    text = _decode(resp.content)
    time.sleep(REQUEST_DELAY)
    return text


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip()


def parse_index(index_html: str) -> list[dict[str, Any]]:
    """Parse the meeting index into one record per meeting.

    Groups AGN/MIN docs by (committee code, date, id); prefers MIN.
    Skips MAT/ATT (attachments) and _SUP (supplementary) docs.
    """
    meetings: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in ROW_SPLIT_RE.split(index_html)[1:]:
        cm = COMMITTEE_CELL_RE.search(row)
        committee = _clean(cm.group(1)) if cm else ""
        for m in DOC_RE.finditer(row):
            path, code, ymd, doc_type, doc_id, suffixes = m.groups()
            if "_SUP" in suffixes:
                continue
            key = (code, ymd, doc_id)
            rec = meetings.setdefault(
                key,
                {
                    "id": doc_id,
                    "committee_code": code,
                    "committee": committee,
                    "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}",
                    "docs": {},
                },
            )
            rec["docs"].setdefault(doc_type, BASE_URL + path)
            # Prefer the row that actually carried this doc for the name
            # (supplementary rows say e.g. "... Supplementary").
            if committee and not rec["committee"]:
                rec["committee"] = committee
    out = list(meetings.values())
    out.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return out


def parse_frameset(frameset_html: str) -> tuple[str | None, str | None]:
    """Return (navigation_src, paper_src) from a *_WEB.htm frameset."""
    nav = paper = None
    for name, src in FRAME_RE.findall(frameset_html):
        if name.lower() == "navigation":
            nav = src
        elif name.lower() == "paper":
            paper = src
    return nav, paper


def parse_bmk(bmk_html: str) -> list[dict[str, str]]:
    """Ordered agenda items from the bookmark frame: [{anchor, title}]."""
    items = []
    seen = set()
    for anchor, title in BMK_ITEM_RE.findall(bmk_html):
        if anchor in seen:
            continue
        seen.add(anchor)
        items.append({"anchor": anchor, "title": _clean(title)})
    return items


def _strip_html(fragment: str) -> list[str]:
    """Word-filtered HTML fragment → list of text paragraphs.

    Block-level tags mark paragraph breaks; literal newlines in the source
    are just Word's line wrapping and must NOT split paragraphs."""
    s = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    s = re.sub(r"</?(?:p|tr|br|div|li|h\d)\b[^>]*>", "\x00", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    paras = [re.sub(r"\s+", " ", part).strip() for part in s.split("\x00")]
    return [p for p in paras if p]


def parse_items(bmk_html: str, paper_html: str) -> list[dict[str, Any]]:
    """Join the bookmark item list with per-item text from the paper frame."""
    bmk_items = parse_bmk(bmk_html)
    positions = {m.group(1): m.start() for m in PAPER_ANCHOR_RE.finditer(paper_html)}
    starts = sorted(positions.values())

    items = []
    for it in bmk_items:
        text = ""
        resolution = None
        pos = positions.get(it["anchor"])
        if pos is not None:
            nxt = next((s for s in starts if s > pos), len(paper_html))
            paras = _strip_html(paper_html[pos:nxt])
            resolution = next((p for p in paras if "Moved by" in p), None)
            text = "\n".join(paras)[:MAX_ITEM_TEXT]
        items.append(
            {
                "anchor": it["anchor"],
                "title": it["title"],
                "text": text,
                "resolution": resolution,
            }
        )
    return items


def scrape(limit: int = 0) -> dict[str, Any]:
    scraped_at = datetime.now(timezone.utc).isoformat()
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        index_html = _fetch(client, BASE_URL)
        candidates = parse_index(index_html)
        if limit:
            candidates = candidates[:limit]

        meetings = []
        for rec in candidates:
            doc_type = "MIN" if "MIN" in rec["docs"] else "AGN"
            source_url = rec["docs"].get(doc_type)
            if not source_url:
                continue
            try:
                frameset = _fetch(client, source_url)
                nav_src, paper_src = parse_frameset(frameset)
                if not nav_src or not paper_src:
                    raise ValueError("frameset missing Navigation/Paper frames")
                base = source_url.rsplit("/", 1)[0] + "/"
                bmk_html = _fetch(client, base + nav_src)
                paper_html = _fetch(client, base + paper_src)
                items = parse_items(bmk_html, paper_html)
            except Exception as e:  # noqa: BLE001 — skip one meeting, never die
                print(f"skip {source_url}: {e}", file=sys.stderr)
                continue
            meetings.append(
                {
                    "id": rec["id"],
                    "committee_code": rec["committee_code"],
                    "committee": rec["committee"],
                    "date": rec["date"],
                    "doc_type": doc_type,
                    "source_url": source_url,
                    "items": items,
                }
            )

    meetings.sort(key=lambda m: (m["date"], m["id"]), reverse=True)
    return {"scraped_at": scraped_at, "meetings": meetings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/meetings.json"))
    parser.add_argument("--limit", type=int, default=0,
                        help="only fetch the N most recent meetings (0 = all)")
    args = parser.parse_args()

    print(f"Fetching meeting index from {BASE_URL} ...", file=sys.stderr)
    snapshot = scrape(limit=args.limit)
    n_items = sum(len(m["items"]) for m in snapshot["meetings"])
    print(f"Got {len(snapshot['meetings'])} meetings, {n_items} items", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
