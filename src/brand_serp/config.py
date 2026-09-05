"""Central configuration: categories, thresholds and the brand knowledge base.

Everything that a human analyst would have to maintain (official domains,
known operators, tracking parameters) lives here so that the rest of the code
stays generic and reusable for other brands.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- categories ----------------------------------------------------------
OFFICIAL = "official"
AFFILIATE = "affiliate"
COMPETITOR = "competitor"
UNKNOWN = "unknown"
CATEGORIES = (OFFICIAL, AFFILIATE, COMPETITOR, UNKNOWN)

# --- decision thresholds -------------------------------------------------
MIN_SCORE = 0.6         # minimal accumulated weight for a non-unknown label
MIN_MARGIN = 0.2        # minimal gap between the best and the runner-up
MIN_CONFIDENCE = 0.6    # below this everything falls back to `unknown`
LLM_MIN_CONFIDENCE = 0.75
CONF_SMOOTHING = 0.25   # keeps a single weak signal from reaching confidence 1.0

# --- LLM providers -------------------------------------------------------
# The fallback is provider-agnostic: only the HTTP call differs (see `llm.py`).
DEFAULT_LLM_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-2.5-flash",
}
DEFAULT_LLM_PROVIDER = "anthropic"

# --- brand knowledge base ------------------------------------------------
BRAND = "StarCasino"
BRAND_TOKENS = ("starcasino", "star casino")

OFFICIAL_DOMAINS = frozenset({
    "starcasino.nl",
    "starcasino.be",
    "starcasino.com",
    "starcasino.sport",
})

# Licensed operators competing for the same NL traffic.
KNOWN_OPERATOR_DOMAINS = frozenset({
    "jackscasino.nl", "hollandcasino.nl", "toto.nl", "unibet.nl",
    "betcity.nl", "kansino.nl", "bet365.nl", "circus.nl",
    "fairplaycasino.nl", "711.nl", "leovegas.nl", "batavia-casino.nl",
    "tonybet.nl", "bwin.nl",
})

# Informational / non-commercial hosts: never a brand-traffic monetiser.
NEUTRAL_DOMAINS = frozenset({
    "wikipedia.org", "trustpilot.com", "kansspelautoriteit.nl",
    "facebook.com", "instagram.com", "x.com", "twitter.com",
    "linkedin.com", "youtube.com", "reddit.com", "nu.nl", "rtl.nl",
    "google.com", "apple.com", "play.google.com", "crunchbase.com",
})

# Query-string keys that normally mean "this click is being monetised".
AFFILIATE_PARAM_KEYS = frozenset({
    "btag", "aff", "affid", "aff_id", "affiliate", "affiliate_id",
    "clickid", "click_id", "subid", "sub_id", "tracker", "trackerid",
    "banner_id", "promocode", "refcode", "ref", "cxd", "pid", "mid",
})

# Path fragments used by *on-site* redirectors. An affiliate that hides its
# monetised click behind its own domain (`casino.nl/go/starcasino`) leaves no
# visible link to the brand at all, so these have to be followed to be seen.
# Matched against the link path with a trailing slash appended, so both
# `/out/?id=1` and `/out?id=1` hit the `/out/` entry.
INTERNAL_REDIRECT_PATTERNS = (
    "/go/", "/out/", "/visit/", "/goto/", "/redirect/",
    "/aff/", "/link/", "/click/",
)

# Budget for resolving those links: they cost one request each, on top of the
# page fetch, and a hostile page can list hundreds.
MAX_INTERNAL_REDIRECTS = 10     # resolved per page
REDIRECT_TIMEOUT = 5.0          # seconds per resolution, independent of the page timeout
MAX_REDIRECT_HOPS = 5           # chain length before we give up on a link

# Host prefixes used by affiliate redirect / cloaking layers.
AFFILIATE_NETWORK_HOSTS = (
    "go.", "track.", "trk.", "click.", "clk.", "record.", "out.",
    "affiliates.", "partners.", "redirect.",
)

# Lexical markers of a review/bonus page (NL + EN).
REVIEW_MARKERS = (
    "review", "recensie", "ervaringen", "beoordeling", "bonus",
    "gratis spins", "free spins", "uitbetaling", "betrouwbaar",
    "vergelijk", "beste casino", "top 10", "aanbieding", "promocode",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERP_FIXTURE = PROJECT_ROOT / "data" / "fixtures" / "serp_starcasino_nl.json"
DEFAULT_PAGE_FIXTURE = PROJECT_ROOT / "data" / "fixtures" / "pages_starcasino_nl.json"


@dataclass
class Settings:
    """Runtime settings, populated from environment variables."""

    serp_api_url: str = "https://serpapi.com/search.json"
    serp_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = DEFAULT_LLM_MODELS[DEFAULT_LLM_PROVIDER]
    use_llm: bool = False
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "serp_monitor.sqlite3")
    http_timeout: float = 15.0
    max_retries: int = 3
    user_agent: str = "brand-serp-monitor/0.1 (+monitoring bot)"

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower()
        # An explicit LLM_MODEL always wins; otherwise fall back to the default
        # for the selected provider, so switching provider is a one-line change.
        model = os.getenv("LLM_MODEL") or DEFAULT_LLM_MODELS.get(provider, cls.llm_model)
        return cls(
            serp_api_url=os.getenv("SERP_API_URL", cls.serp_api_url),
            serp_api_key=os.getenv("SERP_API_KEY"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            llm_provider=provider,
            llm_model=model,
            use_llm=os.getenv("USE_LLM", "false").lower() in {"1", "true", "yes"},
            db_path=Path(os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "serp_monitor.sqlite3"))),
            http_timeout=float(os.getenv("HTTP_TIMEOUT", "15")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
        )
