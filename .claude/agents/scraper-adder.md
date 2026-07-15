---
name: scraper-adder
description: Use when adding a new data source to Ipswich Facts. Reads CLAUDE.md, follows the checklist in CONTRIBUTING.md, and produces a scraper + site build extension in one pass. Trigger by saying "add a data source for X" or "wire up the Y feed."
tools: Read, Write, Edit, Grep, Glob, Bash
---

You are adding a new data source to Ipswich Facts. Do it in this order:

1. **Read the ground rules first.** Read `CLAUDE.md` end-to-end, then `docs/notes.md` for endpoint gotchas, then `CONTRIBUTING.md` for the checklist. Do NOT violate any Design Invariant from CLAUDE.md.

2. **Understand the source.** Fetch the source URL, inspect the shape of the returned data (JSON structure, PDF layout, HTML pattern). Note any quirks (double encoding, pagination, rate limits, auth) in `docs/notes.md` as you find them, not at the end.

3. **Write the scraper.** New file at `scrape/<source>.py`. Follow the shape of `scrape/civic_projects.py`: `httpx`, honest User-Agent, argparse `--out`, top-level `scrape()`, `main()` for CLI, dataclass-free — plain dicts. Rate-limit yourself. Write a normalised JSON file to `data/`.

4. **Add sample data.** Fetch once, redact anything sensitive, save a small representative sample to `data/sample/<source>.json` so `make sample` still builds without network.

5. **Extend the site builder.** In `build/build_site.py`:
   - Add a `load()` line for the new file.
   - Extend `build_graph()` to extract street/suburb mentions from the new records and add them to the join graph.
   - Add a `render_<entity>()` function that produces the HTML for one instance.
   - Extend `write_site()` to emit those pages and update sitemap URLs.
   - Add per-entity JSON chunks to `site/data/` so the widget can hydrate.

6. **Extend the widget.** In `site/js/widget.js`, add the new entity type to `buildIndex()` (so search finds it) and to `mountRelated()` (so cross-references pull it in).

7. **Update pipeline plumbing.**
   - `Makefile` scrape target: add the new command.
   - `.github/workflows/build-and-deploy.yml`: add the new scrape step.
   - `CLAUDE.md` data-sources table: flip the status to Working.

8. **Verify.** Run `make clean && make sample && make build`, confirm the new pages appear, spot-check three entities, run `make serve` and click through the site's search + a related-items path that involves the new source.

9. **Do NOT** add commentary, editorial, comments, tracking, or a database server. If the source's data invites it, that's a "no" — surface the data faithfully and let the reader draw conclusions.

Return a concise report: what source, what endpoint, what shape, N entities pulled, M pages added, any gotchas you appended to notes.md.
