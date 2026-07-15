# Contributing

## The primary way to help

Add a data source. The architecture is designed so this is the main path of growth. See `CLAUDE.md` → "How to add a new data source". The current backlog, roughly in priority order:

1. Council meeting agendas + minutes from `ipswich.infocouncil.biz` — the most valuable source, because it's where decisions actually happen.
2. Capital Works Program PDFs — funding-source attribution per project across financial years, cost escalation over budget cycles.
3. Ipswich First media releases via the WordPress REST API — trivial.
4. Councillor profile pages — fills out the division → councillor → contact resolution.
5. Shape Your Ipswich consultation records — quieter but useful.

## Ground rules

Before you write code, read `CLAUDE.md` (particularly the "Design invariants" list). The rules exist to keep the project cheap, defensible, and useful long-term.

- Reproduce data faithfully; do not editorialise.
- Every republished item must attribute the Council source and link back.
- No feature that requires a paid tier of any service.
- Rate-limit scrapers; set an honest User-Agent.
- No user-generated content in v1.
- Don't scrape DA data — link to PlanningAlerts.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make sample          # build from checked-in sample data
make serve           # http://localhost:8000
```

For live data (network required):

```bash
make scrape
make build
make serve
```

## Adding a scraper — checklist

- [ ] File in `scrape/<source>.py` with a top-level `scrape()` function and a `main()` entry point.
- [ ] Uses `httpx`. Sets `User-Agent: ipswichfacts-scraper/0.1 (+https://ipswichfacts.au)`.
- [ ] Rate-limits itself.
- [ ] Writes to a normalised JSON file with a clear top-level shape.
- [ ] Docstring describes the source URL, format, refresh cadence, and any gotchas.
- [ ] `Makefile` scrape target updated.
- [ ] `.github/workflows/build-and-deploy.yml` scrape step updated.
- [ ] `CLAUDE.md` data-sources table updated (flip status to Working).
- [ ] `docs/notes.md` updated with any endpoint quirks worth remembering.
- [ ] `build/build_site.py` extended to consume the new file, extract mentions, render pages, emit widget data.
- [ ] `site/js/widget.js` extended to include the new entity type in `buildIndex()` and `mountRelated()`.
- [ ] Sample data added under `data/sample/` so `make sample` still works offline.
- [ ] Site rebuild produces new pages without regressions.

## Reporting problems

Open an issue. Please include:
- What you searched for and expected.
- What Ipswich Facts returned.
- What Council's own system returns (so we know whether it's a scrape bug or a data change).

If it's a data reproduction error (we've misrepresented what Council actually said), tag it `data-fidelity` — those get fixed with priority.

## Non-goals

Do not open PRs that:
- Add a comments/discussion/forum feature.
- Add editorial commentary on Council decisions.
- Introduce a runtime backend or database server.
- Add tracking/analytics beyond a privacy-preserving hit counter (and even that is debatable).
- Duplicate PlanningAlerts.
