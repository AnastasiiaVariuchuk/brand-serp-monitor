"""A minimal read-only dashboard over the stored checks.

Deliberately built on `http.server` from the standard library: the rest of the
project runs with no third-party dependencies, and the UI is a thin read layer
over `Storage` — there is nothing here that a framework would make simpler.

The view model (`build_dashboard`) is separated from the rendering and from the
HTTP layer, so it can be tested without opening a socket.
"""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import config as C
from . import report
from .history import diff as diff_checks
from .storage import Storage

CHECK_LIMIT = 50


# ----------------------------------------------------------------- view model
def _official_position(rows: list[dict[str, Any]]) -> int | None:
    positions = [r["position"] for r in rows if r["domain"] in C.OFFICIAL_DOMAINS]
    return min(positions) if positions else None


def build_dashboard(storage: Storage, *, query: str | None = None, gl: str | None = None,
                    device: str | None = None, check_id: int | None = None) -> dict[str, Any]:
    """Everything the dashboard needs, as plain data."""
    checks = storage.list_checks(query=query, gl=gl, device=device, limit=CHECK_LIMIT)
    view: dict[str, Any] = {
        "filters": {"query": query, "gl": gl, "device": device},
        "options": storage.snapshot_keys(),
        "checks": checks,
        "check": None,
        "previous": None,
        "rows": [],
        "summary": None,
        "changes": None,
        "official_position": None,
        "competitors_above_official": 0,
    }
    if not checks:
        return view

    index = 0
    if check_id is not None:
        index = next((i for i, c in enumerate(checks) if c["id"] == check_id), 0)
    current = checks[index]
    previous = checks[index + 1] if index + 1 < len(checks) else None

    rows = storage.results_for_check(current["id"])
    official = _official_position(rows)

    view["check"] = current
    view["previous"] = previous
    view["rows"] = rows
    view["summary"] = report.summarize(rows)
    view["official_position"] = official
    view["competitors_above_official"] = sum(
        1 for r in rows
        if r["category"] == C.COMPETITOR and official is not None and r["position"] < official
    )
    if previous:
        view["changes"] = diff_checks(storage.results_for_check(previous["id"]), rows)
    return view


# -------------------------------------------------------------------- render
STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#16181d;--muted:#666e7a;--line:#e2e5ea;
--official:#1a7f4b;--affiliate:#1f6fb2;--competitor:#c0392b;--unknown:#6b7280;}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--card:#1c1f25;--fg:#e8eaed;
--muted:#98a1ad;--line:#2c313a;--official:#3ecf8e;--affiliate:#5aaef2;
--competitor:#f0736a;--unknown:#9aa3af;}}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:19px;margin:0 0 2px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:28px 0 10px}
.sub{color:var(--muted);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin-bottom:20px}
label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:var(--muted);margin-bottom:4px}
select,button{font:inherit;padding:6px 9px;border:1px solid var(--line);border-radius:7px;
background:var(--card);color:var(--fg)}
button{cursor:pointer;font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi .n{font-size:26px;font-weight:600;line-height:1.15}
.kpi .l{color:var(--muted);font-size:12px;margin-top:2px}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:14px 0 8px}
.bar span{display:block}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:720px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
color:var(--muted);font-weight:600;padding:0 10px 8px;border-bottom:1px solid var(--line)}
td{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
.pos{font-variant-numeric:tabular-nums;color:var(--muted);width:36px}
.tag{display:inline-block;padding:2px 8px;border-radius:20px;font-size:12px;
font-weight:600;color:#fff;white-space:nowrap}
.dom{font-weight:600}
.dom a{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
.why{color:var(--muted);font-size:13px}
details summary{cursor:pointer;color:var(--muted);font-size:12px;margin-top:6px}
code{background:var(--bg);border:1px solid var(--line);border-radius:4px;
padding:1px 5px;font-size:12px}
.conf{font-variant-numeric:tabular-nums;width:52px}
.low{color:var(--competitor)}
.changes{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.changes h3{margin:0 0 8px;font-size:13px}
.changes ul{margin:0;padding-left:16px;color:var(--muted);font-size:13px}
.changes .none{color:var(--muted);font-size:13px}
.empty{text-align:center;padding:48px 16px;color:var(--muted)}
footer{margin-top:32px;color:var(--muted);font-size:12px}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _select(name: str, values: list[str], selected: str | None, any_label: str) -> str:
    opts = [f'<option value="">{_e(any_label)}</option>']
    for value in values:
        mark = " selected" if value == selected else ""
        opts.append(f'<option value="{_e(value)}"{mark}>{_e(value)}</option>')
    return f'<select name="{_e(name)}" id="{_e(name)}">{"".join(opts)}</select>'


def _kpi(number: Any, label: str) -> str:
    return f'<div class="card kpi"><div class="n">{_e(number)}</div><div class="l">{_e(label)}</div></div>'


def _change_block(title: str, entries: list[dict[str, Any]], fmt) -> str:
    if not entries:
        body = '<p class="none">none</p>'
    else:
        body = "<ul>" + "".join(f"<li>{_e(fmt(e))}</li>" for e in entries) + "</ul>"
    return f'<div class="card"><h3>{_e(title)} · {len(entries)}</h3>{body}</div>'


def render_dashboard(view: dict[str, Any]) -> str:
    filters = view["filters"]
    options = view["options"]

    controls = (
        '<form method="get">'
        f'<div><label for="query">Query</label>{_select("query", options["query"], filters["query"], "all")}</div>'
        f'<div><label for="gl">Geo</label>{_select("gl", options["gl"], filters["gl"], "all")}</div>'
        f'<div><label for="device">Device</label>{_select("device", options["device"], filters["device"], "all")}</div>'
    )
    if view["checks"]:
        current_id = view["check"]["id"]
        opts = "".join(
            f'<option value="{c["id"]}"{" selected" if c["id"] == current_id else ""}>'
            f'#{c["id"]} · {_e(c["checked_at"])} · {_e(c["query"])}/{_e(c["gl"])}/{_e(c["device"])}'
            "</option>"
            for c in view["checks"]
        )
        controls += f'<div><label for="check">Check</label><select name="check" id="check">{opts}</select></div>'
    controls += '<button type="submit">Apply</button></form>'

    if not view["check"]:
        body = (
            '<div class="card empty"><p>No checks stored yet.</p>'
            "<p>Run <code>python -m brand_serp.cli run --source fixture</code> first, "
            "then reload this page.</p></div>"
        )
        return _page(controls + body)

    check, summary = view["check"], view["summary"]
    official = view["official_position"]

    kpis = "".join([
        _kpi(f"#{official}" if official else "—", "official site position"),
        _kpi(view["competitors_above_official"], "competitors above it"),
        _kpi(f"{summary['shares'][C.UNKNOWN] * 100:.0f}%", "unknown share"),
        _kpi(summary["total_results"], f"results · {summary['unique_domains']} unique domains"),
        _kpi(summary["avg_confidence"], f"avg confidence · {summary['low_confidence_results']} low"),
    ])

    bar = "".join(
        f'<span style="width:{summary["shares"][cat] * 100:.4f}%;background:var(--{cat})"></span>'
        for cat in C.CATEGORIES if summary["counts"][cat]
    )
    legend = "".join(
        f'<span><i style="background:var(--{cat})"></i>{cat} — '
        f'{summary["counts"][cat]} ({summary["shares"][cat] * 100:.0f}%)</span>'
        for cat in C.CATEGORIES
    )

    rows = []
    for r in view["rows"]:
        signals = r.get("signals") or []
        chips = ""
        if signals:
            chips = ('<details><summary>signals ({})</summary><p>{}</p></details>'.format(
                len(signals), " ".join(f"<code>{_e(s)}</code>" for s in signals)))
        low = " low" if float(r["confidence"]) < C.MIN_CONFIDENCE else ""
        rows.append(
            f'<tr><td class="pos">{r["position"]}</td>'
            f'<td><div class="dom"><a href="{_e(r["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{_e(r["domain"])}</a></div><div class="why">{_e(r.get("title") or "")}</div></td>'
            f'<td><span class="tag" style="background:var(--{r["category"]})">{_e(r["category"])}</span></td>'
            f'<td class="conf{low}">{float(r["confidence"]):.2f}</td>'
            f'<td><div class="why">{_e(r.get("explanation") or "")}</div>{chips}'
            f'<div class="why">via {_e(r.get("classifier") or "rules")}</div></td></tr>'
        )
    table = (
        '<div class="card scroll"><table><thead><tr><th>#</th><th>Domain</th>'
        "<th>Category</th><th>Conf</th><th>Why</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )

    changes_html = ""
    if view["changes"]:
        ch, prev = view["changes"], view["previous"]
        changes_html = (
            f'<h2>Changes since check #{prev["id"]} ({_e(prev["checked_at"])})</h2>'
            '<div class="changes">'
            + _change_block("Appeared", ch["appeared"],
                            lambda e: f'{e["domain"]} at #{e["position"]} ({e["category"]})')
            + _change_block("Disappeared", ch["disappeared"],
                            lambda e: f'{e["domain"]} was #{e["previous_position"]}')
            + _change_block("Moved", ch["moved"],
                            lambda e: f'{e["domain"]}  #{e["from"]} → #{e["to"]} '
                                      f'({"+" if e["delta"] > 0 else ""}{e["delta"]})')
            + _change_block("Recategorized", ch["recategorized"],
                            lambda e: f'{e["domain"]}  {e["from"]} → {e["to"]} ({e["confidence"]})')
            + "</div>"
        )
    else:
        changes_html = ('<h2>Changes</h2><div class="card"><p class="none">'
                        "Only one check stored for this snapshot — nothing to compare against yet."
                        "</p></div>")

    header = (
        f"<h1>{_e(C.BRAND)} — branded SERP</h1>"
        f'<p class="sub">Check #{check["id"]} · {_e(check["checked_at"])} · '
        f'query <code>{_e(check["query"])}</code> · {_e(check["gl"])}/{_e(check["hl"])} · '
        f'{_e(check["device"])} · source <code>{_e(check["source"])}</code></p>'
    )
    body = (
        header + controls
        + f'<div class="kpis">{kpis}</div>'
        + f'<div class="card" style="margin-top:10px"><div class="bar">{bar}</div>'
        + f'<div class="legend">{legend}</div></div>'
        + "<h2>Current SERP</h2>" + table
        + changes_html
    )
    return _page(body)


def _page(body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(C.BRAND)} — Brand SERP Monitor</title><style>{STYLE}</style></head>"
        f'<body><div class="wrap">{body}'
        "<footer>Read-only view of the stored checks. "
        "New data is collected with <code>brand_serp.cli run</code>.</footer>"
        "</div></body></html>"
    )


# ---------------------------------------------------------------- http layer
class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "brand-serp-monitor"
    db_path: str | Path = ""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        def one(name: str) -> str | None:
            value = (params.get(name) or [""])[0].strip()
            return value or None

        if parsed.path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8")
        if parsed.path not in ("/", "/api/dashboard"):
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        check = one("check")
        try:
            check_id = int(check) if check else None
        except ValueError:
            check_id = None

        with Storage(self.db_path) as storage:
            view = build_dashboard(storage, query=one("query"), gl=one("gl"),
                                   device=one("device"), check_id=check_id)

        if parsed.path == "/api/dashboard":
            body = json.dumps(view, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            return self._send(200, body, "application/json; charset=utf-8")
        self._send(200, render_dashboard(view).encode("utf-8"), "text/html; charset=utf-8")

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the default access log but drop the noisy timestamp prefix.
        print(f"[web] {self.address_string()} {fmt % args}")


def make_server(db_path: str | Path, host: str = "127.0.0.1",
                port: int = 8000) -> ThreadingHTTPServer:
    # A per-server subclass rather than a module-level global, so two servers in
    # one process (tests do this) never fight over the database path.
    handler = type("BoundDashboardHandler", (DashboardHandler,), {"db_path": db_path})
    return ThreadingHTTPServer((host, port), handler)


def serve(db_path: str | Path, host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = make_server(db_path, host, port)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"[web] dashboard on http://{shown}:{port}  (db: {db_path})")
    print("[web] Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] stopped")
    finally:
        httpd.server_close()
