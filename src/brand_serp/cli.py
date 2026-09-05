"""Command line interface.

    python -m brand_serp.cli run --source fixture
    python -m brand_serp.cli run --source api --query starcasino --gl nl
    python -m brand_serp.cli history --query starcasino
    python -m brand_serp.cli diff --query starcasino
    python -m brand_serp.cli serve --port 8000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as C
from . import report
from . import web
from .config import Settings
from .evidence import FixtureEvidenceProvider, HttpEvidenceProvider, NullEvidenceProvider
from .history import diff as diff_checks
from .pipeline import run_check
from .serp_client import FallbackSerpClient, FixtureSerpClient, SerpApiClient, SerpError
from .storage import Storage


def _build_serp_client(args, settings: Settings):
    fixture = FixtureSerpClient(args.fixture or C.DEFAULT_SERP_FIXTURE)
    if args.source == "fixture":
        return fixture
    try:
        api = SerpApiClient(settings.serp_api_key or "", settings.serp_api_url,
                            settings.http_timeout, settings.max_retries)
    except SerpError as exc:
        print(f"[warn] {exc}; falling back to the offline fixture", file=sys.stderr)
        return fixture
    return FallbackSerpClient(api, fixture) if args.fallback else api


# Incoherent --source/--evidence pairs: the run completes, but the numbers mean
# nothing, and the failure looks like a broken classifier rather than a bad flag.
INCOHERENT_SOURCES = {
    ("fixture", "http"): (
        "--source fixture replays synthetic SERP data, but --evidence http will "
        "crawl those domains on the real internet. Most of them do not exist, so "
        "the fetches fail and the results collapse to `unknown` for reasons that "
        "have nothing to do with the classifier.\n"
        "       Use --evidence fixture for an offline demo, or --source api for a live run."
    ),
    ("api", "fixture"): (
        "--source api returns live SERP results, but --evidence fixture replays "
        "recorded page snapshots that almost certainly do not describe them. "
        "Domains with no snapshot degrade to `unknown`; domains that happen to "
        "match are judged on stale evidence.\n"
        "       Use --evidence http to crawl the live results, or --source fixture."
    ),
}


def warn_on_incoherent_sources(source: str, evidence: str, force: bool = False) -> str | None:
    """Warn about mismatched data sources. Returns the message (for tests) or None.

    Warns and continues on purpose: the combination is occasionally deliberate,
    so the user's flags are never rewritten and the run is never aborted.
    """
    if force:
        return None
    hint = INCOHERENT_SOURCES.get((source, evidence))
    if hint is None:
        return None
    message = f"[warn] {hint}\n       Pass --force to silence this warning."
    print(message, file=sys.stderr)
    return message


def _build_evidence_provider(args, settings: Settings):
    if args.evidence == "none":
        return NullEvidenceProvider()
    if args.evidence == "http":
        return HttpEvidenceProvider(settings.http_timeout, settings.user_agent)
    return FixtureEvidenceProvider(args.page_fixture or C.DEFAULT_PAGE_FIXTURE)


def cmd_run(args) -> int:
    warn_on_incoherent_sources(args.source, args.evidence, force=args.force)
    settings = Settings.from_env()
    if args.use_llm:
        settings.use_llm = True
    client = _build_serp_client(args, settings)
    provider = _build_evidence_provider(args, settings)

    try:
        run = run_check(client, query=args.query, gl=args.gl, hl=args.hl,
                        device=args.device, limit=args.top,
                        evidence_provider=provider, settings=settings)
    except SerpError as exc:
        print(f"[error] could not fetch the SERP: {exc}", file=sys.stderr)
        return 2

    rows = run.rows
    summary = report.summarize(rows)
    print(report.format_table(rows))
    print()
    print(report.format_summary(summary))

    if not args.no_db:
        with Storage(args.db or settings.db_path) as storage:
            check_id = storage.save_check(query=run.query, gl=run.gl, hl=run.hl,
                                          device=run.device, source=run.source, rows=rows)
            print(f"\n[db] saved check #{check_id} to {args.db or settings.db_path}")

    if args.out:
        out_dir = Path(args.out)
        json_path = report.write_json(out_dir / "latest_check.json", {
            "query": run.query, "gl": run.gl, "hl": run.hl, "device": run.device,
            "source": run.source, "summary": summary, "results": rows,
        })
        csv_path = report.write_csv(out_dir / "latest_check.csv", rows)
        print(f"[out] {json_path}\n[out] {csv_path}")
    return 0


def cmd_history(args) -> int:
    settings = Settings.from_env()
    with Storage(args.db or settings.db_path) as storage:
        checks = storage.list_checks(query=args.query, gl=args.gl, device=args.device,
                                     limit=args.limit)
        if not checks:
            print("History is empty — run `run` first.")
            return 0
        for check in checks:
            print(f"#{check['id']}  {check['checked_at']}  q={check['query']} "
                  f"gl={check['gl']} device={check['device']} "
                  f"source={check['source']} results={check['results_count']}")
        if args.domain:
            print(f"\nTimeline for {args.domain}:")
            for point in storage.domain_timeline(args.domain, args.query):
                print(f"  {point['checked_at']}  pos. {point['position']:>2}  "
                      f"{point['category']} ({point['confidence']})")
    return 0


def cmd_diff(args) -> int:
    settings = Settings.from_env()
    with Storage(args.db or settings.db_path) as storage:
        checks = storage.last_two_checks(args.query, args.gl, args.device)
        if len(checks) < 2:
            print("At least two checks are required for a comparison.")
            return 0
        curr, prev = checks[0], checks[1]
        changes = diff_checks(storage.results_for_check(prev["id"]),
                              storage.results_for_check(curr["id"]))
    print(f"Comparing checks #{prev['id']} ({prev['checked_at']}) "
          f"-> #{curr['id']} ({curr['checked_at']})\n")
    for key, title in (("appeared", "Appeared"), ("disappeared", "Disappeared"),
                       ("moved", "Moved"), ("recategorized", "Recategorized")):
        entries = changes[key]
        print(f"{title}: {len(entries)}")
        for entry in entries:
            print("   ", entry)
    return 0


def cmd_serve(args) -> int:
    settings = Settings.from_env()
    web.serve(args.db or settings.db_path, host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brand-serp-monitor",
                                     description="Branded SERP monitoring")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a SERP check")
    run_p.add_argument("--query", default="starcasino")
    run_p.add_argument("--gl", default="nl")
    run_p.add_argument("--hl", default="nl")
    run_p.add_argument("--device", default="desktop", choices=["desktop", "mobile", "tablet"])
    run_p.add_argument("--top", type=int, default=10)
    run_p.add_argument("--source", default="fixture", choices=["fixture", "api"])
    run_p.add_argument("--fixture", help="path to a SERP JSON fixture")
    run_p.add_argument("--evidence", default="fixture", choices=["fixture", "http", "none"])
    run_p.add_argument("--page-fixture", help="path to a JSON fixture with page snapshots")
    run_p.add_argument("--fallback", action="store_true",
                       help="fall back to the fixture if the API fails")
    run_p.add_argument("--use-llm", action="store_true", help="LLM fallback for unknown results")
    run_p.add_argument("--force", action="store_true",
                       help="silence the warning about mismatched --source/--evidence")
    run_p.add_argument("--out", help="directory for the JSON/CSV export")
    run_p.add_argument("--db", help="path to the SQLite database")
    run_p.add_argument("--no-db", action="store_true")
    run_p.set_defaults(func=cmd_run)

    hist_p = sub.add_parser("history", help="list the stored checks")
    hist_p.add_argument("--query", default="starcasino")
    hist_p.add_argument("--gl", default="nl")
    hist_p.add_argument("--device", default="desktop")
    hist_p.add_argument("--domain", help="show the timeline of a specific domain")
    hist_p.add_argument("--limit", type=int, default=20)
    hist_p.add_argument("--db")
    hist_p.set_defaults(func=cmd_history)

    diff_p = sub.add_parser("diff", help="difference between the two latest checks")
    diff_p.add_argument("--query", default="starcasino")
    diff_p.add_argument("--gl", default="nl")
    diff_p.add_argument("--device", default="desktop")
    diff_p.add_argument("--db")
    diff_p.set_defaults(func=cmd_diff)

    serve_p = sub.add_parser("serve", help="open the dashboard over the stored checks")
    serve_p.add_argument("--host", default="127.0.0.1",
                         help="bind address (use 0.0.0.0 inside a container)")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.add_argument("--db")
    serve_p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
