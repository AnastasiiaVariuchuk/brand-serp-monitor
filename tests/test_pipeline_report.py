import json

import pytest

from brand_serp import config as C
from brand_serp import report
from brand_serp.evidence import (
    FixtureEvidenceProvider,
    HttpEvidenceProvider,
    NullEvidenceProvider,
    extract_outbound_links,
    internal_redirectors,
    looks_like_redirector,
)
from brand_serp.pipeline import run_check
from brand_serp.serp_client import FixtureSerpClient


def run_demo():
    return run_check(
        FixtureSerpClient(C.DEFAULT_SERP_FIXTURE),
        query="starcasino", gl="nl", hl="nl", device="desktop", limit=10,
        evidence_provider=FixtureEvidenceProvider(C.DEFAULT_PAGE_FIXTURE),
    )


def test_end_to_end_demo_run_classifies_expected_domains():
    rows = {r["domain"]: r for r in run_demo().rows}
    assert rows["starcasino.nl"]["category"] == C.OFFICIAL
    assert rows["onlinecasinoground.nl"]["category"] == C.AFFILIATE
    assert rows["starcasinobonus.net"]["category"] == C.AFFILIATE
    assert rows["jackscasino.nl"]["category"] == C.COMPETITOR
    assert rows["casinozonder.nl"]["category"] == C.COMPETITOR
    assert rows["casinobonus-nederland.com"]["category"] == C.UNKNOWN
    assert rows["trustpilot.com"]["category"] == C.UNKNOWN


def test_every_row_has_explanation_and_signals():
    for row in run_demo().rows:
        assert row["explanation"]
        assert isinstance(row["signals"], list)
        assert 0.0 <= row["confidence"] <= 1.0


def test_without_page_evidence_only_official_is_certain():
    run = run_check(FixtureSerpClient(C.DEFAULT_SERP_FIXTURE), query="starcasino",
                    limit=10, evidence_provider=NullEvidenceProvider())
    categories = {r["domain"]: r["category"] for r in run.rows}
    assert categories["starcasino.nl"] == C.OFFICIAL
    non_official = [c for d, c in categories.items() if d != "starcasino.nl"]
    assert all(c in (C.UNKNOWN, C.COMPETITOR) for c in non_official)


class _ExplodingProvider:
    def get(self, url):
        raise RuntimeError("boom")


def test_evidence_provider_failure_does_not_break_the_run():
    run = run_check(FixtureSerpClient(C.DEFAULT_SERP_FIXTURE), query="starcasino",
                    limit=10, evidence_provider=_ExplodingProvider())
    assert len(run.rows) == 10


def test_summary_shares_sum_to_one():
    summary = report.summarize(run_demo().rows)
    assert summary["total_results"] == 10
    assert abs(sum(summary["shares"].values()) - 1.0) < 1e-6
    assert sum(summary["counts"].values()) == 10
    assert summary["unique_domains"] == 9


def test_summary_handles_empty_input():
    summary = report.summarize([])
    assert summary["total_results"] == 0
    assert all(v == 0 for v in summary["counts"].values())


def test_csv_and_json_output(tmp_path):
    rows = run_demo().rows
    csv_path = report.write_csv(tmp_path / "out.csv", rows)
    json_path = report.write_json(tmp_path / "out.json", {"results": rows})
    assert csv_path.exists() and json_path.exists()
    assert "domain" in csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert len(json.loads(json_path.read_text(encoding="utf-8"))["results"]) == 10


def test_extract_outbound_links_filters_internal_and_junk():
    html = """
      <a href="/internal">x</a>
      <a href="https://www.casino.nl/other">same site</a>
      <a href="https://www.starcasino.nl/?btag=1">brand</a>
      <a href="mailto:a@b.nl">mail</a>
      <a href="https://www.starcasino.nl/?btag=1">dup</a>
    """
    links = extract_outbound_links(html, "https://casino.nl/page")
    assert links == ["https://www.starcasino.nl/?btag=1"]


# ------------------------------------------------- internal redirector links
def test_extract_keeps_internal_redirectors_but_drops_navigation():
    html = """
      <a href="/about">about</a>
      <a href="/contact">contact</a>
      <a href="/nieuws/starcasino-2026">article</a>
      <a href="/go/starcasino">cloaked brand CTA</a>
      <a href="/out/?id=123">cloaked</a>
      <a href="/visit/starcasino">cloaked</a>
      <a href="/promo?btag=abc">tracked internal</a>
      <a href="https://www.starcasino.nl/">brand</a>
    """
    links = extract_outbound_links(html, "https://casino.nl/page")
    assert links == [
        "https://casino.nl/go/starcasino",
        "https://casino.nl/out/?id=123",
        "https://casino.nl/visit/starcasino",
        "https://casino.nl/promo?btag=abc",
        "https://www.starcasino.nl/",
    ]


def test_extract_matches_redirector_paths_with_and_without_a_trailing_slash():
    html = '<a href="/out?id=1">a</a><a href="/goto/x">b</a><a href="/linkedin">c</a>'
    links = extract_outbound_links(html, "https://casino.nl/page")
    assert links == ["https://casino.nl/out?id=1", "https://casino.nl/goto/x"]


def test_looks_like_redirector_distinguishes_shape_from_wording():
    assert looks_like_redirector("https://casino.nl/go/starcasino")
    assert looks_like_redirector("https://casino.nl/out/?id=1")
    assert looks_like_redirector("https://casino.nl/promo?btag=abc")
    assert not looks_like_redirector("https://casino.nl/about")
    assert not looks_like_redirector("https://casino.nl/golf-club")
    assert not looks_like_redirector("https://casino.nl/")


def test_internal_redirectors_selects_only_same_domain_links():
    links = ["https://casino.nl/go/x", "https://www.casino.nl/out/?id=1",
             "https://www.starcasino.nl/?btag=1"]
    assert internal_redirectors(links, "casino.nl") == [
        "https://casino.nl/go/x", "https://www.casino.nl/out/?id=1"]
    assert internal_redirectors(links, None) == []


def test_fixture_provider_loads_resolved_links():
    provider = FixtureEvidenceProvider(C.DEFAULT_PAGE_FIXTURE)
    evidence = provider.get("https://casino.nl/aanbieders/starcasino/")
    assert evidence.resolved_internal_links == {
        "https://casino.nl/go/starcasino":
            "https://www.starcasino.nl/?btag=casinonl_2026&subid=aanbieders"}
    assert "resolved_internal_links" in evidence.to_dict()


def test_fixture_provider_defaults_the_field_when_absent(tmp_path):
    path = tmp_path / "pages.json"
    path.write_text(json.dumps({
        "a.nl": {"fetched": True, "outbound": []},
        "b.nl": {"fetched": True, "resolved_internal_links": "not-a-mapping"},
    }), encoding="utf-8")
    provider = FixtureEvidenceProvider(path)
    assert provider.get("https://a.nl/x").resolved_internal_links == {}
    assert provider.get("https://b.nl/x").resolved_internal_links == {}


def test_demo_run_classifies_the_cloaked_affiliate():
    rows = {r["domain"]: r for r in run_demo().rows}
    casino = rows["casino.nl"]
    assert casino["category"] == C.AFFILIATE
    assert "cloaked_link_to_official" in casino["signals"]
    assert "brand_query_no_official_link" not in casino["signals"]


# --------------------------------------------- resolving redirectors offline
class _FakeResponse:
    def __init__(self, url, status_code=200):
        self.url = url
        self.status_code = status_code

    def close(self):
        pass


class _FakeSession:
    """Stands in for `requests.Session` — records calls, never touches a socket."""

    def __init__(self, destinations=None, head_status=200, explode=()):
        self.destinations = destinations or {}
        self.head_status = head_status
        self.explode = set(explode)
        self.head_calls: list[str] = []
        self.get_calls: list[str] = []
        self.max_redirects = None
        self.closed = False

    def _respond(self, url, status):
        if url in self.explode:
            raise RuntimeError("connection reset")
        return _FakeResponse(self.destinations.get(url, url), status)

    def head(self, url, **kw):
        self.head_calls.append(url)
        return self._respond(url, self.head_status)

    def get(self, url, **kw):
        self.get_calls.append(url)
        return self._respond(url, 200)

    def close(self):
        self.closed = True


class _FakeRequests:
    def __init__(self, session):
        self._session = session

    def Session(self):          # noqa: N802 - mirrors the requests API
        return self._session


def resolve(session, urls):
    provider = HttpEvidenceProvider(timeout=15.0)
    return provider._resolve_internal(urls, _FakeRequests(session))


def test_resolution_follows_redirectors_with_head():
    session = _FakeSession({"https://casino.nl/go/star": "https://www.starcasino.nl/?btag=1"})
    assert resolve(session, ["https://casino.nl/go/star"]) == {
        "https://casino.nl/go/star": "https://www.starcasino.nl/?btag=1"}
    assert session.head_calls == ["https://casino.nl/go/star"]
    assert session.get_calls == []          # HEAD succeeded, no GET needed
    assert session.closed


def test_resolution_falls_back_to_get_when_head_is_rejected():
    session = _FakeSession({"https://casino.nl/go/star": "https://www.starcasino.nl/"},
                           head_status=405)
    assert resolve(session, ["https://casino.nl/go/star"]) == {
        "https://casino.nl/go/star": "https://www.starcasino.nl/"}
    assert session.get_calls == ["https://casino.nl/go/star"]


def test_resolution_caps_the_number_of_links_per_page():
    urls = [f"https://casino.nl/go/{i}" for i in range(25)]
    session = _FakeSession({u: "https://www.starcasino.nl/" for u in urls})
    assert len(resolve(session, urls)) == C.MAX_INTERNAL_REDIRECTS
    assert len(session.head_calls) == C.MAX_INTERNAL_REDIRECTS


def test_resolution_applies_a_redirect_hop_limit():
    session = _FakeSession()
    resolve(session, ["https://casino.nl/go/star"])
    assert session.max_redirects == C.MAX_REDIRECT_HOPS


def test_one_broken_redirector_does_not_lose_the_others():
    good, bad = "https://casino.nl/go/star", "https://casino.nl/go/dead"
    session = _FakeSession({good: "https://www.starcasino.nl/"}, explode=[bad])
    assert resolve(session, [bad, good]) == {good: "https://www.starcasino.nl/"}


def test_links_that_do_not_redirect_are_not_recorded():
    """A 200 with no redirect is a real page, not a cloaked destination."""
    session = _FakeSession()      # every URL resolves to itself
    assert resolve(session, ["https://casino.nl/go/star"]) == {}


def test_permanently_failing_redirector_is_skipped():
    session = _FakeSession(head_status=404)
    assert resolve(session, ["https://casino.nl/go/star"]) == {}


def test_a_broken_http_client_degrades_instead_of_raising():
    """Evidence collection must never raise — the page still gets classified."""
    class _BrokenRequests:
        def Session(self):      # noqa: N802 - mirrors the requests API
            raise ImportError("requests is not installed")

    provider = HttpEvidenceProvider()
    assert provider._resolve_internal(["https://casino.nl/go/x"], _BrokenRequests()) == {}


def test_no_links_means_no_session_is_opened():
    session = _FakeSession()
    assert resolve(session, []) == {}
    assert session.head_calls == [] and not session.closed
