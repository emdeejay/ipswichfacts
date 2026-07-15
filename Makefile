.PHONY: all install scrape build clean serve sample backfill backfill-news

# One-off historical meetings backfill (2020-2025). Gentle: 2s between
# requests, ~1h total. Results are committed to data/archive/ and never
# re-scraped — old minutes don't change.
backfill:
	for y in 2020 2021 2022 2023 2024 2025; do \
		python3 -m scrape.council_meetings --year $$y --delay 2 --compact \
			--out data/archive/meetings-$$y.json; \
	done

# One-off historical Ipswich First backfill (2017 through last year).
# ~1 request per 100 posts — minutes, not hours. Results are committed to
# data/archive/ and never re-scraped — published posts don't change.
backfill-news:
	for y in 2017 2018 2019 2020 2021 2022 2023 2024 2025; do \
		python3 -m scrape.ipswich_first --year $$y --compact \
			--out data/archive/news-$$y.json; \
	done

# One command to build from live data
all: scrape build

install:
	python3 -m pip install -r requirements.txt

scrape:
	python3 -m scrape.civic_projects --out data/projects.json
	python3 -m scrape.road_closures --out data/closures.json
	python3 -m scrape.council_meetings --out data/meetings.json
	python3 -m scrape.ipswich_first --out data/news.json

build:
	python3 -m build.build_site --data data --out site

# Build from the checked-in sample data (no network needed)
sample:
	cp data/sample/projects.json data/projects.json
	cp data/sample/closures.json data/closures.json
	cp data/sample/meetings.json data/meetings.json
	cp data/sample/news.json data/news.json
	python3 -m build.build_site --data data --out site

serve:
	cd site && python3 -m http.server 8000

clean:
	rm -rf site/*
	rm -f data/projects.json data/closures.json data/meetings.json data/news.json
