"""URL / domain normalisation.

Deliberately dependency-free: `tldextract` would need to download the Public
Suffix List at runtime, which is a bad idea inside a scheduled job. A small
built-in list of multi-part suffixes covers the markets we care about.
"""
from __future__ import annotations

from urllib.parse import urlsplit, parse_qsl

MULTI_PART_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.nz",
    "com.br", "co.jp", "ne.jp", "or.jp", "com.tr", "co.za", "com.ua",
    "co.il", "com.mx", "com.ar", "co.in", "com.sg", "com.hk",
})


def host_from_url(url: str) -> str | None:
    """Return the bare lowercase hostname, or None if the URL is unusable."""
    if not url or not isinstance(url, str):
        return None
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        netloc = urlsplit(candidate).netloc
    except ValueError:
        return None
    if not netloc:
        return None
    netloc = netloc.rsplit("@", 1)[-1]          # drop credentials
    netloc = netloc.split(":", 1)[0]            # drop port
    netloc = netloc.strip().strip(".").lower()
    if not netloc or " " in netloc or "." not in netloc:
        return None
    return netloc


def strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host: str) -> str | None:
    """`www.shop.starcasino.co.uk` -> `starcasino.co.uk`."""
    if not host:
        return None
    parts = strip_www(host).split(".")
    if len(parts) < 2:
        return None
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_of(url: str) -> str | None:
    """Registrable domain of a URL (the unit we classify and store)."""
    host = host_from_url(url)
    return registrable_domain(host) if host else None


def subdomain_host(url: str) -> str | None:
    host = host_from_url(url)
    return strip_www(host) if host else None


def same_site(a: str, b: str) -> bool:
    da, db = domain_of(a), domain_of(b)
    return bool(da and db and da == db)


def query_keys(url: str) -> set[str]:
    """Lowercased query-string keys of a URL."""
    try:
        return {k.lower() for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    except ValueError:
        return set()
