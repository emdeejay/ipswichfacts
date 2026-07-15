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

### Council Business Papers — TODO

`https://ipswich.infocouncil.biz/`

- Meeting index at `/` (with filters for Meeting type, Year, Month).
- Per-meeting page: `/Open/YYYY/MM/{COMMITTEE_CODE}_YYYYMMDD_{AGN|MIN}_XXXX_AT.htm`.
- Attachments: `/open/YYYY/MM/{committee}_YYYYMMDD_{agn|min}_XXXX_at_files/*_attachment_XXXXX_X.pdf`.
- Full-text search endpoint exists (`SearchResults.aspx`) but is powered by Cludo. Prefer crawling the meeting index and extracting text from HTML+PDFs ourselves — avoids being rate-limited by a third-party search service.

Committee codes observed:
```
CO      Council
IPAAC   Infrastructure, Planning and Assets Committee
GIW     Growth Infrastructure and Waste Committee (historical)
GC      Governance Committee (historical)
FGC     Finance and Governance Committee
...     (see the filter dropdown on the meeting index)
```

### Capital Works Program PDF — TODO

Annual PDF drops under `Corporate Publications > Budget > Budget YYYY-YY > Annual Plan`. Stable URL pattern:

```
https://www.ipswich.qld.gov.au/files/assets/public/v/1/about-council/media-and-publications/corporate-publications/budget/budget-YYYY-YY/annual-plan/*.pdf
```

Also linked from `About-Council/Initiatives/Works-and-Projects`. Table extraction: `camelot-py` handles the multi-column financial-year layout better than `pdfplumber` for these.

### Ipswich First (WordPress) — TODO

```
https://www.ipswichfirst.com.au/wp-json/wp/v2/posts?per_page=100&page=N
```

Standard WP REST API. Paginate. `X-WP-TotalPages` header tells you when to stop.

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
