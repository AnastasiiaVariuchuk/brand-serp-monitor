# Brand SERP Monitor — StarCasino (NL)

A prototype branded-SERP monitoring system for Google: it fetches the top-10
organic results for the query `starcasino` in the Netherlands, classifies every
domain (`official` / `affiliate` / `competitor` / `unknown`), stores the history
in SQLite and shows what changed between checks.

The theory part (2.1) lives in [`docs/THEORY.md`](docs/THEORY.md).

---

## Quick start

The demo runs **offline** on the Python 3.10+ standard library — no keys and no
network access required.

The fastest way is Docker (see the section below): `docker compose run --rm monitor`.
Locally, without Docker:

```bash
git clone <repo-url> && cd brand-serp-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # only needed for the tests and live mode

# SERP check on the test data
PYTHONPATH=src python -m brand_serp.cli run --source fixture --out output

# dashboard over everything stored so far
PYTHONPATH=src python -m brand_serp.cli serve      # http://localhost:8000

# tests
PYTHONPATH=src python -m pytest -q        # 118 tests
```

Expected output:

```
 #  DOMAIN                       CATEGORY     CONF  EXPLANATION
 1  starcasino.nl                official     0.97  is listed in the brand's official domain registry
 2  starcasino.nl                official     0.97  ...
 3  onlinecasinoground.nl        affiliate    0.84  outbound links to the official domain carry btag, subid
 4  casino.nl                    affiliate    0.84  cloaked /go/ link resolves to starcasino.nl with a btag
 5  jackscasino.nl               competitor   0.68  another licensed operator's domain
 6  starcasinobonus.net          affiliate    0.87  the redirect chain ends at starcasino.nl
 7  casinonieuws.nl              unknown      0.30  best hypothesis affiliate (0.35), threshold not reached
 8  casinozonder.nl              competitor   0.67  targets the brand, links only to other operators
 9  trustpilot.com               unknown      0.58  informational resource, monetisation not confirmed
10  casinobonus-nederland.com    unknown      0.50  no page-level evidence (HTTP 403)

Total results: 10 (unique domains: 9)
  official     2  (20%)   affiliate  3  (30%)
  competitor   2  (20%)   unknown    3  (30%)
```

JSON/CSV samples are in [`docs/sample_output/`](docs/sample_output).

## Dashboard

```bash
PYTHONPATH=src python -m brand_serp.cli run --source fixture    # collect a check
PYTHONPATH=src python -m brand_serp.cli serve                   # http://localhost:8000
```

A read-only view of whatever is already in SQLite — collection still happens
through `run`, so the UI never blocks on the network and never writes.

* **KPI tiles** — the official site's position, how many competitors rank above
  it, the `unknown` share (a data-quality metric, not a category), result and
  unique-domain counts, average confidence.
* **Category bar** — official / affiliate / competitor / unknown shares.
* **Current SERP** — position, domain, category, confidence, the explanation,
  and the list of signals that fired, expandable per row. Confidence below
  `MIN_CONFIDENCE` is highlighted.
* **Change feed** — appeared / disappeared / moved / recategorized against the
  previous check for the same snapshot.
* **Filters** — query, geo, device and the specific check to display.

| Route | Returns |
|---|---|
| `GET /` | the dashboard (server-rendered HTML) |
| `GET /api/dashboard` | the same view model as JSON |
| `GET /healthz` | `ok` — for container health checks |

All routes accept `?query=&gl=&device=&check=`.

Built on `http.server` from the standard library, so the UI adds **no
dependencies** and needs no build step. `build_dashboard()` returns the view
model as plain data, separately from rendering and from HTTP — which is what
`tests/test_web.py` exercises.

Binding is `127.0.0.1` by default; pass `--host 0.0.0.0` to expose it (this is
what the container does). There is no authentication, so do not put it on a
public interface as-is.

## Running in Docker

All you need is Docker with the Compose plugin. Nothing has to be installed locally.

```bash
# build (UID/GID are passed through so files in ./output belong to you, not root)
docker compose build

# demo run on the offline fixture — console output + JSON/CSV in ./output
docker compose run --rm monitor

# dashboard on http://localhost:8000 (reads the shared history volume)
docker compose up ui

# tests (118 of them)
docker compose run --rm tests
```

The same commands via `make`: `make build`, `make run`, `make ui`, `make test`,
`make live`, `make history`, `make diff`, `make shell`, `make clean`.

The `ui` service mounts the same `serp-state` volume that `monitor` writes to,
so a `docker compose run --rm monitor` is reflected on the next page reload.
Override the host port with `UI_PORT=9000 docker compose up ui`.

**Arbitrary CLI commands.** Everything after the service name is passed to the
CLI — the `ENTRYPOINT` already contains `python -m brand_serp.cli`:

```bash
docker compose run --rm monitor run --source fixture --top 10 --out /app/output
docker compose run --rm monitor history --query starcasino --domain jackscasino.nl
docker compose run --rm monitor diff --query starcasino
```

**Live run via the SERP API.** Copy `.env.example` to `.env` and fill in the
keys — Compose picks the file up automatically:

```bash
cp .env.example .env      # SERP_API_KEY=..., optionally ANTHROPIC_API_KEY + USE_LLM=true
docker compose run --rm monitor run --source api --evidence http --fallback \
    --query starcasino --gl nl --hl nl --device desktop --top 10 --out /app/output
```

**Data and volumes.**

| Path in the container | What it is | Where on the host |
|---|---|---|
| `/app/state/serp_monitor.sqlite3` | check history | named volume `serp-state` (survives restarts) |
| `/app/output` | JSON/CSV export | `./output` |
| `/app/data/fixtures` | test data | baked into the image |

History accumulates in the volume, so `diff` works across container runs.
To reset everything: `docker compose down -v`.

**Without Compose:**

```bash
docker build -t brand-serp-monitor --build-arg UID=$(id -u) --build-arg GID=$(id -g) .
docker run --rm -v "$PWD/output:/app/output" brand-serp-monitor run --source fixture --out /app/output
docker run --rm --entrypoint python brand-serp-monitor -m pytest -q
```

The image is based on `python:3.12-slim`, runs as the unprivileged user `app`,
and bakes in no secrets — they are passed only through environment variables.

---

### Live mode

```bash
cp .env.example .env      # fill in SERP_API_KEY
set -a && source .env && set +a

PYTHONPATH=src python -m brand_serp.cli run \
    --source api --evidence http --fallback \
    --query starcasino --gl nl --hl nl --device desktop --top 10
```

`--fallback` — use the offline fixture if the SERP API is unavailable.
`--evidence http` enables actual page crawling (redirect and tracking-parameter
detection). `--use-llm` enables the LLM fallback for `unknown` (see below).

`--source` and `--evidence` have to agree about whether the data is real.
`--source fixture --evidence http` crawls the live internet for domains that
only exist in a test file; `--source api --evidence fixture` judges live results
against stale snapshots. Both collapse most results to `unknown` for reasons
that have nothing to do with the classifier, so the CLI warns on stderr and
names the flag to change. It still runs — the combination is occasionally
deliberate — and `--force` silences the warning.

### LLM fallback

Off by default and never the primary classifier — it runs only for results the
rules left as `unknown`, and its verdict is accepted only at
`confidence >= 0.75` with a non-empty evidence list. The source of every
decision is recorded in the `classifier` column.

Two providers, selected with `LLM_PROVIDER`:

```bash
# Anthropic (default)
LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=...  USE_LLM=true

# Gemini
LLM_PROVIDER=gemini     GEMINI_API_KEY=...     USE_LLM=true
```

`LLM_MODEL` is optional — it defaults to `claude-sonnet-4-6` or
`gemini-2.5-flash` depending on the provider. `GOOGLE_API_KEY` is accepted as an
alias for `GEMINI_API_KEY`.

Only the HTTP call differs between providers (`_call_anthropic`,
`_call_gemini`); prompt building, parsing, validation and the thresholds are
shared, so adding a third provider is one function plus a registry entry. The
Gemini path additionally passes a `responseSchema`, which makes the JSON
structure a server-side guarantee rather than a prompt-level request.

Every failure mode — missing key, unknown provider, network error, quota,
malformed or low-confidence output, `requests` not installed — degrades to the
rule-based result instead of raising.

### History and changes

```bash
PYTHONPATH=src python -m brand_serp.cli history --query starcasino --domain jackscasino.nl
PYTHONPATH=src python -m brand_serp.cli diff --query starcasino
```

```
Comparing checks #1 -> #2
Appeared: 1         {'domain': 'casinovergelijker.nl', 'position': 9, 'category': 'unknown'}
Disappeared: 1      {'domain': 'casinonieuws.nl', 'previous_position': 7}
Moved: 2            {'domain': 'jackscasino.nl', 'from': 5, 'to': 3, 'delta': 2}
Recategorized: 0
```

---

## Architecture

```
serp_client  ──►  pipeline  ──►  classifier  ──►  storage (SQLite)
   (API/fixture)      │             (rules)           │
                      ▼                ▼              ▼
                  evidence          llm (fallback)  history / report
              (http/fixture/none)   for unknown     (diff, JSON, CSV)
                                                      │
                                                      ▼
                                                     web
                                              (read-only dashboard)
```

| Module | Responsibility |
|---|---|
| `config.py` | categories, thresholds, brand knowledge base (official domains, operators, tracking parameters) |
| `serp_client.py` | SERP retrieval: `SerpApiClient`, `FixtureSerpClient`, `FallbackSerpClient`; payload normalisation |
| `evidence.py` | page-level evidence: HTTP crawl, redirects, outbound links; fixtures for offline mode |
| `classifier.py` | rules → signals → decision with thresholds |
| `llm.py` | optional LLM fallback with structured output and validation; Anthropic or Gemini, selected by `LLM_PROVIDER` |
| `storage.py` | append-only SQLite: `checks` + `results` |
| `history.py` | diff between snapshots: appeared / disappeared / moved / recategorized |
| `report.py` | aggregations, table, JSON, CSV |
| `web.py` | read-only dashboard: view model, HTML rendering, `http.server` handler |
| `cli.py` | the `run`, `history`, `diff` and `serve` commands |

Runs locally (`PYTHONPATH=src`) or in Docker — the code and the tests are identical.

### Key decisions

**Signals, not hard rules.** Every rule emits a `Signal(name, category, weight,
detail)`. The decision is made centrally: a category is assigned only if its
weight is ≥ `MIN_SCORE` (0.6), the margin over the runner-up hypothesis is ≥
`MIN_MARGIN` (0.2) and the confidence is ≥ `MIN_CONFIDENCE` (0.6). Otherwise the
result is `unknown`. This directly satisfies the requirement "do not assign a
domain to a main category when the data is insufficient", and it makes every
decision explainable: the list of signals that fired is stored in the JSON/CSV.

**Affiliate vs hijacker is decided by the money, not by mentions.** The key
signals are the endpoint of the redirect chain and the tracking parameters
(`btag`, `aff_id`, `clickid`): a link to `starcasino.nl` carrying a `btag` →
`affiliate`; a page that targets the brand while all monetised links lead to
other operators → `competitor`. A plain link to the brand without tracking is a
weak signal (0.35) that is not sufficient on its own (see `casinonieuws.nl` in
the demo).

**Cloaked links are followed, not ignored.** Affiliates routinely hide the
monetised click behind their own domain — `casino.nl/go/starcasino`,
`/out/?id=123`. Such a page appears to link to nobody. Same-domain links whose
shape says "redirector" (a path from `INTERNAL_REDIRECT_PATTERNS`, or an
affiliate key in the query) are kept by `extract_outbound_links` and resolved
with HEAD requests, capped at 10 per page. A destination on an official domain
scores `affiliate` at 0.9 — the same weight as an open tracked link, because
hiding it is itself evidence of intent — and a destination on a competitor's
domain scores `competitor`. The explanation names both the cloaked link and
where it resolved, so the verdict stays auditable. `casino.nl` in the demo is
this case.

**The LLM is a fallback, not the main classifier.** It is called only for
`unknown`, requires strict JSON with a list of evidence, and is accepted only at
`confidence ≥ 0.75` with a non-empty evidence list; otherwise the result stays
`unknown`. The source of the decision is recorded in the `classifier` field.

**Degrade instead of crashing.** SERP API unavailable → retries with backoff →
optionally the fixture; an empty or malformed response → an explicit
`EmptySerpResponse`; records without a URL are skipped; an unreachable page →
`unknown` with a reason rather than an invented category; a failure in the
evidence provider does not stop the run.

**Append-only history.** Every check is an immutable snapshot, so the history can
be reprocessed with a new version of the classifier. Comparison is done per
domain using its best position.

---

## Tests

118 tests (`pytest`), no network access:

* `test_domains.py` — URL normalisation, multi-part TLDs, junk URLs;
* `test_classifier.py` — one case per category plus a check that a single weak
  signal does **not** yield a main category;
* `test_serp_client.py` — parsing, skipping malformed records, empty response,
  API error, fallback to the fixture;
* `test_history_storage.py` — SQLite roundtrip, all four change types, timeline;
* `test_pipeline_report.py` — end-to-end run, aggregations, CSV/JSON, evidence
  provider failure;
* `test_llm.py` — parsing and validation of the model's structured response,
  the request shape for both providers, and that every failure mode falls back
  to the rule-based result;
* `test_web.py` — the dashboard view model (KPIs, diff, filters, empty database),
  HTML escaping of untrusted SERP text, and the HTTP routes against a live
  server on an ephemeral port.

---

## What is not implemented and how I would do it

| Not done | Plan |
|---|---|
| Dashboard: drill-down, moderation queue, alert subscriptions | The overview, SERP table and change feed are built (see above). Still missing from `docs/THEORY.md` §8: the per-domain timeline, the manual-override queue (needs a `human_override` column — the schema is append-only today), and charts over time. Those want a real front end: FastAPI + React, or Metabase on top of Postgres |
| Authentication on the dashboard | None today — it binds to localhost. In production: put it behind the company SSO proxy rather than growing an auth layer here |
| Postgres instead of SQLite | SQLite was chosen to run out of the box; in production — Postgres + SCD2 for the domain category (`valid_from`/`valid_to`) |
| Scheduler | Airflow/Cloud Scheduler: a DAG per `query × geo × device` snapshot, jitter, retries, dead-letter |
| Cloaking via JS-injected links | Server-side cloaking through on-site redirectors **is** detected now (see Key decisions). What is still missed: links written by JavaScript, since extraction is a regex over `href` in the delivered HTML, and UA/IP/geo cloaking where the page shown to the crawler differs from the one a Dutch user sees. Both need headless rendering — Playwright from an NL exit IP, comparing the desktop and mobile renders against the SERP snippet |
| Reconciliation with the partner registry | The strongest signal: look up `btag` in the internal affiliate-programme database → confidence ≈ 1 |
| Alerts | Slack webhook on events: a competitor ranking above the official site, a category change, dropping out of the top-10 |
| Weight calibration | Weights are set by expert judgement; on labelled data — logistic regression plus precision/recall metrics per category |
| Multi-query support | Currently one query per run; it scales via a list of queries in the config |

---

## AI

### In the product

The classifier is rule-based. An LLM is used only as a fallback for results the
rules left as `unknown`, and only under strict conditions: structured JSON
output, a non-empty evidence list, and `confidence >= 0.75`. Anything else keeps
the rule-based verdict. Every row records which classifier produced it.

Two providers are supported, selected with `LLM_PROVIDER`:

| Provider | Env vars | Default model |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini-2.5-flash` |

Prompt building, response parsing, validation and the thresholds are shared
between them — only the HTTP call differs (`_call_anthropic`, `_call_gemini`),
so adding a third provider is one function plus a registry entry. The Gemini
path additionally sends a `responseSchema`, which makes the JSON shape a
server-side guarantee rather than a prompt-level request.

## Security

Secrets are not committed: `.env` is in `.gitignore`, the template is
`.env.example`. Keys are read only from environment variables via
`Settings.from_env()`.
# brand-serp-monitor
