"""
Scrape Ipswich City Council's annual Capital Works Program PDF.

Source: each budget cycle's page at
    https://www.ipswich.qld.gov.au/About-Council/Media-and-Publications/
        Corporate-Publications/Budget-YYYY-YYYY
links a "Capital Works Program" PDF (filename varies by year, e.g.
`2025-2028-capital-works-program.pdf`, `3-capitalworksprogram2026-2027_a4_web.pdf`).

Format: A4 PDF, ~28 pages. Per-program tables with columns
PROJECT | PROJECT DESCRIPTION | FY | FY | FY | 3 Year Total, amounts in $'000.
The tables have no ruling lines, so `page.extract_tables()` returns garbage;
this parser clusters `page.extract_words()` by x-position instead (column
right edges come from the repeated "$'000" header tokens).

Publishing change in the 2026-2027 cycle: per-project rows carry ● dots
(= funded in that FY) instead of dollar amounts; dollar figures exist only
at section ("Road Safety and Operations Total"), area ("FLEET Total") and
GRAND TOTAL level. Reproduced faithfully — `amounts_published` is false and
per-row `amounts`/`total` are null for those cycles.

Refresh cadence: once a year, when the new budget drops (June/July). Output
is committed to data/capital_works/capworks-<cycle>.json, not scraped daily.

Usage:
    python -m scrape.capital_works --cycle 2025-2026          # discover + parse
    python -m scrape.capital_works --cycle 2025-2026 --pdf f.pdf  # parse local

Attribution: Ipswich City Council, CC BY 4.0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pdfplumber

BASE = "https://www.ipswich.qld.gov.au"
BUDGET_PAGE = (
    BASE + "/About-Council/Media-and-Publications/Corporate-Publications/Budget-{cycle}"
)
USER_AGENT = "ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)"
REQUEST_GAP = 1.0  # seconds between requests (design invariant: <=1 req/sec)

FY_RE = re.compile(r"^\d{4}[–-]\d{4}$")
DOLLAR_RE = re.compile(r"^\$['’]000$")
AMOUNT_RE = re.compile(r"^(-|●|\(?\d{1,3}(?:,\d{3})*\)?)$")
CAPWORKS_HREF_RE = re.compile(r'href="([^"]*capital[\s_-]*works[^"]*\.pdf[^"]*)"', re.I)


# ---------------------------------------------------------------------------
# PDF parsing


def _cluster_lines(words: list[dict[str, Any]], tol: float = 4.0) -> list[list[dict[str, Any]]]:
    """Group words into visual lines by top coordinate."""
    lines: list[list[dict[str, Any]]] = []
    ref = None
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if ref is None or w["top"] - ref > tol:
            lines.append([w])
            ref = w["top"]
        else:
            lines[-1].append(w)
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
    return lines


def _norm_fy(s: str) -> str:
    return s.replace("–", "-")


def _parse_amount(s: str) -> Any:
    """'-' -> None (nil), '●' -> 'dot', '1,200' -> 1200."""
    if s == "-":
        return None
    if s == "●":
        return "dot"
    return int(s.strip("()").replace(",", ""))


def _is_bold(w: dict[str, Any]) -> bool:
    return "Bold" in w.get("fontname", "")


def _key(s: str | None) -> str:
    return re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def parse_pdf(path: Path | str, cycle: str, source_url: str | None) -> dict[str, Any]:
    fy_columns: list[str] = []
    col_rights: list[float] = []
    num_x_min = 339.0   # left edge of the numeric zone; refined from headers
    name_x_max = 152.0  # boundary between PROJECT and DESCRIPTION columns
    pending_headings: list[str] = []
    current_area: str | None = None
    programs: list[dict[str, Any]] = []
    area_totals: list[dict[str, Any]] = []
    grand_total: dict[str, Any] | None = None
    current: dict[str, Any] | None = None

    def col_of(w: dict[str, Any]) -> int:
        c = (w["x0"] + w["x1"]) / 2
        for i, r in enumerate(col_rights):
            if c <= r + 2.5:
                return i
        return len(col_rights) - 1

    def totals_dict(nums: list[dict[str, Any]]) -> dict[str, Any]:
        vals: dict[str, Any] = {fy: None for fy in fy_columns}
        vals["total"] = None
        for w in nums:
            v = _parse_amount(w["text"])
            i = col_of(w)
            key = fy_columns[i] if i < len(fy_columns) else "total"
            vals[key] = v
        return vals

    def open_program() -> dict[str, Any]:
        nonlocal current_area
        if len(pending_headings) >= 2:
            current_area = pending_headings[-2]
        section = pending_headings[-1] if pending_headings else None
        pending_headings.clear()
        prog = {"area": current_area, "section": section, "rows": [], "totals": None}
        programs.append(prog)
        return prog

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            if grand_total is not None:
                break  # everything after GRAND TOTAL is prose annex/glossary
            words = page.extract_words(extra_attrs=["size", "fontname"])
            prev_text_top: float | None = None
            page_rows: list[dict[str, Any]] = []
            numeric_events: list[tuple[float, list[dict[str, Any]]]] = []

            for line in _cluster_lines(words):
                texts = [w["text"] for w in line]
                joined = " ".join(texts)

                # Page-number footer.
                if len(line) == 1 and texts[0].isdigit() and line[0]["top"] > 780:
                    continue

                # Phase-key legend repeated at the bottom of table pages
                # ("KEY ● Concept design ● Planning and design ...").
                if texts[0].rstrip(":").upper() == "KEY":
                    continue

                # Footnotes ("1 Printed versions of this portfolio are
                # uncontrolled...") are set at 6pt; table text is 7.5pt.
                line = [w for w in line if w["size"] >= 7]
                if not line:
                    continue
                texts = [w["text"] for w in line]
                joined = " ".join(texts)

                # Financial-year header row ("2025–2026 2026–2027 2027–2028 3 Year Total").
                fy_tokens = [t for t in texts if FY_RE.fullmatch(t)]
                if len(fy_tokens) >= 3:
                    fy_columns = [_norm_fy(t) for t in fy_tokens[:3]]
                    continue

                # "$'000 $'000 $'000 $'000" row: fixes the numeric column edges
                # and marks the start (or page-continuation) of a table.
                dollar_words = [w for w in line if DOLLAR_RE.fullmatch(w["text"])]
                if len(dollar_words) >= 4:
                    col_rights = [w["x1"] for w in dollar_words]
                    num_x_min = min(w["x0"] for w in dollar_words) - 8
                    if pending_headings or current is None:
                        current = open_program()
                    continue

                # "PROJECT | PROJECT DESCRIPTION" header row.
                if texts[:2] == ["PROJECT", "PROJECT"] or (
                    "DESCRIPTION" in texts and "PROJECT" in texts
                ):
                    projs = [w for w in line if w["text"] == "PROJECT"]
                    if len(projs) >= 2:
                        name_x_max = projs[1]["x0"] - 4
                    continue

                text_words = [w for w in line if (w["x0"] + w["x1"]) / 2 < num_x_min]
                num_words = [
                    w
                    for w in line
                    if (w["x0"] + w["x1"]) / 2 >= num_x_min and AMOUNT_RE.fullmatch(w["text"])
                ]
                label = " ".join(w["text"] for w in text_words)

                # Totals rows: "<Section> Total", "<AREA> Total", "GRAND TOTAL".
                if label.lower().endswith("total") and num_words and text_words:
                    name = label[: -len("total")].strip().rstrip("–- ")
                    if label.upper() == "GRAND TOTAL" or label.upper().startswith("GRAND"):
                        grand_total = totals_dict(num_words)
                        break
                    if (
                        current is not None
                        and current["totals"] is None
                        and _key(name) == _key(current.get("section"))
                    ):
                        current["totals"] = totals_dict(num_words)
                        # Council's own mixed-case name is nicer than the
                        # all-caps heading; keep the heading casing otherwise.
                        if name and not name.isupper():
                            current["section"] = name
                    elif _key(name) == _key(current_area):
                        area_totals.append({"area": name, "totals": totals_dict(num_words)})
                    elif current is not None and current["totals"] is None:
                        current["totals"] = totals_dict(num_words)
                    else:
                        area_totals.append({"area": name, "totals": totals_dict(num_words)})
                    continue

                # Section/area headings: all-caps bold standalone lines.
                # Only between tables — inside an open table (rows started,
                # totals row not yet seen) all-caps bold text is a project
                # name (the 2023-2024 Corporate Projects table sets project
                # names in the same 9pt bold caps as 2025 section headings).
                alpha = re.sub(r"[^A-Za-z ]", "", joined).strip()
                if (
                    alpha
                    and alpha == alpha.upper()
                    and not num_words
                    and any(_is_bold(w) for w in line)
                    and (current is None or current["totals"] is not None or not current["rows"])
                ):
                    pending_headings.append(joined)
                    continue

                # Table content. Ignore anything outside a table (intro prose).
                if current is None or not col_rights:
                    continue
                if num_words:
                    numeric_events.append((line[0]["top"], num_words))
                if not text_words:
                    continue
                top = text_words[0]["top"]
                name_words = [w for w in text_words if w["x0"] < name_x_max]
                desc_words = [w for w in text_words if w["x0"] >= name_x_max]
                # Text lines within one row sit 8.5pt apart; distinct rows at
                # least 13.7pt. Some tables centre the name block vertically,
                # so a row can open with a description-only line — any text
                # after a row-sized gap starts a new row. At a page start
                # (prev_text_top is None) a name-less line is the continuation
                # of a row split across the page break.
                if prev_text_top is None:
                    new_row = bool(name_words) or not current["rows"]
                else:
                    new_row = top - prev_text_top > 11 or not current["rows"]
                if new_row:
                    row = {
                        "project": "",
                        "description": "",
                        "page": pno,
                        "_top": top,
                        "_last": top,
                    }
                    current["rows"].append(row)
                    page_rows.append(row)
                if current["rows"]:
                    row = current["rows"][-1]
                    row["_last"] = max(row.get("_last", top), top)
                    if name_words:
                        row["project"] = (
                            row["project"] + " " + " ".join(w["text"] for w in name_words)
                        ).strip()
                    if desc_words:
                        row["description"] = (
                            row["description"] + " " + " ".join(w["text"] for w in desc_words)
                        ).strip()
                prev_text_top = top

            # Attach amounts/dots to the nearest row on this page. Amounts sit
            # on the row's first text line (±1pt for the bold 3-Year-Total),
            # but ● markers float anywhere within the row's block, so match
            # against the row's vertical span rather than a single line.
            def _dist(r: dict[str, Any], t: float) -> float:
                if r["_top"] - 5 <= t <= r["_last"] + 5:
                    return 0.0
                return min(abs(t - r["_top"]), abs(t - r["_last"]))

            for top, nws in numeric_events:
                if not page_rows:
                    continue
                row = min(page_rows, key=lambda r: _dist(r, top))
                if _dist(row, top) > 12:
                    continue
                cells = row.setdefault("_cells", {})
                for w in nws:
                    cells[col_of(w)] = _parse_amount(w["text"])

    # Merge name-less continuation rows into the preceding named project.
    # (Some projects list several sub-items, each with its own description
    # and ● markers, under one name-column block — e.g. the 2026-2029
    # Enviroplan track upgrades.)
    for prog in programs:
        merged: list[dict[str, Any]] = []
        for row in prog["rows"]:
            if row["project"] or not merged:
                merged.append(row)
                continue
            prev = merged[-1]
            prev["description"] = (prev["description"] + " " + row["description"]).strip()
            cells = prev.setdefault("_cells", {})
            for col, v in row.get("_cells", {}).items():
                if v == "dot" or cells.get(col) == "dot":
                    cells[col] = "dot"
                elif isinstance(v, int) and isinstance(cells.get(col), int):
                    cells[col] += v
                elif v is not None:
                    cells[col] = v
        prog["rows"] = merged

    # Normalise rows.
    any_amounts = False
    for prog in programs:
        for row in prog["rows"]:
            cells = row.pop("_cells", {})
            row.pop("_top", None)
            row.pop("_last", None)
            dots = [i for i, v in cells.items() if v == "dot"]
            if dots:
                row["amounts"] = None
                row["total"] = None
                row["funded_years"] = [fy_columns[i] for i in sorted(dots) if i < 3]
            else:
                amounts = {fy: None for fy in fy_columns}
                total = None
                for i, v in cells.items():
                    if i < len(fy_columns):
                        amounts[fy_columns[i]] = v
                    else:
                        total = v
                row["amounts"] = amounts
                row["total"] = total
                row["funded_years"] = [fy for fy in fy_columns if amounts.get(fy)]
                if any(v for v in amounts.values()):
                    any_amounts = True

    # Drop tables that never accumulated rows (defensive).
    programs = [p for p in programs if p["rows"]]

    return {
        "cycle": cycle,
        "source_url": source_url,
        "fy_columns": fy_columns,
        "amounts_published": any_amounts,
        "programs": programs,
        "area_totals": area_totals or None,
        "grand_total": grand_total,
    }


# ---------------------------------------------------------------------------
# Discovery + scrape


def find_pdf_url(client: httpx.Client, cycle: str) -> str | None:
    """Find the capital works PDF linked from a budget cycle's page."""
    page_url = BUDGET_PAGE.format(cycle=cycle)
    resp = client.get(page_url, follow_redirects=True)
    resp.raise_for_status()
    m = CAPWORKS_HREF_RE.search(resp.text)
    if not m:
        return None
    href = m.group(1)
    return href if href.startswith("http") else BASE + href


def scrape(cycle: str, pdf_path: Path | None = None, source_url: str | None = None) -> dict[str, Any]:
    if pdf_path is not None:
        return parse_pdf(pdf_path, cycle, source_url)

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        url = find_pdf_url(client, cycle)
        if not url:
            raise RuntimeError(
                f"No capital works PDF found on the Budget-{cycle} page "
                "(older cycles may embed the schedule inside the main budget PDF)"
            )
        time.sleep(REQUEST_GAP)
        print(f"Downloading {url} ...", file=sys.stderr)
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
        tmp = Path(f"/tmp/capworks-{cycle}.pdf")
        tmp.write_bytes(resp.content)
        return parse_pdf(tmp, cycle, url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", required=True, help="budget cycle, e.g. 2025-2026")
    parser.add_argument("--pdf", type=Path, help="parse a local PDF instead of downloading")
    parser.add_argument("--source-url", help="source URL recorded when using --pdf")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--compact", action="store_true", help="minified JSON")
    args = parser.parse_args()

    out = args.out or Path(f"data/capital_works/capworks-{args.cycle}.json")
    data = scrape(args.cycle, pdf_path=args.pdf, source_url=args.source_url)
    n_rows = sum(len(p["rows"]) for p in data["programs"])
    print(
        f"Cycle {args.cycle}: {len(data['programs'])} programs, {n_rows} rows, "
        f"amounts_published={data['amounts_published']}, "
        f"grand_total={data['grand_total']}",
        file=sys.stderr,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.compact:
        out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
