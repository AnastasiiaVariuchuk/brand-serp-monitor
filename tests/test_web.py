import json
import threading
from urllib.request import urlopen

import pytest

from brand_serp import web
from brand_serp.storage import Storage

ROW = dict(title="t", explanation="because", classifier="rules")


def row(position, domain, category="affiliate", confidence=0.8, signals=None):
    return {**ROW, "position": position, "domain": domain,
            "url": f"https://{domain}/", "category": category,
            "confidence": confidence, "signals": signals or ["review_markers"]}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "web.sqlite3"
    with Storage(path) as storage:
        storage.save_check(
            query="starcasino", gl="nl", hl="nl", device="desktop", source="fixture",
            checked_at="2026-01-01T00:00:00+00:00",
            rows=[row(1, "starcasino.nl", "official", 0.97),
                  row(2, "casino.nl"),
                  row(3, "jackscasino.nl", "competitor")],
        )
        storage.save_check(
            query="starcasino", gl="nl", hl="nl", device="desktop", source="fixture",
            checked_at="2026-01-02T00:00:00+00:00",
            rows=[row(1, "jackscasino.nl", "competitor"),
                  row(2, "starcasino.nl", "official", 0.97),
                  row(3, "newcomer.nl", "unknown", 0.4)],
        )
    return path


# ------------------------------------------------------------------ view model
def test_dashboard_is_empty_without_checks(tmp_path):
    with Storage(tmp_path / "blank.sqlite3") as storage:
        view = web.build_dashboard(storage)
    assert view["check"] is None
    assert view["rows"] == []
    assert view["options"] == {"query": [], "gl": [], "device": []}


def test_dashboard_defaults_to_the_latest_check(db):
    with Storage(db) as storage:
        view = web.build_dashboard(storage)
    assert view["check"]["checked_at"] == "2026-01-02T00:00:00+00:00"
    assert view["previous"]["checked_at"] == "2026-01-01T00:00:00+00:00"
    assert [r["domain"] for r in view["rows"]] == [
        "jackscasino.nl", "starcasino.nl", "newcomer.nl"]


def test_dashboard_counts_competitors_above_the_official_site(db):
    with Storage(db) as storage:
        view = web.build_dashboard(storage)
    assert view["official_position"] == 2
    assert view["competitors_above_official"] == 1


def test_dashboard_reports_no_official_domain_as_none(tmp_path):
    path = tmp_path / "no_official.sqlite3"
    with Storage(path) as storage:
        storage.save_check(query="q", gl="nl", hl="nl", device="desktop",
                           source="fixture", rows=[row(1, "casino.nl")])
        view = web.build_dashboard(storage)
    assert view["official_position"] is None
    assert view["competitors_above_official"] == 0


def test_dashboard_diffs_against_the_previous_check(db):
    with Storage(db) as storage:
        view = web.build_dashboard(storage)
    changes = view["changes"]
    assert [c["domain"] for c in changes["appeared"]] == ["newcomer.nl"]
    assert [c["domain"] for c in changes["disappeared"]] == ["casino.nl"]
    assert {c["domain"] for c in changes["moved"]} == {"jackscasino.nl", "starcasino.nl"}


def test_oldest_check_has_no_previous_to_compare_with(db):
    with Storage(db) as storage:
        checks = storage.list_checks()
        view = web.build_dashboard(storage, check_id=checks[-1]["id"])
    assert view["previous"] is None
    assert view["changes"] is None


def test_unknown_check_id_falls_back_to_the_latest(db):
    with Storage(db) as storage:
        view = web.build_dashboard(storage, check_id=99999)
    assert view["check"]["checked_at"] == "2026-01-02T00:00:00+00:00"


def test_filters_narrow_the_check_list(db):
    with Storage(db) as storage:
        assert web.build_dashboard(storage, device="mobile")["check"] is None
        assert web.build_dashboard(storage, device="desktop")["check"] is not None


def test_snapshot_keys_feed_the_filter_dropdowns(db):
    with Storage(db) as storage:
        assert storage.snapshot_keys() == {
            "query": ["starcasino"], "gl": ["nl"], "device": ["desktop"]}


# ---------------------------------------------------------------------- render
def test_render_produces_a_self_contained_page(db):
    with Storage(db) as storage:
        page = web.render_dashboard(web.build_dashboard(storage))
    assert page.startswith("<!doctype html>")
    assert "jackscasino.nl" in page and "newcomer.nl" in page
    assert "http://" not in page.split("<body>")[0]  # no external assets in <head>


def test_render_escapes_untrusted_serp_text(tmp_path):
    path = tmp_path / "xss.sqlite3"
    evil = '<script>alert("xss")</script>'
    with Storage(path) as storage:
        storage.save_check(query=evil, gl="nl", hl="nl", device="desktop", source="fixture",
                           rows=[{**row(1, "casino.nl"), "title": evil, "explanation": evil}])
        page = web.render_dashboard(web.build_dashboard(storage))
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_render_handles_an_empty_database(tmp_path):
    with Storage(tmp_path / "blank.sqlite3") as storage:
        page = web.render_dashboard(web.build_dashboard(storage))
    assert "No checks stored yet" in page


# ------------------------------------------------------------------ http layer
@pytest.fixture
def server(db):
    httpd = web.make_server(db, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_index_serves_html(server):
    with urlopen(server + "/") as response:
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        assert b"jackscasino.nl" in response.read()


def test_api_serves_the_view_model_as_json(server):
    with urlopen(server + "/api/dashboard") as response:
        payload = json.loads(response.read())
    assert payload["official_position"] == 2
    assert len(payload["rows"]) == 3


def test_query_parameters_select_the_check(server, db):
    with Storage(db) as storage:
        oldest = storage.list_checks()[-1]["id"]
    with urlopen(f"{server}/api/dashboard?check={oldest}&device=desktop") as response:
        payload = json.loads(response.read())
    assert payload["check"]["id"] == oldest
    assert payload["changes"] is None


def test_malformed_check_parameter_does_not_crash(server):
    with urlopen(server + "/api/dashboard?check=not-a-number") as response:
        assert json.loads(response.read())["check"] is not None


def test_healthz_and_unknown_paths(server):
    with urlopen(server + "/healthz") as response:
        assert response.read() == b"ok"
    with pytest.raises(Exception) as exc:
        urlopen(server + "/nope")
    assert "404" in str(exc.value)
