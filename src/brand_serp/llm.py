"""Optional LLM fallback for domains the rules could not resolve.

Guardrails:
  * the LLM is called only for `unknown` results;
  * it must return strict JSON with a category, confidence and evidence list;
  * anything malformed, low-confidence or evidence-free is rejected and the
    result stays `unknown`.

Providers: only the HTTP call is vendor-specific (`_call_anthropic`,
`_call_gemini`) — prompt building, parsing, validation and the acceptance
thresholds are shared, so adding a provider is one function plus a registry
entry. Selected with `LLM_PROVIDER`; any failure degrades to the rule-based
result rather than raising.
"""
from __future__ import annotations

import json
import re

from . import config as C
from .models import Classification, PageEvidence, SerpResult, Signal

SYSTEM_PROMPT = (
    "You classify domains that rank for a branded search query. "
    "Answer with a single JSON object and nothing else: "
    '{"category": "official|affiliate|competitor|unknown", '
    '"confidence": 0.0-1.0, "evidence": ["..."], "explanation": "one sentence"}. '
    "Use `affiliate` only if the page monetises traffic towards the brand itself, "
    "`competitor` only if it redirects branded traffic to a different operator, and "
    "`unknown` whenever the provided data is insufficient. Never invent facts."
)

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

MAX_TOKENS = 1024

# Same shape as SYSTEM_PROMPT describes, in the subset of OpenAPI that Gemini
# accepts. Passing it makes the JSON structure a server-side guarantee instead
# of a prompt-level request; `parse_llm_payload` still validates the result.
GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "category": {"type": "STRING", "enum": list(C.CATEGORIES)},
        "confidence": {"type": "NUMBER"},
        "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        "explanation": {"type": "STRING"},
    },
    "required": ["category", "confidence", "evidence", "explanation"],
}


def build_prompt(result: SerpResult, evidence: PageEvidence | None, brand: str = C.BRAND) -> str:
    outbound = (evidence.outbound[:25] if evidence and evidence.fetched else [])
    payload = {
        "brand": brand,
        "official_domains": sorted(C.OFFICIAL_DOMAINS),
        "position": result.position,
        "url": result.url,
        "domain": result.domain,
        "title": result.title,
        "snippet": result.snippet,
        "final_url": evidence.final_url if evidence else None,
        "outbound_links_sample": outbound,
        "page_fetched": bool(evidence and evidence.fetched),
    }
    return "Classify this SERP result:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def parse_llm_payload(text: str) -> dict | None:
    """Extract and validate the JSON object returned by the model."""
    if not text:
        return None
    match = JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    category = str(data.get("category", "")).strip().lower()
    if category not in C.CATEGORIES:
        return None
    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    evidence = data.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    return {
        "category": category,
        "confidence": max(0.0, min(1.0, confidence)),
        "evidence": [str(e) for e in evidence][:5],
        "explanation": str(data.get("explanation", "")).strip(),
    }


def accept(payload: dict | None) -> bool:
    """A main category is accepted only with high confidence AND evidence."""
    if not payload:
        return False
    if payload["category"] == C.UNKNOWN:
        return True
    return payload["confidence"] >= C.LLM_MIN_CONFIDENCE and bool(payload["evidence"])


def to_classification(payload: dict, base_signals: list[Signal]) -> Classification:
    detail = payload["explanation"] or "; ".join(payload["evidence"])
    signals = list(base_signals) + [
        Signal("llm_verdict", payload["category"], payload["confidence"], detail)
    ]
    return Classification(
        category=payload["category"],
        confidence=payload["confidence"] if payload["category"] != C.UNKNOWN
        else min(payload["confidence"], 0.6),
        explanation=f"LLM: {detail}",
        signals=signals,
        classifier="llm",
    )


# ------------------------------------------------------------------ providers
def _post_json(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    """The only place that touches the network. `requests` is an optional
    dependency, so an ImportError here degrades like any other failure."""
    import requests

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _call_anthropic(prompt: str, settings) -> str:
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": settings.llm_model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        },
        settings.http_timeout,
    )
    blocks = data.get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _call_gemini(prompt: str, settings) -> str:
    data = _post_json(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.llm_model}:generateContent",
        {
            "x-goog-api-key": settings.gemini_api_key,
            "content-type": "application/json",
        },
        {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": MAX_TOKENS,
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": GEMINI_RESPONSE_SCHEMA,
            },
        },
        settings.http_timeout,
    )
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p.get("text"), str))


# provider name -> (call function, the Settings attribute holding its API key)
PROVIDERS = {
    "anthropic": (_call_anthropic, "anthropic_api_key"),
    "gemini": (_call_gemini, "gemini_api_key"),
}


def select_provider(settings):
    """The (call, key) pair to use, or None if the LLM fallback is unusable."""
    if not getattr(settings, "use_llm", False):
        return None
    call, key_attr = PROVIDERS.get(str(getattr(settings, "llm_provider", "")).lower(),
                                   (None, ""))
    if call is None:
        return None
    key = getattr(settings, key_attr, None)
    return (call, key) if key else None


def refine(result: SerpResult, evidence: PageEvidence | None, current: Classification,
           settings) -> Classification:
    """Return a refined classification, or the original one on any problem."""
    if current.category != C.UNKNOWN:
        return current
    provider = select_provider(settings)
    if provider is None:
        return current
    call, _ = provider
    try:
        text = call(build_prompt(result, evidence), settings)
    except Exception:  # network, auth, quota, bad payload, missing `requests`
        return current

    payload = parse_llm_payload(text)
    if not accept(payload):
        return current
    return to_classification(payload, current.signals)
