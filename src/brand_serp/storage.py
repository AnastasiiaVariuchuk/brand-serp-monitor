"""SQLite storage: one row per check, one row per classified result.

The schema is intentionally append-only — we never overwrite a past check, so
history stays reconstructable and the dashboard can plot any time range.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at   TEXT NOT NULL,
    query        TEXT NOT NULL,
    gl           TEXT NOT NULL,
    hl           TEXT NOT NULL,
    device       TEXT NOT NULL,
    source       TEXT NOT NULL,
    results_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id    INTEGER NOT NULL REFERENCES checks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    url         TEXT NOT NULL,
    domain      TEXT NOT NULL,
    title       TEXT,
    category    TEXT NOT NULL,
    confidence  REAL NOT NULL,
    explanation TEXT,
    classifier  TEXT,
    signals     TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_check  ON results(check_id);
CREATE INDEX IF NOT EXISTS idx_results_domain ON results(domain);
CREATE INDEX IF NOT EXISTS idx_checks_key     ON checks(query, gl, device, checked_at);
"""


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def save_check(self, *, query: str, gl: str, hl: str, device: str, source: str,
                   rows: Iterable[dict[str, Any]], checked_at: str | None = None) -> int:
        rows = list(rows)
        checked_at = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO checks (checked_at, query, gl, hl, device, source, results_count)"
            " VALUES (?,?,?,?,?,?,?)",
            (checked_at, query, gl, hl, device, source, len(rows)),
        )
        check_id = int(cur.lastrowid)
        self.conn.executemany(
            "INSERT INTO results (check_id, position, url, domain, title, category,"
            " confidence, explanation, classifier, signals) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (check_id, r["position"], r["url"], r["domain"], r.get("title", ""),
                 r["category"], r["confidence"], r.get("explanation", ""),
                 r.get("classifier", "rules"), json.dumps(r.get("signals", []), ensure_ascii=False))
                for r in rows
            ],
        )
        self.conn.commit()
        return check_id

    def list_checks(self, query: str | None = None, gl: str | None = None,
                    device: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM checks WHERE 1=1"
        params: list[Any] = []
        for column, value in (("query", query), ("gl", gl), ("device", device)):
            if value:
                sql += f" AND {column} = ?"
                params.append(value)
        sql += " ORDER BY checked_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def snapshot_keys(self) -> dict[str, list[str]]:
        """Distinct values of the snapshot dimensions — used to build UI filters."""
        keys = {}
        for column in ("query", "gl", "device"):
            rows = self.conn.execute(
                f"SELECT DISTINCT {column} AS v FROM checks ORDER BY v"
            )
            keys[column] = [r["v"] for r in rows]
        return keys

    def results_for_check(self, check_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM results WHERE check_id = ? ORDER BY position", (check_id,)
        )
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["signals"] = json.loads(d.get("signals") or "[]")
            except json.JSONDecodeError:
                d["signals"] = []
            out.append(d)
        return out

    def last_two_checks(self, query: str, gl: str, device: str) -> list[dict[str, Any]]:
        return self.list_checks(query=query, gl=gl, device=device, limit=2)

    def domain_timeline(self, domain: str, query: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT c.checked_at, c.query, r.position, r.category, r.confidence"
               " FROM results r JOIN checks c ON c.id = r.check_id WHERE r.domain = ?")
        params: list[Any] = [domain]
        if query:
            sql += " AND c.query = ?"
            params.append(query)
        sql += " ORDER BY c.checked_at"
        return [dict(r) for r in self.conn.execute(sql, params)]
