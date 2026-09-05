"""Rule-based domain classifier.

Design notes
------------
* Every rule emits a `Signal` with a category and a weight; nothing is decided
  inside a rule (except an exact official-domain match, which is decisive).
* The final label is chosen only if the winning category has enough weight,
  a clear margin over the runner-up and a confidence above `MIN_CONFIDENCE`.
  Otherwise the result stays `unknown` — as required by the task, we never
  guess one of the three main categories on thin evidence.
* Every decision carries the signal list, so it is auditable and debuggable.
"""
from __future__ import annotations

from collections import defaultdict

from . import config as C
from .domains import domain_of, query_keys, subdomain_host
from .models import Classification, PageEvidence, SerpResult, Signal


# --------------------------------------------------------------------- utils
def _is_neutral(domain: str) -> bool:
    return any(domain == n or domain.endswith("." + n) for n in C.NEUTRAL_DOMAINS)


def _brand_in(text: str) -> bool:
    t = (text or "").lower()
    return any(tok in t for tok in C.BRAND_TOKENS)


def _brand_in_domain(domain: str) -> bool:
    flat = domain.replace("-", "").replace("_", "")
    return any(tok.replace(" ", "") in flat for tok in C.BRAND_TOKENS)


def _affiliate_params(url: str) -> set[str]:
    return query_keys(url) & C.AFFILIATE_PARAM_KEYS


def _is_network_host(url: str) -> bool:
    host = subdomain_host(url) or ""
    return host.startswith(C.AFFILIATE_NETWORK_HOSTS)


def _review_markers(text: str) -> list[str]:
    t = (text or "").lower()
    return [m for m in C.REVIEW_MARKERS if m in t]


# --------------------------------------------------------------------- rules
def build_signals(result: SerpResult, evidence: PageEvidence | None = None) -> list[Signal]:
    domain = result.domain
    signals: list[Signal] = []

    if not domain:
        return [Signal("unparsable_url", C.UNKNOWN, 0.9, "URL could not be parsed into a domain")]

    # 1. Official property — decisive, no further evidence needed.
    if domain in C.OFFICIAL_DOMAINS:
        return [Signal("official_domain", C.OFFICIAL, 1.0,
                       f"{domain} is listed in the brand's official domain registry", decisive=True)]

    text = f"{result.title} {result.snippet}"
    brand_in_text = _brand_in(text)

    # 2. Domain-level priors.
    if domain in C.KNOWN_OPERATOR_DOMAINS:
        signals.append(Signal("known_operator_domain", C.COMPETITOR, 0.95,
                              f"{domain} is another licensed operator's domain"))
    if _is_neutral(domain):
        signals.append(Signal("neutral_domain", C.UNKNOWN, 0.7,
                              f"{domain} is an informational/non-commercial resource; "
                              "monetisation of brand traffic is not confirmed"))
    if _brand_in_domain(domain):
        signals.append(Signal("brand_in_domain", C.AFFILIATE, 0.3,
                              "the brand name appears in the domain, but the domain is not official"))
    markers = _review_markers(text)
    if markers:
        signals.append(Signal("review_markers", C.AFFILIATE, 0.2,
                              "review/bonus vocabulary in the title or snippet: " + ", ".join(markers[:3])))
    if brand_in_text:
        signals.append(Signal("brand_in_title", None, 0.0, "the brand is mentioned in the title/snippet"))

    # 3. Page-level evidence.
    if evidence is None or not evidence.fetched:
        reason = (evidence.error if evidence and evidence.error else "page was not fetched")
        signals.append(Signal("no_page_evidence", C.UNKNOWN, 0.5,
                              f"no page-level evidence ({reason})"))
        return signals

    outbound = [u for u in evidence.outbound if u]
    out_domains = {d for d in (domain_of(u) for u in outbound) if d}

    to_official = [u for u in outbound if domain_of(u) in C.OFFICIAL_DOMAINS]
    tracked_official = [u for u in to_official if _affiliate_params(u)]
    other_operators = out_domains & C.KNOWN_OPERATOR_DOMAINS

    final_domain = domain_of(evidence.final_url or "") if evidence.final_url else None

    # Where the page's own redirector links actually land, once followed.
    cloaked = {src: dst for src, dst in (evidence.resolved_internal_links or {}).items() if dst}
    cloaked_official = {src: dst for src, dst in cloaked.items()
                        if domain_of(dst) in C.OFFICIAL_DOMAINS}
    cloaked_operators = {src: dst for src, dst in cloaked.items()
                         if domain_of(dst) in C.KNOWN_OPERATOR_DOMAINS}

    # A cloaked link is still a link to the brand. Rules that ask "does this page
    # send anyone to the official site at all?" have to see it, or a hidden
    # affiliate reads as a page that only promotes competitors.
    official_destinations = to_official + sorted(cloaked_official.values())

    if tracked_official:
        params = sorted({p for u in tracked_official for p in _affiliate_params(u)})
        signals.append(Signal("tracked_link_to_official", C.AFFILIATE, 0.9,
                              f"outbound links to the official domain carry tracking parameters ({', '.join(params)})"))
    elif to_official:
        signals.append(Signal("plain_link_to_official", C.AFFILIATE, 0.35,
                              "links to the official domain, but without tracking parameters"))

    # Cloaked destinations are strong evidence: the click is monetised *and*
    # hidden, which is deliberate — nothing about an honest link needs hiding.
    if cloaked_official:
        src, dst = sorted(cloaked_official.items())[0]
        signals.append(Signal("cloaked_link_to_official", C.AFFILIATE, 0.9,
                              f"internal redirect link {src} is cloaked and resolves to "
                              f"the official domain ({domain_of(dst)})"))
    if cloaked_operators:
        src, dst = sorted(cloaked_operators.items())[0]
        signals.append(Signal("cloaked_link_to_competitor", C.COMPETITOR, 0.9,
                              f"internal redirect link {src} is cloaked and resolves to "
                              f"another operator ({domain_of(dst)})"))

    # Same-domain links only survive extraction if they are redirector-shaped, so
    # any that were never resolved are a known gap, not an absence of evidence.
    # Left unrecorded they would silently strengthen `brand_query_no_official_link`:
    # "links only to competitors" cannot be asserted while an unfollowed link on
    # the page might lead to the brand. Only material when nothing else already
    # proves the page reaches the brand.
    unresolved_internal = [u for u in outbound
                           if domain_of(u) == domain and u not in cloaked]
    if unresolved_internal and not official_destinations and final_domain not in C.OFFICIAL_DOMAINS:
        signals.append(Signal("unresolved_internal_redirect", C.UNKNOWN, 0.3,
                              f"{len(unresolved_internal)} internal redirect link(s) could not be "
                              f"followed, so the destination of the monetised click is unproven "
                              f"(e.g. {unresolved_internal[0]})"))

    if final_domain and final_domain != domain:
        if final_domain in C.OFFICIAL_DOMAINS:
            signals.append(Signal("redirect_to_official", C.AFFILIATE, 0.8,
                                  f"the redirect chain ends at {final_domain}"))
        elif final_domain in C.KNOWN_OPERATOR_DOMAINS:
            signals.append(Signal("redirect_to_competitor", C.COMPETITOR, 0.9,
                                  f"the redirect chain leads to another operator ({final_domain})"))

    if other_operators and not official_destinations and brand_in_text:
        signals.append(Signal("brand_query_no_official_link", C.COMPETITOR, 0.75,
                              "the page targets the brand but links only to other operators: "
                              + ", ".join(sorted(other_operators)[:3])))
    elif other_operators and official_destinations:
        signals.append(Signal("multi_brand_toplist", C.AFFILIATE, 0.25,
                              "comparison list of casinos that includes both the brand and its competitors"))

    # A monetised redirect layer proves *that* traffic is sold; the direction of
    # the money is inferred from what the page actually links to.
    if any(_is_network_host(u) for u in outbound):
        if official_destinations or final_domain in C.OFFICIAL_DOMAINS:
            signals.append(Signal("affiliate_network_link", C.AFFILIATE, 0.35,
                                  "traffic is monetised through a redirect host and lands on the brand"))
        elif other_operators or final_domain in C.KNOWN_OPERATOR_DOMAINS:
            signals.append(Signal("affiliate_network_link", C.COMPETITOR, 0.35,
                                  "traffic is monetised through a redirect host and lands on other operators"))
        else:
            signals.append(Signal("affiliate_network_link", C.UNKNOWN, 0.25,
                                  "a redirect host was detected, but the final beneficiary is undetermined"))

    # Only meaningful when nothing stronger is known about the domain.
    strong = any(s.weight >= 0.5 and s.category in (C.AFFILIATE, C.COMPETITOR) for s in signals)
    if not outbound and not strong:
        signals.append(Signal("no_outbound_links", C.UNKNOWN, 0.4,
                              "the page has no outbound links — monetisation cannot be determined"))

    return signals


# ------------------------------------------------------------------ decision
def decide(signals: list[Signal]) -> Classification:
    scored = [s for s in signals if s.category and s.weight > 0]

    decisive = [s for s in scored if s.decisive]
    if decisive:
        best = max(decisive, key=lambda s: s.weight)
        return Classification(best.category, 0.97, best.detail, signals)

    if not scored:
        return Classification(C.UNKNOWN, 0.3, "no informative signal was found", signals)

    scores: dict[str, float] = defaultdict(float)
    for s in scored:
        scores[s.category] += s.weight
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_cat, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    total = sum(scores.values())

    if best_cat != C.UNKNOWN and best_score >= C.MIN_SCORE and (best_score - second) >= C.MIN_MARGIN:
        confidence = min(0.95, best_score / (total + C.CONF_SMOOTHING))
        if confidence >= C.MIN_CONFIDENCE:
            why = "; ".join(s.detail for s in scored if s.category == best_cat)
            return Classification(best_cat, round(confidence, 2), why, signals)

    # Not enough evidence -> stay in `unknown` on purpose.
    unknown_score = scores.get(C.UNKNOWN, 0.0)
    confidence = round(min(0.6, 0.3 + 0.4 * unknown_score), 2)
    reasons = [s.detail for s in scored if s.category == C.UNKNOWN] or [
        f"signals are contradictory or weak (best hypothesis: {best_cat}, weight {best_score:.2f})"
    ]
    if best_cat != C.UNKNOWN:
        reasons.append(f"best hypothesis is {best_cat} (weight {best_score:.2f}), "
                       f"but the {C.MIN_SCORE}/{C.MIN_CONFIDENCE} threshold was not reached")
    return Classification(C.UNKNOWN, confidence, "; ".join(reasons), signals)


def classify(result: SerpResult, evidence: PageEvidence | None = None) -> Classification:
    return decide(build_signals(result, evidence))
