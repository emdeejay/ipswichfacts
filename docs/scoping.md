# "Ipswich Facts" — scoping doc

*Drafted 14 July 2026. A working document, not a pitch.*

## The problem, in one paragraph

Ipswich City Council publishes a large volume of information — road closures, capital works, council meeting decisions, budget documents, media releases, consultation records — across six or seven disconnected systems, none of which cross-link. A resident with a straightforward question ("what works are planned on my street, who approved them, how much are they costing, and where did the money come from") has to open five tabs, know which system holds which fragment, and reconstruct the picture themselves. The information is public but effectively unusable. The point of this project is to reproduce Council's own data, faithfully and attributed, joined together by street/suburb/project so a resident can answer that question on one page.

## MVP scope

Deliver a website that lets any resident type a street name, suburb name, or project name and see:

- Every currently active road closure or impact on that road.
- Every entry on Council's Civic Projects Map for that location — past, present, planned — with full detail (title, description, funding, phase of work, dates, links).
- Every Council or committee meeting item that has mentioned that location since April 2019.
- Every Ipswich First media release that has mentioned it.
- The divisional councillor and current contact.
- Direct links back to Council's source page for every item.

That single search-by-place capability is the whole v1. Everything else is v2 or later.

**Explicitly out of scope for v1:**

- User accounts, subscriptions, email alerts (v2).
- DA / planning application feed — PlanningAlerts already covers this. Link to them.
- Councillor voting record extraction — requires minutes parsing quality that's a rabbit hole (v2).
- Historical council meetings pre-April 2019 (the archive exists but is inconsistently structured).
- Any editorial commentary. The site reproduces Council data; residents draw their own conclusions.

## Data sources and how to get them

| Source | System | Extraction approach |
|---|---|---|
| Civic Projects | ArcGIS (`maps.ipswich.qld.gov.au/civicprojects`) | Reverse-engineer the underlying `FeatureServer` REST endpoint. All project attributes come back as GeoJSON with `?where=1=1&outFields=*&f=geojson`. No pagination pain up to a few thousand features. |
| Live road closures | Leaflet dashboard (`traffic.ipswich.qld.gov.au`) | Inspect XHR — dashboard fetches a JSON feed on load. Pull that feed directly. Snapshot every 15–30 min. |
| Meeting agendas/minutes | InfoCouncil (`ipswich.infocouncil.biz`) | Static HTML per meeting under `/Open/YYYY/MM/{COMMITTEE}_YYYYMMDD_{AGN|MIN}_XXXX_AT.htm`. Crawl the meeting index page to build the URL list. Attachments live in a parallel `_files/` folder as PDFs. |
| Meeting attachment PDFs | InfoCouncil | Download + text-extract with `pdfplumber`. Chunk into per-item text keyed to the agenda structure. |
| Capital Works Program, Budget, Annual Report | Council CMS (`ipswich.qld.gov.au/.../*.pdf`) | Annual PDF drops with stable URL patterns under Corporate Publications. Table-extract with `camelot` or `tabula-py`. |
| Media releases | WordPress (`ipswichfirst.com.au`) | `wp-json/wp/v2/posts` REST endpoint. Paginate at 100/page. |
| Consultation records | Granicus EngagementHQ (`shapeyouripswich.com.au`) | HTML scrape per project. No formal API. Lower priority for v1. |
| Councillor / division data | Council CMS | One-off scrape of `/About-Council/Mayor-Councillors/*` pages. Refreshes at each election. |

The core insight: **everything worth pulling is either JSON already or a static HTML/PDF crawl.** No headless browsers required.

## Data model

SQLite for v1 (portable, versionable in git, FTS5 comes built in). Sketch:

```
projects        (id, ref_id, name, description, category, division,
                 phase, status, first_seen, last_updated, source_url)
project_years   (project_id, financial_year, amount_dollars, source_document)
project_geoms   (project_id, geometry_wkt, street_name, suburb)

closures        (id, road_name, suburb, impact_type, permit_number,
                 start_date, end_date, snapshot_at, active)

meetings        (id, committee_code, meeting_date, agenda_url, minutes_url)
meeting_items   (id, meeting_id, item_number, item_title,
                 recommendation_text, resolution_text, full_text)

documents       (id, source_type, url, published_date, title, extracted_text)

mentions        (id, source_type, source_id, entity_type, entity_value,
                 confidence)
                 -- polymorphic: source_type in [meeting_item, document,
                 --                              project, closure, article]
                 -- entity_type in [street, suburb, project_name,
                 --                 councillor, funding_source]

councillors     (id, name, division, term_start, term_end, email)
media_releases  (id, url, title, published_date, text, categories)
```

The `mentions` table is the join key that makes place-based search work. Entity extraction can start crude — a gazetteer of Ipswich street names and suburbs plus regex — and improve over time.

## Tech stack — static-first, widget-hydrated

The whole site ships as static HTML + a small JS widget that hydrates against pre-built JSON. No runtime backend. No database server. Every page is a real, Google-indexable URL, plus interactive drill-down for anyone who wants to explore.

- **Scrapers (build time):** Python 3.12, `httpx`, `pdfplumber`, `beautifulsoup4`, `camelot-py`. Run as GitHub Actions cron jobs (free tier).
- **Intermediate storage:** SQLite, built by scrapers, committed to the repo. This is a build cache, not a runtime component.
- **Site generator:** Astro (or Hugo / 11ty — pick during spike). Renders one static HTML page per entity: street, suburb, project, meeting item, media release, councillor.
- **Client-side data:** the same SQLite gets exported at build time as chunked JSON files — an index (`streets.json`, `projects.json`, `suburbs.json`) plus lazy-loaded detail chunks. Total payload ~3MB brotli.
- **Widget:** vanilla JS or Preact, ~20KB. Embedded on every entity page. Hydrates against the JSON chunks. Handles live filtering, cross-references, and queries that would otherwise combinatorially explode into too many pre-rendered pages ("all closures on any street in Division 3 in the last 12 months").
- **Search:** [Pagefind](https://pagefind.app/) builds a ~100KB search index from the generated HTML and runs entirely in the browser. Google indexes the same HTML for external discovery.
- **Hosting:** Cloudflare Pages, GitHub Pages, or Netlify — free tier. GitHub Pages if we blow past Cloudflare's 20K file limit; otherwise Cloudflare for the CDN.
- **Domain:** something neutral. `ipswichfacts.au` if available. Avoid names that read as activist — the framing is "public data, findable."

**If client-side queries get complex enough to warrant it:** swap the chunked-JSON approach for [sql.js-httpvfs](https://github.com/phiresky/sql.js-httpvfs) — the browser queries a shared SQLite file via HTTP Range requests, downloading only the pages a query touches. Not needed for v1 but the widget interface stays the same, so it's a drop-in later.

Total infrastructure cost: **$15/year** — just the domain. Everything else free tier.

## SEO — the discovery mechanism

Since the goal is "Google surfaces Council data to residents who search for it," SEO is a first-class concern, not an afterthought.

- **One canonical URL per entity.** Human-readable slugs. `/street/gordon-street-ipswich/`, `/project/marsden-parade-gordon-street-footpath/`, `/meeting/2026/06/16/infrastructure-planning-and-assets/`, `/councillor/marnie-doyle/`.
- **URL stability across rebuilds.** Slugs derived from Council's own identifiers where possible, so a URL still resolves in five years.
- **Proper `<title>`, `<meta description>`, `<h1>`** per page, generated from the entity's real content.
- **`sitemap.xml` per section**, submitted to Google Search Console. Auto-regenerated on build.
- **Schema.org structured data** — `GovernmentService` / `CivicStructure` / `NewsArticle` where each applies. Helps rich results.
- **Semantic HTML.** All the substantive information is in the initial DOM; the widget adds *interactivity*, not content. Google should be able to render the page with JavaScript disabled and still see everything that matters.
- **We are canonical for the joined view.** Individual data snippets attribute + link back to the Council source, but our page is the one Google indexes for the joined query. That's the whole point — otherwise Council's fractured pages win the search result.
- **`robots.txt` and sitemaps ping** on deploy so new content gets crawled promptly.

## Effort estimate

Two focused weekends for a shippable v1.

**Weekend 1 — Data:**
- Sat AM: Civic Projects ArcGIS endpoint → SQLite. Confirm we can get every project with all fields.
- Sat PM: Traffic dashboard feed → SQLite snapshots. Set up snapshot cadence.
- Sun AM: InfoCouncil meeting crawler → agendas + minutes HTML → SQLite. Item-level parsing.
- Sun PM: PDF pipeline (Capital Works Program, Budget statements). Ipswich First WordPress scraper.

**Weekend 2 — Site:**
- Sat AM: Astro scaffold. FTS5 search index build. Search page with results grouped by source type.
- Sat PM: Project detail page — everything about one project in one view (all CWP appearances showing funding trajectory, all meeting items that reference it, all media releases).
- Sun AM: Street/suburb landing page — combines all mentions across sources.
- Sun PM: Attribution/legal footer, source-link discipline pass, deploy.

Ongoing maintenance: ~1 hr/month if scrapers hold, more when Council changes something (twice a year, roughly).

## Legal / ethics posture

- Council content is [CC BY 4.0](https://www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications/Legal-Disclaimer-and-Copyright-Notice) — republish with attribution is explicitly allowed. Include a clear "Sourced from Ipswich City Council, [link], licensed CC BY 4.0" line on every derived item.
- Prominent "Unofficial. Council's own systems are the source of truth. This site reproduces public data more searchably." disclaimer in header and footer.
- Rate-limit scrapers to ≤1 req/sec per host. Set a `User-Agent` naming the project and contact email so Council can reach the maintainer easily. Respect `robots.txt` for anything not on the primary Council-published surfaces.
- No user-generated content in v1 — no comments, no submissions. This eliminates the entire defamation-adjacent risk surface.
- Editorial voice: **none.** The site reproduces and joins; readers judge. Any commentary lives in a separate blog domain if wanted.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Council changes URL structure | Loose scraping (find-by-pattern, not hard-coded IDs). Monitoring job that alerts when scrapes return zero results unexpectedly. |
| Cludo search endpoint blocks automated queries | Don't scrape their search — crawl the meeting index page directly, which lists every meeting. |
| ArcGIS query limits | Paginate with `resultOffset` / `resultRecordCount`. Fine at Ipswich's project volume. |
| Council perceives it as adversarial | Framing matters. This is data plumbing, not activism. First-contact approach: email the Council digital team politely, tell them what the site is, offer to preview it. Most Aus council IT teams are fine with well-behaved reuse of public data. If pushback comes, CC BY licence is the fallback. |
| Maintainer burnout | v1 must be small enough that abandoning it for a month doesn't break it. Scrapers should degrade gracefully — if one source breaks, others keep updating. |

## What v2 could add (not for now)

- Email/RSS subscriptions per street or suburb.
- Councillor voting record aggregation (parse minutes for divisions/votes).
- Project cost-escalation dashboard (roll-ups across CWPs showing budget vs. actual).
- Funding source attribution by cross-referencing Budget cash flow statements and Annual Report grants schedules.
- Cross-council expansion (same code, other SEQ councils; each is a data-source config file).

## Decision checklist for tomorrow

- [ ] Am I actually going to spend two weekends on this, or is this just venting?
- [ ] If I do, am I OK with maintaining it for at least 12 months?
- [ ] Am I comfortable with my name/email being the "contact" for Council if they call?
- [ ] Domain: `ipswichfacts.au` or similar — is it available?
- [ ] Do I want a co-maintainer before starting, or is a solo v1 fine?

## First actions if it's a go

1. Register the domain.
2. Set up a public GitHub org and empty repo.
3. **Prototype spike:** open the network tab on `maps.ipswich.qld.gov.au/civicprojects`, find the ArcGIS endpoint, curl it, confirm the data shape matches expectations. Ninety minutes. If that works cleanly, the rest of the plan is very likely to hold.
4. **Second spike:** same for `traffic.ipswich.qld.gov.au`. Another 30 minutes.
5. Only then commit the weekend.

## Files that would exist at end of v1

```
ipswichfacts/
├── scrape/
│   ├── civic_projects.py       # ArcGIS FeatureServer → projects table
│   ├── road_closures.py        # dashboard JSON → closures table
│   ├── infocouncil.py          # meeting index + agendas/minutes
│   ├── infocouncil_pdfs.py     # PDF text extraction
│   ├── capital_works.py        # annual CWP PDF → project_years
│   ├── budget.py               # budget statement PDF tables
│   ├── ipswich_first.py        # WP REST → media_releases
│   └── extract_mentions.py     # gazetteer-based NER over all text
├── build/
│   ├── export_json.py          # SQLite → chunked JSON for the widget
│   └── build_site.py           # SQLite → static HTML pages
├── data/
│   ├── ipswichfacts.db          # SQLite build cache (git-tracked)
│   └── gazetteer.yaml          # street names, suburbs, project aliases
├── site/                       # Astro (or Hugo/11ty)
│   ├── src/pages/
│   │   ├── index.astro          # front door + search
│   │   ├── street/[slug].astro  # street rollup + widget
│   │   ├── suburb/[slug].astro  # suburb rollup + widget
│   │   ├── project/[slug].astro # project rollup + widget
│   │   ├── meeting/[…].astro    # meeting item
│   │   ├── councillor/[slug].astro
│   │   └── media/[slug].astro
│   ├── src/widget/              # vanilla JS or Preact
│   │   └── OpenIpswich.tsx      # hydrates against /data/*.json
│   ├── public/data/             # chunked JSON emitted by export_json.py
│   │   ├── streets.json
│   │   ├── projects.json
│   │   ├── suburbs.json
│   │   ├── mentions.json
│   │   └── details/<id>.json
│   └── ...
├── .github/workflows/
│   ├── scrape.yml               # cron schedule per source
│   └── deploy.yml               # build + deploy on data change
├── README.md
└── ATTRIBUTION.md
```
