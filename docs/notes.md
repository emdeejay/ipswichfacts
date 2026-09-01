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

### Shape Your Ipswich (Granicus EngagementHQ) — GET (scrape/shape_your_ipswich.py)

Community-consultation site, "the Hive" theme on Granicus EngagementHQ. Host
redirects `shapeyouripswich.com.au` → `https://www.shapeyouripswich.com.au/`
(302) — always use the `www.` host.

- **No public REST API.** `/api/v2/projects`, `/projects.json` etc. all 404.
- **But there IS a clean JSON feed** — the one the `/projects` page's "Show
  more" button calls. Each `<section class="projects-list" data-route="…">`
  block carries its own load-more route:

  ```
  https://www.shapeyouripswich.com.au/ccm/the_hive_projects/tools/
      the_hive_projects_list/load_more/{blockID}?page=N
      -> { "result": [ {project}, … ], "moreToLoad": bool }
  ```

  The front-end (`packages/the_hive_projects/js/hive-projects-list.js`) starts
  at `page=0` and increments while `moreToLoad` is true. Currently every block
  returns its whole set on page 0 (`moreToLoad:false`), but the scraper paginates
  defensively. There are **three list blocks** — one each for the Open, Active
  and Closed project groups — so fetch all three routes and de-dupe by
  `projectID`. Discover the routes from the `/projects` HTML; don't hardcode the
  numeric block ids (Council can change them).
- **Each `result` item is fully-formed JSON**, no HTML parsing needed:
  `projectID` (stable int), `projectName`, `projectDescription` (Council's own
  one-line summary), `projectStatus` (`Open`|`Active`|`Closed`), `projectPath`
  (canonical URL — last path segment is the stable EngagementHQ slug),
  `projectDateNum`/`projectDateStr` (a recency/last-activity date; spans 2022→now,
  correlates with status), `projectLocationArray` (**gazetted Ipswich suburb
  names — the primary suburb join key**, clean and structured, no free-text
  guessing), `projectCategoryArray` (one category has an escaped slash,
  `Waste/Resource Recovery`). Verified 17 Jul 2026: 120 top-level projects
  (9 Open, 23 Active, 88 Closed). 22 of them are tagged with all ~81 LGA suburbs
  (i.e. "citywide") — faithful, so kept; the mentions cap (50) stops any one
  suburb page ballooning.
- **Enumeration: use this feed, NOT the sitemap or the HTML cards.** The card
  markup (`data-project-location` / `data-project-category` as entity-encoded
  JSON) is a fallback if the feed ever dies. The homepage lists only ~22
  *featured* projects. `sitemap.xml` returns 544 locs, but ~408 of them are
  project sub-pages (news updates, event/pop-up RSVP pages, survey tool pages)
  and business-workshop blog posts — **not** top-level consultations. The
  load-more feed is the authoritative top-level list; the sitemap is only a
  cross-check.
- **Invariant 8 (no UGC) — why listing-only, never the project pages.**
  EngagementHQ project pages are built around resident surveys, comments,
  guestbooks, forums and contributions (a single page carries dozens of
  `comment`/`survey`/`contribution`/`moderation` markers). This listing feed is
  the one surface that exposes *only* Council-authored project metadata, so it's
  both sufficient and the safe choice. And fetching the page wouldn't even help:
  a project page's only clean Council-authored text is its `<meta
  og:description>`, which is byte-for-byte the `projectDescription` already in
  the feed. `normalise_project()` whitelists fields (never passthrough) so no
  future UGC field can leak through.
- Status wording is Council's own (`Open`/`Active` = taking input, `Closed` =
  archived); reproduced verbatim, never characterised.

### Development.i — development applications (scrape/development_applications.py)

Council's own DA portal ("Development.i", an ASP.NET Core app):
`https://developmenti.ipswich.qld.gov.au`. No robots.txt (404). This is a
**link + factual-metadata layer only** — see Invariant 7 for what we surface and
the long list of what we deliberately don't.

- **Applications endpoint:** `POST /Geo/GetApplicationFilterResults`,
  `Content-Type: application/json`, header `X-Requested-With: XMLHttpRequest`.
  Returns a GeoJSON `FeatureCollection`.
- **Anti-forgery gate (the main gotcha).** The POST returns **500** unless you
  first `GET /` to receive the `.AspNetCore.Antiforgery._*` cookie AND read the
  hidden `<input name="__RequestVerificationToken">` from the home HTML, then
  send that token in a `RequestVerificationToken` request header (with the
  cookie) on the POST. httpx's `Client` keeps the cookie automatically; the
  token is scraped from the HTML. No login/account needed.
- **Body schema quirk:** the working body uses `SortField:"submitted"` and
  `SortAscending` (a **bool**), NOT `SortDirection`. It also carries
  `BBox/PixelWidth/PixelHeight`. Sending `SortDirection` (as an early capture
  suggested) 500s. `IncludeDA:true, IncludeBA:false, IncludePlumb:false` =
  development applications only. `Progress:"all"` spans In Progress + Decided +
  Past. Pagination via `PagingStartIndex` + `MaxRecords` (200/page).
- **Enumeration:** `GET /Geo/GetLocality` returns a FeatureCollection of the ~82
  suburb polygons; each `feature.id` is the gazetted locality name. The geo
  features carry **no locality field**, so iterate the localities and set
  `LocalityId` per request — that scopes the query and hands you the suburb
  bucket for free (no geocoding). Verified: a whole-LGA `ViewPort` with
  `LocalityId` set returns the same result as `ViewPort:null`, so we send
  `ViewPort:null` and just vary `LocalityId`. The geo layer returns one feature
  per land parcel, so a multi-parcel application repeats — **de-dupe by
  `application_number`**.
- **COMPLETENESS — read this.** `GetApplicationFilterResults` is the **map
  layer** and returns only a small **mapped subset** of each locality's register
  — roughly **4%**. Measured: Swanbank returns ~14–15 unique apps via geo, but
  Council's own list endpoint (`POST /Home/ApplicationTileSearch`, same body,
  returns HTML tiles) reports **345** for Swanbank, 46 for Amberley, 954 for
  Brassall, **27,558 citywide**. The geo count also fluctuates slightly per call
  (22↔25 features). We ship the geo/mapped set faithfully, framed as Council's
  *mapped* applications with a prominent link to the full Development.i register,
  and **never assert a total DA count** on any page. If the product ever wants
  the complete register, switch to `ApplicationTileSearch` (HTML tiles, carries a
  real "N of M applications" total) or the CSV export
  (`POST /Home/ApplicationFilterCSVPaged`) — both are heavier (HTML/CSV parsing,
  ~27k rows) and were out of scope for the link-layer.
- **Validation case (pinned in tests):** `LocalityId:"Swanbank", Progress:"all"`
  includes `application_number` **"12285/2026/MCU"**, description **"Material
  Change of Use - Warehouse (Data Centre)"**, `progress` "In Progress".
- **Fields on `feature.properties`** (confirmed): `pdonline_id,
  application_number, description, progress, date_received, date_determined,
  application_type, assessment_level, uselevel1, uselevel2, land_no,
  category_desc, group_desc, group_code, submissionindicator, publicnotification,
  project_officer, decision_desc, appeal_result, ...`. We WHITELIST to
  `application_number, pdonline_id (as id), description, progress→status,
  application_type, assessment_level, date_received, suburb (=LocalityId),
  coords`. **Never emitted:** `project_officer`, `decision_desc`,
  `appeal_result`, `submissionindicator`, `publicnotification` (Invariant 7).
- **Per-application Council permalink:** there is **no full-page** detail route
  (`/Application/ApplicationDetails/{id}` etc. all 404). The site opens details
  via a modal AJAX partial:
  `GET /Home/ApplicationDetail?type=plan_development_apps_unique&id={application_number}`
  (application number URL-encoded) — a working 200 that renders the specific
  application. That's the "go to Council for the detail" pointer we deep-link to;
  it's chrome-less (a modal fragment) but it's Council's own authoritative view
  of that application. The `type` value is the record-type the result tiles use
  (`data-record-type`), not `development`.
- **Address:** not in the list feed (only `land_no` + point `coords`).
  `GET /Geo/GetPropertyDetailsByLandNumber` could enrich, but that's one request
  per DA — skipped. DAs are suburb-bucketed via `LocalityId`, which is enough.

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
- **Sitemap** is regenerated on every build (7.4k+ URLs, with `lastmod`).

### Search-engine indexing — receipts (2026-08-21)

The site had been live since 2026-07-15 but **nothing was indexed** — `site:ipswichfacts.au` returned zero pages, and Search Console showed **0 indexed / 0 clicks**. Root cause: the sitemap was **never submitted to Search Console**. Ownership was verified (the `google-site-verification` meta tag ships on every page), but verification ≠ submission — Google was never handed the URL list, so it only ever found the homepage (via a Facebook-group backlink) and left it "Crawled – currently not indexed" (normal caution for a new, low-authority domain). Fixed on 2026-08-21:

1. **Submitted `https://ipswichfacts.au/sitemap.xml`** in Search Console (Indexing → Sitemaps). This is the load-bearing fix. It's a one-time human action and **self-sustaining** — Google re-fetches a submitted sitemap on its own schedule forever, using `lastmod` to spot changes. It cannot silently lapse the way it did, so there is deliberately **no sitemap-ping step in CI for Google**: Google retired the `google.com/ping?sitemap=` endpoint in 2023, and adding it would be cargo-cult.
2. **Requested indexing** of the homepage (URL Inspection → Request Indexing) to priority-queue the front page.
3. Expect indexing to build over **days-to-weeks** — a 5-week-old domain with ~one backlink gets a conservative crawl budget. The single highest-leverage accelerant now is **real backlinks** (Council open-data directory, local media, a Facebook/Reddit post), not more GSC buttons.

- **IndexNow** (`INDEXNOW_KEY` in `build_site.py`) covers **Bing, Yandex, DuckDuckGo, Seznam — not Google** (Google doesn't participate). The build emits `/{key}.txt` and the deploy workflow's `notify` job pings IndexNow with the homepage after each deploy; the engines follow the sitemap from there. Submitting only the homepage keeps the ping non-spammy. Set `INDEXNOW_KEY = None` to disable both halves.
- **Re-check the sitemap status in a day or two** — right after submission it read "Couldn't fetch" with an empty *Last read*, which is the normal not-yet-fetched state (the sitemap itself serves clean: 200, valid XML, robots allows it). If it's still failing after Google has actually attempted a read, that's a real signal.

## Attribution and legal

- Council content is CC BY 4.0. Attribution reads: `Source: [name of Council page](URL) (CC BY 4.0)` at the bottom of every derived page.
- Site framing on every footer: "Unofficial. Council's own systems are the source of truth."
- No user-submitted content in v1 → no defamation surface.
- If asked to remove: comply with a request to correct a factual reproduction error, escalate anything else to a decision about the site's mission before acting.
