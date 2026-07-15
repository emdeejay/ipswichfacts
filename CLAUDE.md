# CLAUDE.md — project memory for Ipswich Facts

Read this before doing anything. It's the shortest possible brief that will let you make good decisions without asking the user to relitigate them.

## What this is

A public-service mirror of Ipswich City Council data — projects, road closures, meeting decisions, media releases — joined by street/suburb/project and republished as a static site so Google can index the joined view. Council publishes this information across ~6 unconnected systems; this project stitches them together and puts one URL per entity.

The whole point is discoverability: the site "wins" when a resident Googles their street and lands on our page instead of five broken Council pages.

## Design invariants — do NOT violate these without checking with the user

1. **Static-first.** The site ships as pre-rendered HTML. A JS-disabled browser must see all the substantive content. The widget adds interactivity, not information.
2. **Zero runtime backend.** No database server, no serverless functions, no API. Just HTML + JSON + a bit of JS. Total infrastructure cost floor: a domain.
3. **Free hosting.** GitHub Pages, Cloudflare Pages, or Netlify free tier. No paid tier assumed.
4. **Faithful reproduction, no editorial.** Data is reproduced verbatim with attribution and a link back to the Council source. This site is not a Council watchdog blog. If editorial content is wanted, it lives on a separate domain.
5. **Unofficial framing.** Every page footer says "Unofficial. Council's own systems are the source of truth."
6. **CC BY 4.0 attribution** on all republished content. Council data is CC BY 4.0; keep the attribution.
7. **PlanningAlerts owns DAs.** Do not scrape or republish development applications. Link out to openaustralia.org.au/planningalerts instead.
8. **No user-generated content.** No comments, no submissions, no forms in v1. Removes the entire defamation surface.
9. **Rate-limit scrapers.** ≤1 req/sec per host. Set a User-Agent that names the project and contact email so Council can find us.
10. **The name is `ipswichfacts`.** Not "IpswichWatch" or anything else that reads as activist. Framing matters.

## Architecture in one paragraph

`scrape/*.py` pull JSON from Council endpoints and write normalised JSON to `data/*.json`. `build/build_site.py` reads those JSON files, extracts street/suburb mentions via regex, builds a graph of entities, and emits one static HTML page per entity into `site/`, plus a sitemap and per-entity JSON chunks in `site/data/` that the vanilla-JS widget (`site/js/widget.js`) hydrates against for on-site search and cross-references. Two GitHub Actions workflows deploy to GitHub Pages: the daily full run (all scrapers, caches meetings data) and an hourly closures refresh (traffic feeds + projects only; meetings come from the daily run's cache so infocouncil is never hit hourly).

## Data sources — status

| Source | Endpoint | Status |
|---|---|---|
| Civic Projects Map | `maps.ipswich.qld.gov.au/icc/data/Projects_Infrastructure.JSON` | **Working** — `scrape/civic_projects.py` |
| Live road impacts (Council + QLDTraffic) | `traffic.ipswich.qld.gov.au/dashboard/{imsRoad,tmrRoadData}` | **Working** — `scrape/road_closures.py`. Note both feeds are double-JSON-encoded (JSON string containing JSON). |
| Council meeting agendas/minutes | `ipswich.infocouncil.biz/Open/YYYY/MM/{COMMITTEE}_YYYYMMDD_{AGN|MIN}_XXXX[_AT][_EXTRA]_WEB.htm` | **Working** — `scrape/council_meetings.py`. WEB.htm docs are framesets; fetch the frameset for the real BMK/paper frame names. See docs/notes.md. Historical years via `?year=YYYY` on the index: 2020–2025 backfilled once (`make backfill`, 2s delay) into committed `data/archive/meetings-YYYY.json` — never re-scraped; the daily cron fetches only the current year. |
| Capital Works Programs | `ipswich.qld.gov.au/.../budget/*/annual-plan/*.pdf` | Not yet built. Extract funding tables with `camelot` or `pdfplumber`. |
| Ipswich First media releases | `ipswichfirst.com.au/wp-json/wp/v2/posts` | Not yet built. WordPress REST API. |
| Shape Your Ipswich consultations | `shapeyouripswich.com.au/<project-slug>` | Not yet built. Granicus EngagementHQ. HTML scrape. |
| Councillor profiles | `ipswich.qld.gov.au/About-Council/Mayor-Councillors/*` | **Working** — `scrape/councillors.py`. One-off; output committed as `data/councillors.json`. Re-run after each election (next: 2028). Feeds `/councillors/` and `/division/<n>/` pages. |

## How to add a new data source (the main growth path)

1. Write `scrape/<source>.py` that produces a normalised JSON file in `data/`. Follow the shape of the existing scrapers: `httpx`, sensible User-Agent, argparse for `--out`, a top-level `scrape()` function, `main()` for CLI.
2. Extend `build/build_site.py`:
   - Load the new file in `load()`.
   - Extend `build_graph()` to extract street/suburb mentions and hang the records off the graph.
   - Add a render function (`render_meeting`, etc.) and a template.
   - Extend `write_site()` to emit the new pages and update sitemap URLs.
   - Update the widget data files in `site/data/` so the client hydration knows about the new entity type.
3. Extend `site/js/widget.js` to include the new entity type in `buildIndex()` and `mountRelated()`.
4. Add an entry to the "Data sources — status" table in this file. Flip status to Working.
5. Update `.github/workflows/build-and-deploy.yml` to include the new scrape step.

## Repo layout

```
ipswichfacts/
├── CLAUDE.md                        # this file — project memory
├── README.md                        # user-facing docs
├── LICENSE                          # MIT (code) + CC BY 4.0 note (data)
├── Makefile                         # make sample | scrape | build | serve
├── requirements.txt                 # httpx only
├── docs/
│   ├── scoping.md                   # original scoping doc
│   ├── notes.md                     # findings, decisions, receipts
│   └── why-this-exists.md           # origin story (frustration → tool)
├── scrape/                          # data pullers, one file per source
├── build/build_site.py              # static-site generator
├── data/sample/                     # committed sample data (builds offline)
├── site/                            # (generated) static site
├── .github/workflows/               # scrape + deploy on cron
└── .claude/                         # settings + agents for Claude Code sessions
```

## Common commands

```bash
make install       # pip install httpx
make sample        # rebuild site from checked-in sample data (no network)
make scrape        # pull fresh data from Council (requires network)
make build         # regenerate site/ from data/
make serve         # local preview at http://localhost:8000
make clean         # wipe generated site and data (keeps data/sample)
```

## Coding notes

- Python 3.10+; the CI runs on 3.12.
- Only external runtime dep is `httpx`. PDF-parsing sources (when added) will add `pdfplumber` or `camelot`.
- No frameworks in the site. Vanilla JS. If that stops scaling, the next step is **not** React — it's swapping the JSON-chunks approach for `sql.js-httpvfs` (SQLite in the browser over HTTP Range).
- Slugs are lowercase-kebab-case, max 80 chars, derived from Council-supplied names so URLs stay stable across rebuilds.
- Don't rename URL slugs once published — they get indexed by Google. If a Council-supplied name changes, keep the old slug and 301 redirect via a static `_redirects` file (Cloudflare Pages / Netlify) or a small `<meta http-equiv="refresh">` shim.
- Tip jar is configured at the top of `build/build_site.py` via `COFFEE_URL` and `COFFEE_LABEL`. Point at Buy Me a Coffee, Ko-fi, GitHub Sponsors, or wherever. Set `COFFEE_URL = None` to hide the coffee link and About-page section entirely.

## What NOT to build

- A comments/discussion feature.
- A user account system.
- A Council watchdog blog with editorial commentary.
- A DA (development application) scraper — PlanningAlerts covers this.
- A native app.
- Anything that requires a paid tier of any service.
- Anything that inflates the site into thousands of tracker-laden pages. Every page should stay fast and boring.

## Contact and escalation

If Ipswich City Council pushes back on the scraping or attribution, first-contact posture is polite and cooperative: attribution is intact, licence is respected, rate limits are generous, and the intent is clearly civic. If it gets legal, the fallback is CC BY 4.0.
