# Convenience shortcuts. Without Docker this also works: PYTHONPATH=src python -m brand_serp.cli ...
UID := $(shell id -u)
GID := $(shell id -g)
export UID
export GID

.PHONY: build run ui test shell diff history clean

build:            ## build the image
	docker compose build

run:              ## demo run on the offline fixture
	docker compose run --rm monitor

live:             ## live run via the SERP API (requires SERP_API_KEY in .env)
	docker compose run --rm monitor run --source api --evidence http --fallback \
		--query starcasino --gl nl --hl nl --device desktop --top 10 --out /app/output

ui:               ## dashboard over the stored checks -> http://localhost:8000
	docker compose up ui

test:             ## run the tests inside the container
	docker compose run --rm tests

history:          ## show the stored checks
	docker compose run --rm monitor history --query starcasino

diff:             ## difference between the two latest checks
	docker compose run --rm monitor diff --query starcasino

shell:            ## interactive shell inside the image
	docker compose run --rm --entrypoint bash monitor

clean:            ## remove the containers and the history volume
	docker compose down -v
