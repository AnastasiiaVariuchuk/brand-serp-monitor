from brand_serp.history import by_domain, diff
from brand_serp.storage import Storage

ROW = dict(url="https://x.nl/", title="t", explanation="", classifier="rules", signals=[])


def row(position, domain, category="affiliate", confidence=0.8):
    return {**ROW, "position": position, "domain": domain,
            "url": f"https://{domain}/", "category": category, "confidence": confidence}


def test_by_domain_keeps_best_position():
    grouped = by_domain([row(4, "starcasino.nl"), row(1, "starcasino.nl")])
    assert grouped["starcasino.nl"]["position"] == 1


def test_diff_detects_all_change_types():
    previous = [row(1, "starcasino.nl", "official"), row(2, "casino.nl"),
                row(3, "old.nl", "unknown")]
    current = [row(1, "starcasino.nl", "official"), row(4, "casino.nl"),
               row(2, "new.nl", "competitor"), row(3, "old2.nl", "unknown")]
    changes = diff(previous, current)
    assert [c["domain"] for c in changes["appeared"]] == ["new.nl", "old2.nl"]
    assert [c["domain"] for c in changes["disappeared"]] == ["old.nl"]
    assert changes["moved"] == [{"domain": "casino.nl", "from": 2, "to": 4, "delta": -2}]
    assert changes["recategorized"] == []


def test_diff_detects_category_change():
    previous = [row(3, "flip.nl", "affiliate")]
    current = [row(3, "flip.nl", "competitor")]
    changes = diff(previous, current)
    assert changes["recategorized"][0]["from"] == "affiliate"
    assert changes["recategorized"][0]["to"] == "competitor"
    assert changes["moved"] == []


def test_storage_roundtrip_and_history(tmp_path):
    db = tmp_path / "test.sqlite3"
    with Storage(db) as storage:
        first = storage.save_check(query="starcasino", gl="nl", hl="nl", device="desktop",
                                   source="fixture", rows=[row(1, "starcasino.nl", "official"),
                                                           row(2, "casino.nl")],
                                   checked_at="2026-09-01T08:00:00+00:00")
        second = storage.save_check(query="starcasino", gl="nl", hl="nl", device="desktop",
                                    source="fixture", rows=[row(1, "starcasino.nl", "official"),
                                                            row(5, "casino.nl")],
                                    checked_at="2026-09-02T08:00:00+00:00")
        assert first != second
        checks = storage.last_two_checks("starcasino", "nl", "desktop")
        assert [c["id"] for c in checks] == [second, first]

        stored = storage.results_for_check(first)
        assert len(stored) == 2 and stored[0]["domain"] == "starcasino.nl"
        assert isinstance(stored[0]["signals"], list)

        changes = diff(storage.results_for_check(first), storage.results_for_check(second))
        assert changes["moved"] == [{"domain": "casino.nl", "from": 2, "to": 5, "delta": -3}]

        timeline = storage.domain_timeline("casino.nl", "starcasino")
        assert [p["position"] for p in timeline] == [2, 5]


def test_storage_filters_by_query(tmp_path):
    with Storage(tmp_path / "t.sqlite3") as storage:
        storage.save_check(query="starcasino", gl="nl", hl="nl", device="desktop",
                           source="fixture", rows=[row(1, "a.nl")])
        storage.save_check(query="star casino nl", gl="nl", hl="nl", device="mobile",
                           source="fixture", rows=[row(1, "b.nl")])
        assert len(storage.list_checks(query="starcasino")) == 1
        assert len(storage.list_checks(device="mobile")) == 1
        assert len(storage.list_checks()) == 2
