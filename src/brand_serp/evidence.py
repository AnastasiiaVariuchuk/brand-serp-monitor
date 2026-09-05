"""Providers of page-level evidence (outbound links, redirect chains).

Three implementations:
  * `NullEvidenceProvider`   - SERP metadata only (fastest, least accurate).
  * `FixtureEvidenceProvider`- offline snapshots, used by tests and the demo.
  * `HttpEvidenceProvider`   - real crawl of the ranking URL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from . import config as C
from .models import PageEvidence
from .domains import domain_of, query_keys

HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
MAX_OUTBOUND = 300


class EvidenceProvider(Protocol):
    def get(self, url: str) -> PageEvidence: ...


class NullEvidenceProvider:
    """Used when crawling is disabled; forces the classifier to stay cautious."""

    def get(self, url: str) -> PageEvidence:
        return PageEvidence(url=url, fetched=False, error="evidence collection disabled")


class FixtureEvidenceProvider:
    """Reads pre-recorded page evidence, keyed by registrable domain."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read page fixture {self.path}: {exc}") from exc
        if not isinstance(self._data, dict):
            raise ValueError("page fixture must be a JSON object keyed by domain")

    def get(self, url: str) -> PageEvidence:
        key = domain_of(url)
        raw = self._data.get(key)
        if not isinstance(raw, dict):
            return PageEvidence(url=url, fetched=False, error="no fixture for this domain")
        return PageEvidence(
            url=url,
            fetched=bool(raw.get("fetched", True)),
            status=raw.get("status"),
            final_url=raw.get("final_url") or url,
            redirect_chain=list(raw.get("redirect_chain") or []),
            outbound=list(raw.get("outbound") or [])[:MAX_OUTBOUND],
            resolved_internal_links=_as_str_mapping(raw.get("resolved_internal_links")),
            error=raw.get("error"),
        )


class HttpEvidenceProvider:
    """Fetches the page itself. Never raises: failures become `fetched=False`."""

    def __init__(self, timeout: float = 15.0, user_agent: str = "brand-serp-monitor/0.1"):
        self.timeout = timeout
        self.user_agent = user_agent

    def get(self, url: str) -> PageEvidence:
        try:
            import requests  # imported lazily so offline runs need no dependency
        except ImportError:
            return PageEvidence(url=url, fetched=False, error="requests is not installed")
        try:
            resp = requests.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent, "Accept-Language": "nl-NL,nl;q=0.9"},
                allow_redirects=True,
            )
        except Exception as exc:  # network, TLS, redirect loops, ...
            return PageEvidence(url=url, fetched=False, error=f"{type(exc).__name__}: {exc}")

        final_url = str(resp.url)
        chain = [str(r.url) for r in resp.history] + [final_url]
        if resp.status_code >= 400:
            return PageEvidence(url=url, fetched=False, status=resp.status_code,
                                final_url=final_url, redirect_chain=chain,
                                error=f"HTTP {resp.status_code}")
        outbound = extract_outbound_links(resp.text, final_url)
        return PageEvidence(
            url=url,
            fetched=True,
            status=resp.status_code,
            final_url=final_url,
            redirect_chain=chain,
            outbound=outbound,
            resolved_internal_links=self._resolve_internal(
                internal_redirectors(outbound, domain_of(final_url)), requests
            ),
        )

    def _resolve_internal(self, urls: list[str], requests) -> dict[str, str]:
        """Follow same-domain redirectors to see where the click really lands.

        Best effort by design: one dead redirector must not cost us the page, so
        every individual failure is skipped and the rest are still resolved.
        """
        if not urls:
            return {}
        timeout = min(self.timeout, C.REDIRECT_TIMEOUT)
        headers = {"User-Agent": self.user_agent, "Accept-Language": "nl-NL,nl;q=0.9"}
        resolved: dict[str, str] = {}
        try:
            session = requests.Session()
            session.max_redirects = C.MAX_REDIRECT_HOPS
        except Exception:  # nothing here is worth failing the page for
            return {}
        try:
            for link in urls[:C.MAX_INTERNAL_REDIRECTS]:
                try:
                    resp = session.head(link, timeout=timeout, headers=headers,
                                        allow_redirects=True)
                    if resp.status_code >= 400:
                        # Plenty of redirectors answer 405/403 to HEAD.
                        resp = session.get(link, timeout=timeout, headers=headers,
                                           allow_redirects=True, stream=True)
                        resp.close()
                    if resp.status_code >= 400:
                        continue
                    final = str(resp.url)
                    if final and final != link:
                        resolved[link] = final
                except Exception:  # network, TLS, too many redirects, ...
                    continue
        finally:
            try:
                session.close()
            except Exception:
                pass
        return resolved


def _as_str_mapping(raw) -> dict[str, str]:
    """Defensive read of a fixture field that should be a {str: str} mapping."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def looks_like_redirector(url: str) -> bool:
    """True if a link exists to forward the click rather than to show a page.

    Two shapes, both from `config`: a redirector path segment (`/go/`, `/out/`)
    or an affiliate tracking key in the query string.
    """
    path = (urlsplit(url).path or "/").lower()
    if not path.endswith("/"):
        path += "/"                      # so `/out?id=1` matches the `/out/` entry
    if any(pattern in path for pattern in C.INTERNAL_REDIRECT_PATTERNS):
        return True
    return bool(query_keys(url) & C.AFFILIATE_PARAM_KEYS)


def internal_redirectors(links: list[str], base_domain: str | None) -> list[str]:
    """The subset of `links` that stays on `base_domain` — i.e. the cloaked ones."""
    return [u for u in links if base_domain and domain_of(u) == base_domain]


def extract_outbound_links(html: str, base_url: str) -> list[str]:
    """Links worth classifying, de-duplicated and absolute.

    Everything that leaves the registrable domain, plus same-domain links that
    look like redirectors (`looks_like_redirector`). Keeping the latter is the
    point: an affiliate that routes clicks through `casino.nl/go/starcasino`
    otherwise appears to link to nobody, and gets away with it. Ordinary
    internal navigation (`/about`, `/contact`) is still dropped.
    """
    base_domain = domain_of(base_url)
    seen: dict[str, None] = {}
    for href in HREF_RE.findall(html or ""):
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        d = domain_of(absolute)
        if not d:
            continue
        if d == base_domain and not looks_like_redirector(absolute):
            continue
        seen.setdefault(absolute, None)
        if len(seen) >= MAX_OUTBOUND:
            break
    return list(seen)
