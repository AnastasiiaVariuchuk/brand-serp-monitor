"""SERP acquisition layer.

`SerpApiClient` talks to a SerpAPI-compatible endpoint; `FixtureSerpClient`
replays a stored JSON payload. Both return the same normalised list of
`SerpResult`, so the rest of the pipeline does not care where data came from.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import SerpResult


class SerpError(RuntimeError):
    """SERP could not be retrieved (network, quota, HTTP error)."""


class EmptySerpResponse(SerpError):
    """The payload was valid JSON but contained no organic results."""


def parse_organic(payload: Any, limit: int = 10) -> list[SerpResult]:
    """Normalise a SerpAPI-style payload; malformed entries are skipped."""
    if not isinstance(payload, dict):
        raise EmptySerpResponse("SERP payload is not a JSON object")
    if payload.get("error"):
        raise SerpError(str(payload["error"]))

    raw = payload.get("organic_results")
    if not isinstance(raw, list) or not raw:
        raise EmptySerpResponse("no organic_results in SERP payload")

    results: list[SerpResult] = []
    fallback_position = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("link") or item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        fallback_position += 1
        position = item.get("position")
        if not isinstance(position, int) or position <= 0:
            position = fallback_position
        result = SerpResult(
            position=position,
            url=url.strip(),
            title=str(item.get("title") or ""),
            snippet=str(item.get("snippet") or item.get("description") or ""),
        )
        if result.domain is None:      # unparsable URL -> not worth storing
            continue
        results.append(result)
        if len(results) >= limit:
            break

    if not results:
        raise EmptySerpResponse("organic_results contained no usable entries")
    return sorted(results, key=lambda r: r.position)


class FixtureSerpClient:
    """Offline source: reads a stored SERP JSON file."""

    name = "fixture"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def search(self, query: str, gl: str = "nl", hl: str = "nl",
               device: str = "desktop", limit: int = 10) -> list[SerpResult]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SerpError(f"fixture not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise SerpError(f"fixture is not valid JSON: {exc}") from exc
        return parse_organic(payload, limit=limit)


class SerpApiClient:
    """Live SERP API client with retries and explicit error handling."""

    name = "serp_api"

    def __init__(self, api_key: str, api_url: str = "https://serpapi.com/search.json",
                 timeout: float = 15.0, max_retries: int = 3, sleep=time.sleep):
        if not api_key:
            raise SerpError("SERP_API_KEY is not set")
        self.api_key = api_key
        self.api_url = api_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep

    def search(self, query: str, gl: str = "nl", hl: str = "nl",
               device: str = "desktop", limit: int = 10) -> list[SerpResult]:
        try:
            import requests
        except ImportError as exc:
            raise SerpError("requests is not installed") from exc

        params = {
            "engine": "google", "q": query, "gl": gl, "hl": hl,
            "device": device, "num": max(limit, 10), "google_domain": "google.nl",
            "api_key": self.api_key,
        }
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(self.api_url, params=params, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SerpError(f"transient HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise SerpError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return parse_organic(resp.json(), limit=limit)
            except EmptySerpResponse:
                raise
            except Exception as exc:
                last = exc
                if attempt < self.max_retries:
                    self._sleep(2 ** attempt)
        raise SerpError(f"SERP API unavailable after {self.max_retries} attempts: {last}")


class FallbackSerpClient:
    """Tries the primary client, falls back to a secondary one (e.g. fixture)."""

    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback
        self.name = getattr(primary, "name", "primary")

    def search(self, *args, **kwargs) -> list[SerpResult]:
        try:
            return self.primary.search(*args, **kwargs)
        except SerpError:
            self.name = getattr(self.fallback, "name", "fallback")
            return self.fallback.search(*args, **kwargs)
