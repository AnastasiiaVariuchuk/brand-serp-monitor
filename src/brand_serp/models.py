"""Dataclasses shared by every layer of the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .domains import domain_of


@dataclass(frozen=True)
class SerpResult:
    position: int
    url: str
    title: str = ""
    snippet: str = ""
    domain: str | None = None

    def __post_init__(self) -> None:
        if self.domain is None:
            object.__setattr__(self, "domain", domain_of(self.url))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageEvidence:
    """What we learned by actually opening the ranking page."""

    url: str
    fetched: bool = False
    status: int | None = None
    final_url: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    outbound: list[str] = field(default_factory=list)
    # Same-domain redirector links mapped to where they actually land:
    # {link on the page -> final URL after following redirects}. Empty when the
    # page had none, or when the provider could not follow them.
    resolved_internal_links: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Signal:
    """A single piece of evidence pointing at (or away from) a category."""

    name: str
    category: str | None      # None = informational only, not scored
    weight: float
    detail: str
    decisive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Classification:
    category: str
    confidence: float
    explanation: str
    signals: list[Signal] = field(default_factory=list)
    classifier: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": round(self.confidence, 2),
            "explanation": self.explanation,
            "classifier": self.classifier,
            "signals": [s.to_dict() for s in self.signals],
        }


@dataclass
class ClassifiedResult:
    result: SerpResult
    classification: Classification
    evidence: PageEvidence | None = None

    def to_row(self) -> dict[str, Any]:
        c = self.classification
        return {
            "position": self.result.position,
            "url": self.result.url,
            "domain": self.result.domain,
            "title": self.result.title,
            "category": c.category,
            "confidence": round(c.confidence, 2),
            "explanation": c.explanation,
            "classifier": c.classifier,
            "signals": [s.name for s in c.signals if s.category],
        }
