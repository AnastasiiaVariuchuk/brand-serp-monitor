import json

import pytest

from brand_serp import config as C
from brand_serp.serp_client import (EmptySerpResponse, FallbackSerpClient,
                                    FixtureSerpClient, SerpError, parse_organic)


def test_parse_fixture_returns_ten_sorted_results():
    client = FixtureSerpClient(C.DEFAULT_SERP_FIXTURE)
    results = client.search("starcasino", limit=10)
    assert len(results) == 10
    assert [r.position for r in results] == sorted(r.position for r in results)
    assert results[0].domain == "starcasino.nl"


def test_parse_skips_entries_without_link():
    payload = {"organic_results": [
        {"position": 1, "title": "no link"},
        {"position": 2, "link": "https://casino.nl/a", "title": "ok"},
        {"position": 3, "link": "   "},
        "garbage",
    ]}
    results = parse_organic(payload)
    assert len(results) == 1 and results[0].domain == "casino.nl"


def test_missing_positions_are_backfilled():
    payload = {"organic_results": [
        {"link": "https://a.nl"}, {"link": "https://b.nl"},
    ]}
    assert [r.position for r in parse_organic(payload)] == [1, 2]


def test_empty_payload_raises():
    with pytest.raises(EmptySerpResponse):
        parse_organic({"organic_results": []})
    with pytest.raises(EmptySerpResponse):
        parse_organic({})
    with pytest.raises(EmptySerpResponse):
        parse_organic("not a dict")


def test_api_error_field_raises_serp_error():
    with pytest.raises(SerpError):
        parse_organic({"error": "Your account has run out of searches."})


def test_missing_fixture_file_raises_serp_error(tmp_path):
    with pytest.raises(SerpError):
        FixtureSerpClient(tmp_path / "nope.json").search("starcasino")


def test_invalid_json_fixture_raises_serp_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SerpError):
        FixtureSerpClient(bad).search("starcasino")


class _BrokenClient:
    name = "broken"

    def search(self, **kwargs):
        raise SerpError("503 from provider")


def test_fallback_client_switches_to_fixture():
    client = FallbackSerpClient(_BrokenClient(), FixtureSerpClient(C.DEFAULT_SERP_FIXTURE))
    results = client.search(query="starcasino", limit=10)
    assert len(results) == 10
    assert client.name == "fixture"
