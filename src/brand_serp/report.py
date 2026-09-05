"""Aggregation and output formats (table / JSON / CSV)."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from . import config as C


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    total = len(rows)
    counts = {cat: 0 for cat in C.CATEGORIES}
    for row in rows:
        counts[row.get("category", C.UNKNOWN)] = counts.get(row.get("category", C.UNKNOWN), 0) + 1
    shares = {cat: (round(n / total, 4) if total else 0.0) for cat, n in counts.items()}

    unique: dict[str, str] = {}
    for row in rows:
        unique.setdefault(row["domain"], row["category"])
    domain_counts = {cat: 0 for cat in C.CATEGORIES}
    for cat in unique.values():
        domain_counts[cat] = domain_counts.get(cat, 0) + 1

    confidences = [float(r.get("confidence", 0)) for r in rows]
    return {
        "total_results": total,
        "unique_domains": len(unique),
        "counts": counts,
        "shares": shares,
        "unique_domain_counts": domain_counts,
        "avg_confidence": round(sum(confidences) / total, 3) if total else 0.0,
        "low_confidence_results": sum(1 for c in confidences if c < C.MIN_CONFIDENCE),
    }


def format_table(rows: Iterable[dict[str, Any]], width: int = 38) -> str:
    rows = list(rows)
    header = f"{'#':>2}  {'DOMAIN':<28} {'CATEGORY':<11} {'CONF':>5}  EXPLANATION"
    lines = [header, "-" * (len(header) + 20)]
    for r in rows:
        explanation = (r.get("explanation") or "").replace("\n", " ")
        if len(explanation) > 80:
            explanation = explanation[:77] + "..."
        lines.append(
            f"{r['position']:>2}  {(r['domain'] or '?'):<28} {r['category']:<11} "
            f"{float(r['confidence']):>5.2f}  {explanation}"
        )
    return "\n".join(lines)


def format_summary(summary: dict[str, Any]) -> str:
    parts = [f"Total results: {summary['total_results']} "
             f"(unique domains: {summary['unique_domains']})"]
    for cat in C.CATEGORIES:
        parts.append(f"  {cat:<11} {summary['counts'][cat]:>2}  "
                     f"({summary['shares'][cat] * 100:.0f}%)")
    parts.append(f"Average confidence: {summary['avg_confidence']}; "
                 f"low confidence: {summary['low_confidence_results']}")
    return "\n".join(parts)


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


CSV_FIELDS = ("position", "url", "domain", "category", "confidence", "explanation",
              "classifier", "signals")


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            row = dict(row)
            if isinstance(row.get("signals"), list):
                row["signals"] = "|".join(map(str, row["signals"]))
            writer.writerow(row)
    return path
