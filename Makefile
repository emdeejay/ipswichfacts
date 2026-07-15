.PHONY: all install scrape build clean serve sample

# One command to build from live data
all: scrape build

install:
	python3 -m pip install -r requirements.txt

scrape:
	python3 -m scrape.civic_projects --out data/projects.json
	python3 -m scrape.road_closures --out data/closures.json
	python3 -m scrape.council_meetings --out data/meetings.json

build:
	python3 -m build.build_site --data data --out site

# Build from the checked-in sample data (no network needed)
sample:
	cp data/sample/projects.json data/projects.json
	cp data/sample/closures.json data/closures.json
	cp data/sample/meetings.json data/meetings.json
	python3 -m build.build_site --data data --out site

serve:
	cd site && python3 -m http.server 8000

clean:
	rm -rf site/*
	rm -f data/projects.json data/closures.json data/meetings.json
