import pytest

from brand_serp import config as C
from brand_serp.classifier import classify
from brand_serp.models import PageEvidence, SerpResult


def ev(url, **kw):
    kw.setdefault("fetched", True)
    kw.setdefault("final_url", url)
    return PageEvidence(url=url, **kw)


def test_official_domain_is_decisive_without_evidence():
    r = SerpResult(1, "https://www.starcasino.nl/", "StarCasino.nl", "officieel")
    c = classify(r, None)
    assert c.category == C.OFFICIAL and c.confidence >= 0.9


def test_tracked_link_to_official_is_affiliate():
    url = "https://www.onlinecasinoground.nl/starcasino-review/"
    r = SerpResult(3, url, "StarCasino review 2026 bonus", "ervaringen en gratis spins")
    c = classify(r, ev(url, outbound=["https://www.starcasino.nl/?btag=ocg_2026"]))
    assert c.category == C.AFFILIATE
    assert c.confidence >= C.MIN_CONFIDENCE
    assert any(s.name == "tracked_link_to_official" for s in c.signals)


def test_redirect_to_official_is_affiliate():
    url = "https://starcasinobonus.net/nl/promocode"
    r = SerpResult(6, url, "StarCasino bonus code", "claim je gratis spins")
    c = classify(r, ev(url, final_url="https://www.starcasino.nl/?btag=x&clickid=1",
                       outbound=["https://go.starcasinobonus.net/out?clickid=1"]))
    assert c.category == C.AFFILIATE


def test_known_operator_domain_is_competitor():
    url = "https://www.jackscasino.nl/promoties"
    r = SerpResult(5, url, "Jacks.nl", "Op zoek naar StarCasino?")
    c = classify(r, ev(url, outbound=[]))
    assert c.category == C.COMPETITOR


def test_brand_page_linking_only_to_other_operators_is_competitor():
    url = "https://casinozonder.nl/starcasino-alternatief"
    r = SerpResult(8, url, "StarCasino alternatief", "Zoek je StarCasino?")
    c = classify(r, ev(url, outbound=[
        "https://www.unibet.nl/?aff_id=1",
        "https://www.betcity.nl/?aff_id=1",
        "https://track.casinozonder.nl/go?to=unibet",
    ]))
    assert c.category == C.COMPETITOR
    assert "brand_query_no_official_link" in [s.name for s in c.signals]


def test_unfetchable_page_stays_unknown():
    url = "https://casinobonus-nederland.com/starcasino/"
    r = SerpResult(10, url, "StarCasino aanbieding", "alles over de aanbieding")
    c = classify(r, PageEvidence(url=url, fetched=False, error="HTTP 403"))
    assert c.category == C.UNKNOWN
    assert c.confidence <= C.MIN_CONFIDENCE


def test_neutral_domain_stays_unknown():
    url = "https://nl.trustpilot.com/review/starcasino.nl"
    r = SerpResult(9, url, "StarCasino Reviews", "klantreviews")
    c = classify(r, ev(url, outbound=["https://www.starcasino.nl/"]))
    assert c.category == C.UNKNOWN


def test_weak_single_signal_does_not_reach_main_category():
    """A plain link to the brand alone is not enough to call a site affiliate."""
    url = "https://www.casinonieuws.nl/nieuws/starcasino-sponsordeal-2026/"
    r = SerpResult(7, url, "StarCasino verlengt sponsordeal", "de operator kondigde aan")
    c = classify(r, ev(url, outbound=["https://www.starcasino.nl/"]))
    assert c.category == C.UNKNOWN


def test_unparsable_url_is_unknown():
    r = SerpResult(1, "not-a-url", "x", "y")
    assert classify(r, None).category == C.UNKNOWN


@pytest.mark.parametrize("category", list(C.CATEGORIES))
def test_confidence_always_in_range(category):
    r = SerpResult(1, "https://example.com/", "StarCasino", "review bonus")
    c = classify(r, None)
    assert 0.0 <= c.confidence <= 1.0


# ------------------------------------------------- cloaked internal redirects
def test_cloaked_link_to_brand_turns_an_unknown_page_into_affiliate():
    """The page shows no link to the brand; only following /go/ reveals one."""
    url = "https://www.casinonieuws.nl/nieuws/starcasino-sponsordeal-2026/"
    r = SerpResult(7, url, "StarCasino bonus review", "ervaringen en gratis spins")
    plain = ev(url, outbound=["https://www.casinonieuws.nl/go/starcasino"])
    assert classify(r, plain).category == C.UNKNOWN      # without resolution: unresolvable

    cloaked = ev(url, outbound=["https://www.casinonieuws.nl/go/starcasino"],
                 resolved_internal_links={
                     "https://www.casinonieuws.nl/go/starcasino":
                         "https://www.starcasino.nl/?btag=cn_2026"})
    c = classify(r, cloaked)
    assert c.category == C.AFFILIATE
    assert c.confidence >= C.MIN_CONFIDENCE
    assert "cloaked_link_to_official" in [s.name for s in c.signals]


def test_cloaked_link_to_another_operator_is_competitor():
    url = "https://casinovergelijker.nl/starcasino-alternatief"
    r = SerpResult(4, url, "StarCasino alternatief", "beste casino vergelijk")
    c = classify(r, ev(url, outbound=["https://casinovergelijker.nl/out/?id=9"],
                       resolved_internal_links={
                           "https://casinovergelijker.nl/out/?id=9":
                               "https://www.unibet.nl/?aff_id=cv9"}))
    assert c.category == C.COMPETITOR
    assert "cloaked_link_to_competitor" in [s.name for s in c.signals]


def test_cloaked_detail_names_the_resolved_destination():
    """The explanation has to stay auditable: say it was cloaked, and where to."""
    url = "https://casino.nl/aanbieders/starcasino/"
    r = SerpResult(4, url, "StarCasino review", "bonus vergelijk")
    c = classify(r, ev(url, outbound=["https://casino.nl/go/starcasino"],
                       resolved_internal_links={
                           "https://casino.nl/go/starcasino":
                               "https://www.starcasino.nl/?btag=x"}))
    detail = next(s.detail for s in c.signals if s.name == "cloaked_link_to_official")
    assert "cloaked" in detail
    assert "https://casino.nl/go/starcasino" in detail
    assert "starcasino.nl" in detail


URL_TOPLIST = "https://casino.nl/aanbieders/starcasino/"
OUTBOUND_TOPLIST = ["https://casino.nl/go/starcasino",
                    "https://www.hollandcasino.nl/", "https://www.betcity.nl/"]


def toplist_result():
    return SerpResult(4, URL_TOPLIST, "StarCasino beoordeling", "vergelijk beste casino")


def test_resolved_cloaked_brand_link_makes_a_toplist_affiliate():
    c = classify(toplist_result(), ev(URL_TOPLIST, outbound=OUTBOUND_TOPLIST,
                                      resolved_internal_links={
                                          "https://casino.nl/go/starcasino":
                                              "https://www.starcasino.nl/?btag=c"}))
    assert c.category == C.AFFILIATE
    assert "brand_query_no_official_link" not in [s.name for s in c.signals]


def test_unfollowed_cloaked_link_stays_unknown_rather_than_guessing_competitor():
    """We know a hidden link exists and could not follow it — that is missing
    evidence, not evidence of absence. Asserting `competitor` here would mean
    claiming the page links only to other operators, which is exactly what the
    unfollowed link leaves unproven."""
    c = classify(toplist_result(), ev(URL_TOPLIST, outbound=OUTBOUND_TOPLIST))
    assert c.category == C.UNKNOWN
    assert "unresolved_internal_redirect" in [s.name for s in c.signals]


def test_competitor_only_page_without_any_redirector_is_still_competitor():
    """The original rule must keep working where nothing is hidden."""
    c = classify(toplist_result(), ev(URL_TOPLIST, outbound=[
        "https://www.hollandcasino.nl/", "https://www.betcity.nl/"]))
    assert c.category == C.COMPETITOR
    assert "brand_query_no_official_link" in [s.name for s in c.signals]
    assert "unresolved_internal_redirect" not in [s.name for s in c.signals]


def test_unresolved_redirect_is_not_flagged_when_the_brand_is_reached_anyway():
    """No gap worth recording if the page demonstrably sends traffic to the brand."""
    c = classify(toplist_result(), ev(URL_TOPLIST, outbound=OUTBOUND_TOPLIST + [
        "https://www.starcasino.nl/?btag=direct"]))
    assert "unresolved_internal_redirect" not in [s.name for s in c.signals]
    assert c.category == C.AFFILIATE


def test_resolution_to_an_unrelated_domain_adds_no_verdict():
    url = "https://casinozonder.nl/starcasino-alternatief"
    r = SerpResult(8, url, "StarCasino", "review")
    c = classify(r, ev(url, outbound=["https://casinozonder.nl/go/partner"],
                       resolved_internal_links={
                           "https://casinozonder.nl/go/partner": "https://example.org/"}))
    names = [s.name for s in c.signals]
    assert "cloaked_link_to_official" not in names
    assert "cloaked_link_to_competitor" not in names


def test_page_without_resolution_data_behaves_exactly_as_before():
    """Every pre-existing verdict must be untouched when the field is empty."""
    url = "https://www.onlinecasinoground.nl/starcasino-review/"
    r = SerpResult(3, url, "StarCasino review 2026 bonus", "ervaringen en gratis spins")
    outbound = ["https://www.starcasino.nl/?btag=ocg_2026"]
    before = classify(r, ev(url, outbound=outbound))
    after = classify(r, ev(url, outbound=outbound, resolved_internal_links={}))
    assert before.category == after.category == C.AFFILIATE
    assert before.confidence == after.confidence
    assert [s.name for s in before.signals] == [s.name for s in after.signals]
