"""Change detection between two checks (domain-level)."""
from __future__ import annotations

from typing import Any, Iterable


def by_domain(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Best (lowest) position per domain — a domain can hold several URLs."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        domain = row.get("domain")
        if not domain:
            continue
        current = best.get(domain)
        if current is None or row["position"] < current["position"]:
            best[domain] = dict(row)
    return best


def diff(previous: Iterable[dict[str, Any]], current: Iterable[dict[str, Any]]) -> dict[str, Any]:
    prev, curr = by_domain(previous), by_domain(current)

    appeared = [
        {"domain": d, "position": r["position"], "category": r["category"]}
        for d, r in sorted(curr.items(), key=lambda kv: kv[1]["position"])
        if d not in prev
    ]
    disappeared = [
        {"domain": d, "previous_position": r["position"], "category": r["category"]}
        for d, r in sorted(prev.items(), key=lambda kv: kv[1]["position"])
        if d not in curr
    ]
    moved, recategorized = [], []
    for domain, row in sorted(curr.items(), key=lambda kv: kv[1]["position"]):
        old = prev.get(domain)
        if not old:
            continue
        if old["position"] != row["position"]:
            moved.append({
                "domain": domain,
                "from": old["position"],
                "to": row["position"],
                "delta": old["position"] - row["position"],  # >0 == moved up
            })
        if old["category"] != row["category"]:
            recategorized.append({
                "domain": domain,
                "from": old["category"],
                "to": row["category"],
                "confidence": row.get("confidence"),
            })
    return {
        "appeared": appeared,
        "disappeared": disappeared,
        "moved": moved,
        "recategorized": recategorized,
        "stable": len(curr) - len(appeared) - len(moved),
    }
