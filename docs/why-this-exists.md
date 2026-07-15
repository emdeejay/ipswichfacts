# Why this exists

*Written 14–15 July 2026. Preserved so contributors understand the origin.*

The project started because looking up straightforward Council information — "what roadworks are on my street?" — takes more clicks than it should. In one afternoon, checking two adjacent streets in Ipswich CBD (Gordon Street and Marsden Parade) required:

- Loading the traffic dashboard, which shows a list of currently-affected roads but no project detail, timeline, cost, or contact.
- Loading the Civic Projects Map (a separate URL on a separate map stack), typing the street name, clicking through a series of pins to find the actual project, then reading the pop-up.
- Cross-referencing the resulting project (e.g. INF03149 "Gordon Street Pedestrian Link") against the Capital Works Program PDF (buried four clicks deep under About Council → Initiatives) to find the funding profile.
- Cross-referencing against media releases on `ipswichfirst.com.au` and the Council Business Papers portal at `ipswich.infocouncil.biz` (a different subdomain, different search UX) to find any Council meeting decisions or announcements.
- Consulting the iGO Active Transport Action Plan PDF (via a fifth URL) to check strategic context.

Every one of those sources is public. Every one is worth reading. But no page in Council's own systems joins them. And the "Reference ID" that identifies a project on the Civic Projects Map (`INF03149`) doesn't appear anywhere in the Council Business Papers portal — the two systems don't share vocabulary.

The Council's Transparency and Integrity Hub is a real page, and the Mayor publicly leans on transparency as a brand. Council content is licensed CC BY 4.0. The information exists. The failure is purely on findability.

**The project's thesis, then, is that the data doesn't need to be liberated — it needs to be joined.** A single URL per entity (street, project, meeting, media release, councillor) with everything Council has said about that entity in one place, attributed and linked back to Council. If Google indexes those joined pages ahead of Council's fractured ones, the resident gets what they were looking for in one click.

## Findings from the initial spike

- Civic Projects Map is not ArcGIS. It's a custom OpenLayers app that pulls a single GeoJSON file from `maps.ipswich.qld.gov.au/icc/data/Projects_Infrastructure.JSON`. 385 features, ~780 KB, refreshed daily. Contains: project name, description, status, phase of work, division, dates, and up to 10 numbered pairs of (URL, title) for related documents.
- Traffic dashboard has two feeds: `/dashboard/imsRoad` (Council's own IMS, usually empty) and `/dashboard/tmrRoadData` (QLDTraffic proxy, populated). Both return JSON-encoded strings that themselves contain JSON — decode twice.
- InfoCouncil (`ipswich.infocouncil.biz`) exposes static per-meeting HTML pages with predictable URLs and per-attachment PDFs in parallel `_files/` folders. Full-text search of the portal returns zero hits for `INF03149` — confirming the two systems do not cross-reference.
- The `ipswichfacts.au` domain is probably available (check first).

## What was tried that didn't stick

- **A pre-render-only static site.** Discarded in favour of static + widget-hydrated after realising the dataset is small enough (~3 MB brotli) to ship the whole thing to the browser and let it do live cross-filtering without a backend.
- **ArcGIS FeatureServer scraping.** Not needed — Ipswich uses a simpler homemade stack.
- **sql.js-httpvfs.** Worth keeping in mind for v2 if queries get more complex, but chunked JSON is enough for v1.

## Sources referenced in the origin session

- `traffic.ipswich.qld.gov.au`
- `maps.ipswich.qld.gov.au/civicprojects`
- `www.ipswich.qld.gov.au/About-Council/Initiatives/Works-and-Projects`
- `www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications/Strategy-and-Implementation-Programs/iGO-Ipswich-Transport-Strategy-2025`
- `www.ipswich.qld.gov.au/files/assets/public/v/1/about-council/initiatives/works-and-projects/documents/4-capitalworksprogram2025-2028_web.pdf` — Capital Works Program 2025–28
- `ipswich.infocouncil.biz` — Council Business Papers portal
- `www.ipswichfirst.com.au` — Council-run news
- `www.ipswich.qld.gov.au/About-Council/Media-and-Publications/Corporate-Publications/Legal-Disclaimer-and-Copyright-Notice` — CC BY 4.0 confirmation
