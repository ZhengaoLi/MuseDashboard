import json
import sqlite3
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
HUMAN = "human-token-0123456789abcdef"
CHRON = "chronicle-token-0123456789abcdef"
C_AUTH = {"Authorization": "Bearer " + CHRON}


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", HUMAN)
    monkeypatch.setenv("CHRONICLE_TOKEN", CHRON)
    from app.main import app
    with TestClient(app) as cl:
        yield cl


def ago(minutes=0, hours=0, tz="-04:00"):
    sign = -1 if tz.startswith("-") else 1
    off = timezone(timedelta(hours=sign * int(tz[1:3])))
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours)).astimezone(off).isoformat(timespec="seconds")


def ev(c, body, headers=None):
    return c.post("/api/context/events", json=body, headers=C_AUTH if headers is None else headers)


def ctx(c):
    return c.get("/api/state").json()["context"]


def test_empty_state_shape(c):
    x = ctx(c)
    assert x["latest"] is None and x["recent"] == [] and x["ttl_minutes"] == 30


@pytest.mark.parametrize("event,typ,place", [("arrived_home", "geofence_enter", "home"), ("left_home", "geofence_exit", "home"),
                                              ("arrived_office", "geofence_enter", "office"), ("left_office", "geofence_exit", "office")])
def test_valid_native_events(c, event, typ, place):
    r = ev(c, {"event": event, "device": "iphone", "ts": ago(1)})
    assert r.status_code == 200 and r.json()["stored"] is True
    l = ctx(c)["latest"]
    assert (l["type"], l["place"], l["source"], l["fresh"]) == (typ, place, "chronicle", True) and l["age_seconds"] < 120


def test_canonical_format_accepted(c):
    r = ev(c, {"event_id": "chronicle-abc-1", "source": "chronicle", "type": "geofence_enter", "place": "home", "observed_at": ago(2)})
    assert r.status_code == 200 and r.json()["stored"]
    assert ctx(c)["latest"]["place"] == "home"
    assert ev(c, {"type": "geofence_enter", "place": "gym", "observed_at": ago(1)}).status_code == 422


def test_auth_boundaries(c):
    body = {"event": "arrived_home", "ts": ago(1)}
    assert ev(c, body, {}).status_code == 401
    assert ev(c, body, {"Authorization": "Bearer nope"}).status_code == 401
    assert ev(c, body, {"Authorization": "Bearer " + TOKEN}).status_code == 401          # Muse's token cannot post events
    assert ev(c, body, {"Authorization": "Bearer " + HUMAN}).status_code == 401
    assert c.post("/api/update", json={"heartbeat": True}, headers=C_AUTH).status_code == 401     # chronicle token cannot publish
    assert c.post("/api/action", json={"type": "done", "section": "queue", "id": "x"}, headers=C_AUTH).status_code == 401
    assert ctx(c)["latest"] is None
    assert CHRON not in c.get("/api/state").text


def test_disabled_without_token(c, monkeypatch):
    monkeypatch.delenv("CHRONICLE_TOKEN")
    assert ev(c, {"event": "arrived_home", "ts": ago(1)}).status_code == 503


def test_duplicates(c):
    t = ago(3)
    assert ev(c, {"event": "arrived_home", "ts": t, "event_id": "evt-12345"}).json()["stored"]
    d = ev(c, {"event": "arrived_home", "ts": t, "event_id": "evt-12345"}).json()           # same id
    assert d["duplicate"] and not d["stored"]
    assert ev(c, {"event": "arrived_home", "ts": t}).json()["duplicate"]                     # same (event, ts) without id
    assert ev(c, {"event": "arrived_home", "ts": ago(1)}).json()["duplicate"]                 # same event within 10 minutes: keep first
    assert ev(c, {"event": "left_home", "ts": ago(2)}).json()["stored"]                       # different event is fine
    assert len(ctx(c)["recent"]) == 2


def test_out_of_order_does_not_overwrite_newer_state(c):
    assert ev(c, {"event": "arrived_office", "ts": ago(2)}).json()["stored"]
    r = ev(c, {"event": "left_home", "ts": ago(25)}).json()                                   # arrives late, happened earlier
    assert r["stored"] and r["superseded"] is True
    l = ctx(c)["latest"]
    assert (l["type"], l["place"]) == ("geofence_enter", "office")
    assert [(e["type"], e["place"]) for e in ctx(c)["recent"]] == [("geofence_enter", "office"), ("geofence_exit", "home")]


def test_observed_time_wins_over_receive_time(c):
    ev(c, {"event": "arrived_home", "ts": ago(20)})
    l = ctx(c)["latest"]
    assert 19 * 60 <= l["age_seconds"] <= 21 * 60 and l["observed_at"] != l["received_at"]


def test_bad_timestamps(c):
    fut = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    for ts, why in ((fut, "future"), (ago(hours=60), "too old"), ("2026-10-19T09:12:00", "naive"), ("yesterday", "garbage"), ("", "empty"), ("2026-13-40T00:00:00+00:00", "invalid")):
        assert ev(c, {"event": "arrived_home", "ts": ts}).status_code == 422, why
    assert ev(c, {"event": "arrived_home", "ts": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(timespec="seconds")}).status_code == 200   # small clock skew ok
    assert ctx(c)["latest"] is not None and len(ctx(c)["recent"]) == 1


def test_vocabulary_whitelist_and_ignored_known_events(c):
    for e in ("arrived_gym", "left_gym", "woke_up"):
        r = ev(c, {"event": e, "ts": ago(1)})
        assert r.status_code == 200 and r.json()["ignored"] is True
    for e in ("arrived_mars", "rm -rf", "ARRIVED_HOME", "arrived_home;ls"):
        assert ev(c, {"event": e, "ts": ago(1)}).status_code == 422, e
    assert ev(c, {"event": "arrived_home", "device": "android", "ts": ago(1)}).status_code == 422
    assert ctx(c)["latest"] is None


def test_free_form_instructions_rejected(c):
    for extra in ({"cmd": "do something"}, {"note": "hi"}, {"focus": {"title": "x"}}, {"threads": []}, {"ui_actions": []}, {"queue": [{"id": "a"}]}):
        assert ev(c, {"event": "arrived_home", "ts": ago(1), **extra}).status_code == 422, extra
    assert ev(c, ["arrived_home"]).status_code == 422
    assert c.post("/api/context/events", content=b"{not json", headers=C_AUTH).status_code == 400
    assert c.post("/api/context/events", content=b"x" * 3000, headers=C_AUTH).status_code == 413


def test_endpoint_cannot_change_dashboard_content(c):
    H = {"Authorization": "Bearer " + TOKEN}
    c.post("/api/update", json={"focus": {"title": "F"}, "threads": [{"id": "t", "title": "T", "area": "life", "ui_actions": [{"id": "ua", "label": "L", "intent": "go_on"}]}],
                                "queue": [{"id": "q", "title": "Q", "planned_for": "2026-10-19"}], "deadlines": [{"id": "d", "title": "D", "due": "2026-10-20"}]}, headers=H)
    before = c.get("/api/state").json()
    def clean(rows):
        return [{k: v for k, v in r.items() if not k.endswith("_seconds") and k != "overdue"} for r in rows]
    for e in ("arrived_home", "left_home", "arrived_office", "left_office"):
        ev(c, {"event": e, "ts": ago(1 + ["arrived_home", "left_home", "arrived_office", "left_office"].index(e) * 12)})
    after = c.get("/api/state").json()
    for k in ("focus", "threads", "queue", "deadlines", "schedule", "automations", "inbox", "journal", "actions"):
        assert clean(before[k]) == clean(after[k]) if isinstance(before[k], list) else before[k] == after[k], k
    assert after["muse"]["last_update"] == before["muse"]["last_update"]                       # does not count as a Muse update
    conn = sqlite3.connect(os.environ["MUSE_DB"])
    assert conn.execute("select count(*) from audit").fetchone()[0] == 1                       # no audit entry from context events


def test_ttl_freshness(c):
    ev(c, {"event": "left_office", "ts": ago(29)})
    assert ctx(c)["latest"]["fresh"] is True
    c2 = ago(45)
    ev(c, {"event": "arrived_home", "ts": c2})                                                 # older, not latest
    assert ctx(c)["latest"]["place"] == "office"


def test_stale_latest_is_not_fresh(c):
    ev(c, {"event": "arrived_home", "ts": ago(45)})
    l = ctx(c)["latest"]
    assert l is not None and l["fresh"] is False and l["age_seconds"] >= 44 * 60               # kept for Muse, not a live position
    assert ctx(c)["recent"]                                                                    # still available as history


def test_prunes_old_rows_and_keeps_minimal_fields(c):
    ev(c, {"event": "arrived_home", "ts": ago(5)})
    conn = sqlite3.connect(os.environ["MUSE_DB"])
    conn.execute("INSERT INTO context_events(event_key,source,type,place,observed_ts,observed_at,received_at) VALUES('old','chronicle','geofence_enter','home',?,?,?)",
                 (time.time() - 40 * 86400, "x", "x")); conn.commit()
    ev(c, {"event": "left_home", "ts": ago(30)})
    assert conn.execute("select count(*) from context_events where event_key='old'").fetchone()[0] == 0
    cols = [r[1] for r in conn.execute("pragma table_info(context_events)")]
    assert not any(x in cols for x in ("lat", "lon", "latitude", "longitude", "device", "coords"))


def test_existing_features_unaffected(c):
    H = {"Authorization": "Bearer " + TOKEN}
    assert c.post("/api/update", json={"queue": [{"id": "q", "title": "Q"}]}, headers=H).status_code == 200
    assert c.get("/api/health").json()["status"] == "ok"
    assert c.get("/api/state").json()["queue"][0]["id"] == "q"
