"""Orchestration: SERP -> evidence -> classification -> rows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import config as C
from . import llm
from .classifier import classify
from .evidence import EvidenceProvider, NullEvidenceProvider
from .models import ClassifiedResult, SerpResult


@dataclass
class RunResult:
    query: str
    gl: str
    hl: str
    device: str
    source: str
    items: list[ClassifiedResult]

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [i.to_row() for i in self.items]


def run_check(serp_client, *, query: str, gl: str = "nl", hl: str = "nl",
              device: str = "desktop", limit: int = 10,
              evidence_provider: EvidenceProvider | None = None,
              settings=None) -> RunResult:
    """Fetch a SERP and classify every organic result. Raises `SerpError`
    only if the SERP itself cannot be obtained; page-level failures degrade
    gracefully into `unknown`."""
    evidence_provider = evidence_provider or NullEvidenceProvider()
    results: list[SerpResult] = serp_client.search(
        query=query, gl=gl, hl=hl, device=device, limit=limit
    )

    items: list[ClassifiedResult] = []
    for result in results:
        try:
            evidence = evidence_provider.get(result.url)
        except Exception as exc:  # a provider must never break the run
            evidence = None
            classification = classify(result, None)
            classification.explanation += f" (evidence collection failed: {exc})"
        else:
            classification = classify(result, evidence)
        if settings is not None and classification.category == C.UNKNOWN:
            classification = llm.refine(result, evidence, classification, settings)
        items.append(ClassifiedResult(result=result, classification=classification,
                                      evidence=evidence))

    return RunResult(query=query, gl=gl, hl=hl, device=device,
                     source=getattr(serp_client, "name", "unknown"), items=items)
