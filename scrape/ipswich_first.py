"""
Scrape media releases from Ipswich First, Ipswich City Council's news site.

Endpoint: standard WordPress REST API,

    https://www.ipswichfirst.com.au/wp-json/wp/v2/posts?per_page=100&page=N

Paginate until the `x-wp-totalpages` header is exhausted (it reflects any
active filters). ~4,900 posts back to 2017-07-01. Request
`_fields=id,date,modified,slug,link,title,excerpt,content,categories` to
skip the heavy `yoast_head`/`_links` payload on every post.

Gotchas (see docs/notes.md):
- `content.rendered` is NOT plain HTML — the site is built with Divi, and
  raw builder shortcodes come through verbatim (`[et_pb_section ...]`
  wrapping the real `<p>` HTML, plus a boilerplate `[et_pb_cta ...]`
  subscribe box). Shortcode attribute quotes are entity-encoded curly
  quotes, so strip the `[...]` tokens BEFORE unescaping entities.
- Titles/excerpts carry HTML entities (`&#8230;`); body text uses `\xa0`.
- Date filtering via `after`/`before` (ISO, strict comparison).
- Category ids map to names via one request to /wp/v2/categories.

Posts are effectively immutable once published, so history is scraped once
per year into data/archive/news-YYYY.json (committed) and the daily cron
fetches only the current year into data/news.json.

Refresh cadence: daily. Rate limit: 1 request/second (~1 request per 100
posts, so a current-year scrape is a handful of requests).

Usage:
    python -m scrape.ipswich_first [--out data/news.json]
        [--year YYYY | --all] [--delay 1.0] [--compact]

Attribution: Ipswich First, Ipswich City Council, CC BY 4.0.
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

API_BASE = "https://www.ipswichfirst.com.au/wp-json/wp/v2"
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"
REQUEST_DELAY = 1.0  # seconds between requests — project invariant, keep it.
# 100-post pages intermittently 502 (the server times out rendering the full
# content of 100 posts); 50 keeps responses fast enough to be reliable.
PER_PAGE = 50
MAX_TEXT = 12000
FIELDS = "id,date,modified,slug,link,title,excerpt,content,categories"

# Elements whose entire content is noise, not article text.
_DROP_RE = re.compile(
    r"<(script|style|iframe|form|svg|noscript)\b.*?</\1\s*>", re.I | re.S
)
# Divi builder + WordPress-core shortcode tokens. Attribute values contain
# entity-encoded curly quotes but no ']', so a bracket match is safe. Strip
# before html.unescape so decoded quotes can't confuse the tag stripper.
_SHORTCODE_RE = re.compile(
    r"\[/?(?:et_pb_\w+|caption|gallery|embed|video|audio|playlist)\b[^\]]*\]"
)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
# Block-level tags mark paragraph breaks; literal newlines in the source are
# just markup formatting and must NOT split paragraphs.
_BLOCK_RE = re.compile(
    r"</?(?:p|div|li|ul|ol|h\d|tr|table|thead|tbody|blockquote|figure"
    r"|figcaption|section|article|header|footer)\b[^>]*>|<br\s*/?>",
    re.I,
)


def html_to_text(rendered: str | None) -> str:
    """Divi-wrapped WP HTML → plain-text paragraphs joined with \\n."""
    s = _DROP_RE.sub(" ", rendered or "")
    s = _SHORTCODE_RE.sub("\x00", s)
    s = _IMG_RE.sub(" ", s)
    s = _BLOCK_RE.sub("\x00", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s).replace("\xa0", " ")
    # Some posts (2017-era quizzes) carry double-encoded embed snippets
    # (&lt;div data-fsid=...&gt;) that only become tags after unescaping —
    # strip those too.
    s = _DROP_RE.sub(" ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    paras = [re.sub(r"\s+", " ", part).strip() for part in s.split("\x00")]
    return "\n".join(p for p in paras if p)[:MAX_TEXT]


def _plain(s: str | None) -> str:
    """title.rendered / excerpt.rendered → single-line plain text."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", htmllib.unescape(s).replace("\xa0", " ")).strip()


def normalise_post(post: dict[str, Any], cat_names: dict[int, str]) -> dict[str, Any]:
    return {
        "id": post.get("id"),
        "date": (post.get("date") or "")[:10],
        "slug": post.get("slug"),
        "url": post.get("link"),
        "title": _plain((post.get("title") or {}).get("rendered")),
        "excerpt": _plain((post.get("excerpt") or {}).get("rendered")),
        "text": html_to_text((post.get("content") or {}).get("rendered")),
        "categories": [cat_names[c] for c in post.get("categories") or [] if c in cat_names],
    }


def _get(client: httpx.Client, url: str, params: dict[str, Any],
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
            # The API 502s under load; give it room to breathe (5/10/20 s).
            time.sleep(5 * 2 ** attempt)
    raise RuntimeError("unreachable")


def fetch_categories(client: httpx.Client, delay: float) -> dict[int, str]:
    """One id→name map for the whole run (27 categories, single page)."""
    cats: dict[int, str] = {}
    page, total_pages = 1, 1
    while page <= total_pages:
        resp = _get(client, f"{API_BASE}/categories",
                    {"per_page": 100, "page": page, "_fields": "id,name"}, delay)
        total_pages = int(resp.headers.get("x-wp-totalpages") or total_pages)
        for c in resp.json():
            cats[c["id"]] = htmllib.unescape(c.get("name") or "")
        page += 1
    return cats


def scrape(year: int | None = None, all_posts: bool = False,
           delay: float = REQUEST_DELAY) -> dict[str, Any]:
    """Fetch posts. Default: the current year (daily cron). `year` scrapes
    one archive year; `all_posts` disables the date filter (backfill)."""
    scraped_at = datetime.now(timezone.utc).isoformat()
    params_base: dict[str, Any] = {"per_page": PER_PAGE, "_fields": FIELDS}
    y = None
    if not all_posts:
        y = year or datetime.now().year
        # Strict comparisons: after 23:59:59 of the prior NYE includes a post
        # stamped exactly YYYY-01-01T00:00:00; before next NYE excludes it.
        params_base["after"] = f"{y - 1}-12-31T23:59:59"
        params_base["before"] = f"{y + 1}-01-01T00:00:00"

    posts: list[dict[str, Any]] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
    ) as client:
        cat_names = fetch_categories(client, delay)
        page, total_pages = 1, 1
        while page <= total_pages:
            try:
                resp = _get(client, f"{API_BASE}/posts",
                            {**params_base, "page": page}, delay)
            except Exception as e:  # noqa: BLE001 — skip one page, never die
                if page == 1:
                    raise  # can't even establish total_pages — a real outage
                print(f"skip page {page}: {e}", file=sys.stderr)
                page += 1
                continue
            total_pages = int(resp.headers.get("x-wp-totalpages") or total_pages)
            posts.extend(normalise_post(p, cat_names) for p in resp.json())
            page += 1

    if y is not None:
        # Belt and braces on top of the API-side after/before filter.
        posts = [p for p in posts if (p.get("date") or "").startswith(f"{y}-")]
    posts.sort(key=lambda p: (p.get("date") or "", p.get("id") or 0), reverse=True)
    return {"scraped_at": scraped_at, "posts": posts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/news.json"))
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--year", type=int, default=None,
                       help="scrape one calendar year (default: current year)")
    scope.add_argument("--all", action="store_true",
                       help="no date filter — every post (one-off backfill)")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY,
                        help="seconds between requests")
    parser.add_argument("--compact", action="store_true",
                        help="write minified JSON (for committed archive files)")
    args = parser.parse_args()

    scope_label = "all" if args.all else (args.year or "current year")
    print(f"Fetching Ipswich First posts ({scope_label}, delay={args.delay}s) ...",
          file=sys.stderr)
    snapshot = scrape(year=args.year, all_posts=args.all, delay=args.delay)
    print(f"Got {len(snapshot['posts'])} posts", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    indent = None if args.compact else 2
    args.out.write_text(json.dumps(snapshot, indent=indent, ensure_ascii=False))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
