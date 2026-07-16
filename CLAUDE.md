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
| Capital Works Programs | Budget page per cycle (`ipswich.qld.gov.au/.../Corporate-Publications/Budget-YYYY-YYYY`) links one capital-works PDF | **Working** — `scrape/capital_works.py`. Parsed with `pdfplumber` `extract_words()` + x-coordinate clustering (the tables have no ruling lines; `extract_tables()` returns garbage). Note the 2026–27 cycle publishes ● dots per project instead of dollar amounts — reproduce faithfully, never invent amounts. Output committed to `data/capital_works/` (re-run `make backfill-capworks` each June/July); not in the daily cron. 2021-22/2022-23 PDFs use an older layout the parser can't do reliably — skipped. |
| Ipswich First media releases | `ipswichfirst.com.au/wp-json/wp/v2/posts` | **Working** — `scrape/ipswich_first.py`. WP REST API; content is Divi-shortcode-wrapped HTML (see docs/notes.md). 2017–2025 archive committed in `data/archive/`; daily scrape covers the current year. |
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
6. Add parser tests in `tests/` with a trimmed real response in `tests/fixtures/`, and a floor in `MIN_EXPECTED` (`build/build_site.py`) so a silently-empty scrape can't reach production. See "Don't publish a gutted site" below.

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

## Comparing budget figures across programs

Each Capital Works Program covers a **rolling three-year window**, so the same
financial year is published in up to three successive programs — and the
figures don't always match. Setting those side by side (`/capital-works/revisions/`,
and the matrix on project pages) is the one thing here Council's own systems
can't show, because each program is a separate PDF.

Two rules, both load-bearing:

1. **Only ever compare the same financial year across programs.** A project's
   *three-year total* covers a different window each cycle, so a project that's
   nearly finished looks identical to one that's been cut. Comparing totals
   would be actively misleading.
2. **Reproduce, don't characterise** (invariant 4). Show what each program
   printed, link to the page it's printed on, and stop. No percentages, no
   "blowout", no verdict. Two numbers next to each other is journalism the
   reader does themselves; the moment we editorialise it belongs on another
   domain.

Also: never render compared amounts with `fmt_kdollars` — it rounds, so
$2,450k and $2,500k both print "$2.5M" and a real revision looks like our bug.
`fmt_dollars_exact` exists for this and a test enforces it.

## Don't publish a gutted site

The dangerous failure here is not a crash. Every scraper parses HTML or PDFs
Council can restructure without notice; an HTTP error raises and fails the
workflow (safe — the last good deploy stays up), but a *silent* change (200 OK,
different markup) makes a parser match nothing. Because the scrapers skip bad
items rather than dying, that yields a complete-looking, empty dataset — and a
build that cheerfully replaces thousands of indexed pages with nothing. Google
then de-indexes them for weeks.

Two defences, keep both working:

1. **`--strict`** (both workflows use it): `MIN_EXPECTED` in `build/build_site.py`
   sets a floor per dataset. Below it, the build refuses to write anything and
   exits 1. Floors are deliberately generous — they detect breakage, not
   fluctuation. Raise them as the archives grow. Closures are deliberately
   un-floored: an empty traffic dashboard is a real state.
2. **`tests/`** (`make test`, and CI gates the deploy on it): parsers pinned
   against trimmed real responses in `tests/fixtures/`. For the capital works
   PDFs the arithmetic assertions matter most — if x-column clustering drifts,
   amounts land in the wrong financial year and look plausible; they stop
   summing to Council's printed totals, and the tests catch it.

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
