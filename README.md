# Ipswich Facts

Public Ipswich City Council data — projects, closures, mentions — joined up and made searchable, then published as a plain static site so Google can index the joined view.

Status: **working prototype**. Five data sources plumbed end-to-end (Civic Projects + live road closures + Council meeting agendas/minutes + Ipswich First media releases + Capital Works Program budgets). Everything else on the roadmap is a bolt-on that follows the same shape.

## What it is

Ipswich Council publishes a lot of information across half a dozen unconnected systems. A resident who wants to know "what's happening on my street, who approved it, when, and how much is it costing" currently has to open five tabs and reconstruct the picture themselves. Ipswich Facts pulls the raw data from Council's own systems, joins it by street/suburb/project, and republishes it as a single searchable site with a page per entity.

Each page ships as pre-rendered HTML so Google can index it directly. A small client-side widget hydrates against JSON files for live filtering and drill-down.

## Quick start

```
git clone <your-repo> ipswichfacts
cd ipswichfacts
make install       # pip install httpx + pdfplumber
make sample        # build the site from the checked-in sample data
make serve         # open http://localhost:8000
```

To build from live Council data (requires network):

```
make scrape        # pull projects + closures from Ipswich Council
make build         # generate the site
make serve
```

## Data sources verified end-to-end

| Source | Endpoint | Records | Refresh |
|---|---|---|---|
| Civic Projects (map) | `https://maps.ipswich.qld.gov.au/icc/data/Projects_Infrastructure.JSON` | ~385 GeoJSON features (~780 KB) | Daily |
| Live road impacts (Council-managed) | `https://traffic.ipswich.qld.gov.au/dashboard/imsRoad` | GeoJSON FeatureCollection, JSON-string-wrapped, often empty | Real-time |
| Live road impacts (QLDTraffic proxy) | `https://traffic.ipswich.qld.gov.au/dashboard/tmrRoadData` | Bare array of GeoJSON Features, double-JSON-encoded | Real-time |
| Council meetings (agendas + minutes) | `https://ipswich.infocouncil.biz/` index → `Open/YYYY/MM/*_WEB.htm` framesets | ~100 meetings/year, per-item text + resolutions; 2020–2025 archive committed in `data/archive/` | Daily (current year); archive scraped once |
| Ipswich First media releases | `https://www.ipswichfirst.com.au/wp-json/wp/v2/posts` (WordPress REST API) | ~4,900 posts back to 2017, plain-text body + categories; 2017–2025 archive committed in `data/archive/` | Daily (current year); archive scraped once |
| Capital Works Programs | One PDF per budget cycle, linked from `.../Corporate-Publications/Budget-YYYY-YYYY`; parsed with `pdfplumber` | ~380–470 projects/cycle with funding by financial year; 2023-2024 → 2026-2027 committed in `data/capital_works/` (2026-27 marks funded years with ● instead of amounts) | Yearly (`make backfill-capworks` when the new budget drops) |

Endpoints were discovered by inspecting the Council apps' network traffic; both return plain JSON (once double-decoded for the traffic feed) and can be scraped with `httpx`.

## Roadmap (not yet built)

Same shape as above, just more sources:

- **Shape Your Ipswich** — Granicus EngagementHQ. HTML scrape per project page.

Add each as a new file in `scrape/`, extend the `build_site.py` graph to consume it, generate more page templates. No architectural changes required.

## Project layout

```
ipswichfacts/
├── README.md                        # you are here
├── Makefile                         # make sample | scrape | build | serve
├── requirements.txt                 # httpx + pdfplumber
├── scrape/
│   ├── civic_projects.py            # Civic Projects Map JSON → data/projects.json
│   ├── road_closures.py             # Traffic dashboard feeds → data/closures.json
│   ├── council_meetings.py          # infocouncil business papers → data/meetings.json
│   ├── ipswich_first.py             # Ipswich First WP REST API → data/news.json
│   ├── capital_works.py             # Capital Works Program PDFs → data/capital_works/
│   └── extract_mentions.py          # gazetteer-based place-name NER
├── build/
│   └── build_site.py                # emits site/ from data/*.json
├── data/
│   ├── sample/                      # committed sample data so the site builds without network
│   │   ├── projects.json            # 14 real projects observed via Council's map
│   │   ├── closures.json            # 5 real active closures observed 15 Jul 2026
│   │   ├── meetings.json            # 3 real meetings observed 15 Jul 2026
│   │   └── news.json                # 5 real Ipswich First posts observed 15 Jul 2026
│   ├── capital_works/               # committed: one file per budget cycle (2023-2024 → 2026-2027)
│   ├── projects.json                # (generated) full projects dump
│   ├── closures.json                # (generated) snapshot of active impacts
│   ├── meetings.json                # (generated) full meetings dump (current year)
│   └── news.json                    # (generated) Ipswich First posts (current year)
└── site/                            # (generated) shippable static site
```

## Site structure

Every entity gets its own URL:

- `/` — search + summary + active closures
- `/project/<slug>/` — one page per project (title, description, status, dates, division, links, related streets/suburbs, capital works funding per budget cycle)
- `/street/<slug>/` — every project, closure and Council meeting mention on that street
- `/suburb/<slug>/` — every project, closure and Council meeting mention in that suburb
- `/meeting/<slug>/` — one page per Council meeting (per-item text, resolutions, links back to the business paper)
- `/news/<slug>/` — one page per Ipswich First media release (plain-text body, categories, link back to the original article)
- `/news/` — news index (two most recent years, plus per-year pages `/news/YYYY/` for older articles)
- `/capital-works/` — every Capital Works Program by budget cycle: program totals and grand totals
- `/capital-works/<cycle>/` — one page per budget cycle: every project row with funding by financial year, linked to project pages and to the exact PDF page
- `/division/<n>/` — each Council division: its two councillors (with contacts) and every project in the division
- `/councillors/` — the Mayor and all eight councillors
- `/projects/`, `/suburbs/`, `/streets/`, `/meetings/` — index pages
- `/about/` — about + attribution
- `/data/*.json` — the widget's data files (client-side hydration)
- `/sitemap.xml`, `/robots.txt` — SEO plumbing

~6,700 pages from the sample data plus the committed meeting and news archives; the full live dataset builds to ~7,400 pages, well within any free-tier host's file limits.

## Tip jar

There's a "Buy me a coffee" link in the footer and a support section on the About page. Configure it at the top of `build/build_site.py`:

```python
COFFEE_URL = "https://buymeacoffee.com/your-handle"
COFFEE_LABEL = "Buy me a coffee"
```

Set `COFFEE_URL = None` to hide it entirely. Buy Me a Coffee, Ko-fi (0% platform fee for the basic tier), and GitHub Sponsors all work.

## Hosting

Zero-cost options:

- **Cloudflare Pages** — free tier, brotli, watch the 20K file limit
- **GitHub Pages** — free for public repos, no file limit
- **Netlify** — free tier fine at this volume

Deployment is a matter of `git push` → build via a GitHub Actions workflow → publish the `site/` directory. See `.github/workflows/build-and-deploy.yml` — it scrapes fresh data and redeploys daily on a cron, plus on every push to `main`. Enable it in the repo settings under **Settings → Pages → Source: GitHub Actions**.

Note: pages use root-relative URLs, so the site must be served from a domain root (a custom domain, or a `<user>.github.io` root repo) — it will not render correctly from a `github.io/<repo>/` subpath.

## Licence and attribution

- Council data is published under [CC BY 4.0](https://www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications/Legal-Disclaimer-and-Copyright-Notice).
- Every page in the generated site attributes the Council source with a direct link back.
- This project's own code: MIT.
- Site framing: "Unofficial. Council's own systems are the source of truth." on every page footer.

## Design notes

- **Static-first.** All content is baked into HTML at build time so Google indexes every entity. The widget adds interactivity but not information — a JS-disabled browser still sees the full content.
- **Widget-hydrated.** ~500-line vanilla-JS widget (no framework, no build step) hydrates against chunked JSON in `/data/`. Search runs in the browser.
- **Faithful to Council data.** No editorial. Records are reproduced verbatim with attribution.
- **Cheap to maintain.** Scrapers are small, idempotent, and independent — if one breaks the others keep updating. Total cost floor is a domain (~$15/year).

## Anti-goals

- Not a comments platform. Not a submissions channel. Not a place for editorial spin. Not a Council watchdog blog. If those exist they should live on separate domains.
- Not a replacement for PlanningAlerts — DA data is out of scope; link out.

## Contact

If Ipswich City Council's digital team wants to talk about the scraping, cadence, or attribution, open an issue on the repo.
