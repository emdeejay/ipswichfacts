# Notes — receipts, findings, gotchas

Living document. Add to it when you discover something that would waste a future contributor's time to re-discover.

## Endpoints

### Civic Projects — GET

```
https://maps.ipswich.qld.gov.au/icc/data/Projects_Infrastructure.JSON
```

Plain GeoJSON FeatureCollection. EPSG:4326. Point geometries.

Feature properties (from the app's `icc_capital_works.js`):

```
ID, SUBURB, DIVISION 1..4 ("T" if in that division, else null),
PROJECT_NAME, COUNCIL_REFERENCE, MAJOR_PROJECT ("Yes"|"No"),
PROJECT_DESCRIPTION, PROJECT_STATUS, WHAT_TO_EXPECT, MANAGED_BY,
PHASE_OF_WORK ("What's Being Planned" | "Current Program" |
               "Under Construction" | "Survey Underway" |
               "Completed" | "On Hold" | "Historic"),
EXTRA_INFORMATION_{1..10}_OBJ (URL),
EXTRA_INFORMATION_{1..10}_TITLE (link label),
DATE_PUBLISHED (YYYYMMDD),
DATE_UPDATED (YYYYMMDD)
```

Verified 15 Jul 2026: 385 features, 778,741 bytes uncompressed. Cache-Control is `public, max-age=0` so re-scraping daily is polite. `Last-Modified` header is meaningful; use conditional GET if being extra polite.

### Traffic dashboard — GET

Two feeds. Both **double-encoded** (a JSON string containing another JSON — parse twice).

```
https://traffic.ipswich.qld.gov.au/dashboard/imsRoad
https://traffic.ipswich.qld.gov.au/dashboard/tmrRoadData
```

- `imsRoad` — Council's own incident-management feed (QIT Plus IMS). Inner payload is a `FeatureCollection`. Usually empty; populates during actual events.
- `tmrRoadData` — QLDTraffic (Asignit) impacts within Ipswich LGA, proxied. Inner payload is a **bare array** of Features, not a FeatureCollection. Usually populated. Each feature has a rich `road_summary` sub-object containing `road_name`, `suburb`, `postcode`, `local_government_area`, `district`.

Feature properties on tmrRoadData: `id, status, published, source, url, event_type, event_subtype, event_due_to, impact, duration, event_priority, description, advice, information, road_summary, last_updated, next_inspection, web_link, group_id`.

Scrape cadence: every 15–30 min for closures is plenty. Nothing changes second-by-second at the LGA level.

### Council Business Papers — GET (scrape/council_meetings.py)

`https://ipswich.infocouncil.biz/`

- Meeting index at `/` lists the current year. Rows are `<tr class="bpsGridMenuItem">` / `bpsGridMenuAltItem`; each row has `bpsGridDate` (e.g. `30 Jun 2026<br>…`), `bpsGridCommittee` (full committee name), and doc links wrapped in a redirector: `RedirectToDoc.aspx?URL=Open/YYYY/MM/{FILE}`. The `Open/...` path fetches fine directly.
- Doc filename grammar: `{CODE}_{YYYYMMDD}_{AGN|MIN|MAT|ATT}_{ID}{SUFFIXES}_WEB.htm` where suffixes seen include `_AT`, `_SUP` (supplementary), `_EXTRA` (extraordinary meetings), `_EXCLUDED`. Same committee code (e.g. `CO`) covers both ordinary and extraordinary meetings — the `bpsGridCommittee` cell disambiguates ("Council" vs "Extraordinary Council"), so build the code→name mapping per row, don't hardcode.
- A `*_WEB.htm` doc is a **frameset**, not content. Frame `Navigation` → `{...}_BMK.HTM` (nav/bookmarks), frame `Paper` → the actual paper HTML. **Do not derive the inner names by stripping suffixes** — MIN inner names drop `_AT` but AGN inner names keep it (`CO_20260226_AGN_3996_AT.HTM`). Fetch the frameset and read the frame `src` attributes.
- BMK frame: one `<a class='bpsNavigationListItem' href='...#ANCHOR' title='...'>` per agenda item. Item anchors start `PDF2_ReportName_` (papers prepared in advance, including `_N_*` variants) **or `PDF2_NewItem_`** (items raised in the meeting — advisory committees sometimes have *only* these). Ignore `PDF2_Resolution_*` (procedural: leave of absence, meeting-cancelled notes), `PDF1_Contents`, and `bpsNavigationDetail` links. The `title` attribute is cleaner than inner text (which can contain tabs/entities). A handful of meetings legitimately have zero items (cancelled meetings whose minutes are a single procedural resolution).
- Paper frame: Word-filtered HTML (MsoNormal soup), declared `charset=windows-1252` in a meta tag — sniff the meta, don't trust httpx's default decode. Item anchors are `<a name="PDF2_ReportName_...">` and the tag can be **split across lines** (`<a\n  name="..."`), so match with `\s+`. Text between one ReportName anchor and the next is that item's content. Resolutions are paragraphs containing "Moved by" / "Seconded by".
- MAT/ATT docs are attachments (skip in v1); `_SUP` docs are supplementary papers (skip in v1). Minutes supersede agendas — prefer MIN over AGN when both exist.
- Full-text search endpoint exists (`SearchResults.aspx`) but is powered by Cludo. Prefer crawling the meeting index and extracting text from HTML ourselves — avoids being rate-limited by a third-party search service.

Committee codes observed 2026 (full names come from the index table): AAC, CASCC, CBWS, CO, EACDC, ESC, FAGCC, IPAAC, LCSAC, MAC, RAC, SRAC.

### Capital Works Program PDF — GET (scrape/capital_works.py)

Discovery: each budget cycle's page at
`https://www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications/Budget-YYYY-YYYY`
links exactly one capital-works PDF. Filenames vary per year
(`2025-2028-capital-works-program.pdf`, `3-capitalworksprogram2026-2027_a4_web.pdf`,
`capitalworksprogram-2023-2024.pdf`, `4-capitalworksprogram2024-2027_web.pdf`) —
match hrefs on `capital[\s_-]*works.*\.pdf`, case-insensitive. Output is
committed per cycle to `data/capital_works/` and loaded unconditionally by the
build (like the meeting/news archives); refresh once a year via
`make backfill-capworks`.

Parsing (an earlier note here suggested `camelot`; that was wrong):

- **`page.extract_tables()` returns garbage** — the tables have no ruling
  lines. Parse `page.extract_words()` and cluster by x-position instead.
- Numeric column right edges come from the four repeated `$’000` header
  tokens (curly apostrophe, U+2019). The PROJECT/DESCRIPTION boundary comes
  from the second `PROJECT` header token (x≈156; description starts x≈154.5).
- Text lines within a row sit 8.5pt apart; distinct rows ≥13.7pt. Split rows
  on a >11pt gap between text-zone lines. Some tables (2023-24 Corporate
  Projects) centre the name block vertically, so a row can open with a
  description-only line.
- **Numbers don't sit on the row's text lines.** The bold 3-Year-Total figure
  is offset ~1pt; the ● markers (25pt glyphs) float anywhere within the row's
  block, ~2pt above the first line or several pt below. Attach numeric words
  to the row whose vertical span they fall in, not to a same-top line.
- **2026–27 publishing change:** per-project rows carry ● dots (= funded that
  FY, amount unpublished); dollar figures exist only at section
  ("Road Safety and Operations Total"), area ("FLEET Total", 9pt bold) and
  GRAND TOTAL (10.5pt) level. 2023–2026 cycles publish per-row amounts. `-`
  means nil.
- Section/area headings are all-caps bold lines **between** tables — but the
  2023-24 Corporate Projects table sets *project names* in the same 9pt bold
  caps as 2025 section headings, so only treat caps lines as headings when no
  table is open. Theme/heading sizes differ per year (9pt 2025, 14pt 2023,
  16pt 2026) — don't key on size.
- Skip the 6pt footnote ("Printed versions of this portfolio are
  uncontrolled…") and the bottom-of-page `KEY: ● Concept design …` legend
  (its 12pt dots otherwise attach to the nearest row).
- Some projects list several description sub-items each with its own ● under
  one name block (2026 Enviroplan track upgrades) — merge name-less rows into
  the preceding named project.
- Everything after the GRAND TOTAL line is prose annex/glossary — stop there.
- Validation invariants that all shipped cycles satisfy exactly: per-row FY
  amounts sum to the row's 3-year total; per-program rows sum to the stated
  program total; program totals sum to the GRAND TOTAL.

Cycle coverage: 2023-2024 through 2026-2027 parse clean. **2021-2022 and
2022-2023 are skipped** — older layout generation: nil cells are blank (not
`-`), single-line rows sit at the same 8.5pt pitch as wrapped lines (rows
merge), and iFuture theme headings wrap across lines; program totals came out
systematically wrong, so don't ship them. 2020-2021 has no dedicated capital
works PDF at all (schedule is inside `budget2020-21_report_a4_web.pdf`).

Deep links: `{pdf_url}#page=N` opens the PDF at the right page in browsers —
every republished row links back to its page.

### Ipswich First (WordPress) — GET (scrape/ipswich_first.py)

```
https://www.ipswichfirst.com.au/wp-json/wp/v2/posts?per_page=100&page=N
```

Standard WP REST API. Paginate; `x-wp-totalpages` header tells you when to stop (it reflects any active filters). Verified 15 Jul 2026: 4,922 posts, earliest 2017-07-01.

- **`per_page=100` intermittently 502s** — the origin appears to time out rendering the full content of 100 posts and the gateway gives up, and the same page keeps failing on immediate retry. `per_page=50` is reliable. The scraper also backs off 5/10/20 s between retries.

- **Request `&_fields=id,date,modified,slug,link,title,excerpt,content,categories`** — without it every post carries a huge `yoast_head` blob and `_links` cruft.
- **`content.rendered` is NOT plain HTML.** The site is built with Divi, and the raw builder shortcodes come through verbatim: `[et_pb_section fb_built=&#8221;1&#8243; ...]` wrapping the real `<p>` HTML, plus a boilerplate `[et_pb_cta ...]` subscribe box at the end. Shortcode attribute quotes are entity-encoded curly quotes, so strip `\[/?et_pb_\w+[^\]]*\]` tokens *before* unescaping entities. Applies to all years (2017 through current).
- **Titles/excerpts contain HTML entities** (`&#8230;`, `&#8217;`) — `html.unescape` everything; body text also uses `\xa0` non-breaking spaces.
- **Date filtering**: `&after=...&before=...` (ISO, site-local time, strict comparison). Per-year scrape uses `after={Y-1}-12-31T23:59:59&before={Y+1}-01-01T00:00:00` then filters client-side by year as belt and braces.
- **Categories** are ids; one request to `/wp-json/wp/v2/categories?per_page=100` gives the id→name map (27 categories, single page).
- Same immutable-history pattern as meetings: `data/archive/news-YYYY.json` committed once per past year, `data/news.json` (current year) scraped daily and gitignored.

## Data-model gotchas

- **Division fields** come through as `DIVISION 1`, `DIVISION 2`, `DIVISION 3`, `DIVISION 4` — with a space, uppercase. Values are `"T"` (in division) or `null`. Only 4 divisions on the map layer even though the LGA has more councillors — because the map is scoped to civic-project reporting. Full councillor mapping requires a separate scrape.
- **`COUNCIL_REFERENCE`** looks like `INF03149`, `CCC00083`, `IDM01002`, etc. It's meaningful (prefix denotes program) but does NOT appear in Council Business Papers or news, so it's not a useful join key across systems.
- **`EXTRA_INFORMATION_*_OBJ`** URLs go via a redirector on the Council side; treat them as opaque strings, don't try to normalise.
- **`DATE_PUBLISHED` / `DATE_UPDATED`** are `YYYYMMDD` strings, no delimiters.
- **Suburb strings** sometimes include compound suburbs like `"Rosewood / Tallegalla"`. Normalise carefully — don't split on `/` blindly, because it also appears in some road names.

## Frontend/SEO gotchas

- **Slugs** are derived from Council names, kebab-lowercased, max 80 chars. Don't change the algorithm without providing a migration table — every URL is a canonical Google entry.
- **`<title>` tags** need to be human-readable and end with `— Ipswich Facts`.
- **Meta description** should be ~150 chars, drawn from the entity's substantive content.
- **Canonical URL** is set explicitly to `https://ipswichfacts.au/<path>/` — trailing slash matters; index.html-per-directory pattern relies on it.
- **Sitemap** is regenerated on every build. Ping Google Search Console after deploy if the site grows meaningfully (add to workflow).

## Attribution and legal

- Council content is CC BY 4.0. Attribution reads: `Source: [name of Council page](URL) (CC BY 4.0)` at the bottom of every derived page.
- Site framing on every footer: "Unofficial. Council's own systems are the source of truth."
- No user-submitted content in v1 → no defamation surface.
- If asked to remove: comply with a request to correct a factual reproduction error, escalate anything else to a decision about the site's mission before acting.
