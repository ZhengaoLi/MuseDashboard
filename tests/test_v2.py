import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
HUMAN = "human-token-0123456789abcdef"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", HUMAN)
    from app.main import app
    with TestClient(app) as cl:
        yield cl


M = {"Authorization": "Bearer " + TOKEN}
Hh = {"Authorization": "Bearer " + HUMAN}


def post(c, body, headers=None):
    return c.post("/api/update", json=body, headers={**M, **(headers or {})})


def act(c, body, headers=None):
    return c.post("/api/action", json=body, headers=Hh if headers is None else headers)


def seed(c):
    r = post(c, {"threads": [{"id": "bike", "title": "Bike", "area": "purchase", "template": "purchase",
                              "stages": ["Browsing", "Test ride", "Negotiation", "Bought"], "stage": "Browsing"}],
                 "queue": [{"id": "q1", "title": "Call", "thread": "bike", "status": "active"}],
                 "deadlines": [{"id": "d1", "title": "Due", "due": "2999-01-01", "thread": "bike"}],
                 "inbox": [{"id": "new_payee", "title": "New payee", "kind": "confirm", "sensitive": True, "status": "open"},
                           {"id": "pick", "title": "Which?", "kind": "decision", "options": ["A", "B"], "status": "open"}]})
    assert r.status_code == 200, r.text


def test_v1_payloads_still_work_and_state_superset(c):
    assert post(c, {"queue": [{"id": "a", "title": "A"}], "focus": {"title": "F"}, "journal": "x"}).status_code == 200
    s = c.get("/api/state").json()
    for k in ("focus", "queue", "deadlines", "projects", "schedule", "automations", "journal", "threads", "inbox", "areas", "actions", "human_actions_enabled"):
        assert k in s
    assert [a["id"] for a in s["areas"]][:3] == ["research", "course", "career"]
    assert s["human_actions_enabled"] is True


def test_threads_links_and_validation(c):
    seed(c)
    s = c.get("/api/state").json()
    assert s["queue"][0]["thread"] == "bike" and s["deadlines"][0]["when_ts"] > 0
    assert post(c, {"queue": [{"id": "q2", "title": "x", "thread": "nope"}]}).status_code == 422
    assert post(c, {"threads": [{"id": "t2", "title": "T"}]}).status_code == 422          # area required
    assert post(c, {"threads": [{"id": "t2", "area": "bogus"}]}).status_code == 422
    assert post(c, {"threads": [{"id": "bike", "stage": "Nope"}]}).status_code == 422
    assert post(c, {"queue": [{"id": "q1", "merged_into": "q1"}]}).status_code == 422
    assert post(c, {"queue": [{"id": "q3", "title": "dup", "merged_into": "q1"}]}).status_code == 200
    r = post(c, {"threads": [{"id": "bike", "delete": True}]})
    assert r.status_code == 422 and "still referenced" in r.text


def test_automation_kind_and_journal_v2(c):
    seed(c)
    assert post(c, {"automations": [{"id": "a", "name": "A", "kind": "daily"}]}).status_code == 200
    assert post(c, {"automations": [{"id": "b", "name": "B", "kind": "hourly"}]}).status_code == 422
    assert post(c, {"journal": [{"message": "m", "kind": "digest", "title": "Morning", "area": "life", "thread": "bike"},
                                {"message": "x", "kind": "alert"}]}).status_code == 200
    j = c.get("/api/state").json()["journal"]
    assert j[1]["kind"] == "digest" and j[1]["title"] == "Morning" and j[1]["thread"] == "bike"
    assert "title" not in j[0]
    assert post(c, {"journal": [{"message": "m", "thread": "bogus-format!"}]}).status_code == 422


def test_sensitive_inbox_muse_cannot_answer(c):
    seed(c)
    for body in ({"status": "confirmed"}, {"status": "declined"}, {"status": "acknowledged"}, {"sensitive": False}, {"choice": "A"}):
        r = post(c, {"inbox": [dict(id="new_payee", **body)]})
        assert r.status_code == 422, body
    assert post(c, {"inbox": [{"id": "z2", "title": "n", "sensitive": True, "status": "confirmed"}]}).status_code == 422
    assert post(c, {"inbox": [{"id": "pick", "status": "resolved"}]}).status_code == 200      # non-sensitive: fine
    assert post(c, {"inbox": [{"id": "new_payee", "detail": "more info"}]}).status_code == 200


def test_action_auth_separation(c):
    seed(c)
    body = {"type": "done", "section": "queue", "id": "q1"}
    assert c.post("/api/action", json=body).status_code == 401
    assert act(c, body, {"Authorization": "Bearer wrong"}).status_code == 401
    assert act(c, body, M).status_code == 401                                   # Muse's token cannot click
    assert c.post("/api/update", json={"heartbeat": True}, headers=Hh).status_code == 401   # human token cannot publish
    assert act(c, body).status_code == 200
    assert HUMAN not in c.get("/api/state").text and TOKEN not in c.get("/api/state").text


def test_actions_disabled_without_human_token(c, monkeypatch):
    monkeypatch.delenv("MUSE_HUMAN_TOKEN")
    assert act(c, {"type": "done", "section": "queue", "id": "q1"}).status_code == 503
    assert c.get("/api/state").json()["human_actions_enabled"] is False


def test_action_transitions_and_audit(c):
    seed(c)
    r = act(c, {"type": "done", "section": "queue", "id": "q1"}).json()
    assert r["status"] == "done" and r["changed"]
    st = c.get("/api/state").json()
    q = st["queue"][0]
    assert q["status"] == "done" and q["human"]["type"] == "done"
    assert act(c, {"type": "set_status", "section": "queue", "id": "q1", "params": {"status": "blocked"}}).json()["status"] == "blocked"
    assert act(c, {"type": "set_status", "section": "queue", "id": "q1", "params": {"status": "weird"}}).status_code == 422
    assert act(c, {"type": "reopen", "section": "queue", "id": "q1"}).json()["status"] == "todo"
    assert act(c, {"type": "done", "section": "deadlines", "id": "d1"}).json()["status"] == "done"
    assert act(c, {"type": "set_stage", "section": "threads", "id": "bike", "params": {"stage": "Test ride"}}).status_code == 200
    assert c.get("/api/state").json()["threads"][0]["stage"] == "Test ride"
    assert act(c, {"type": "set_stage", "section": "threads", "id": "bike", "params": {"stage": "Nope"}}).status_code == 422
    r = act(c, {"type": "choose", "section": "inbox", "id": "pick", "params": {"option": "B"}}).json()
    assert r["status"] == "resolved"
    assert [i for i in c.get("/api/state").json()["inbox"] if i["id"] == "pick"][0]["choice"] == "B"
    assert act(c, {"type": "choose", "section": "inbox", "id": "pick", "params": {"option": "Z"}}).status_code == 422
    assert act(c, {"type": "done", "section": "queue", "id": "missing"}).status_code == 404
    assert act(c, {"type": "confirm", "section": "queue", "id": "q1"}).status_code == 422        # not allowed on queue
    assert act(c, {"type": "delete", "section": "queue", "id": "q1"}).status_code == 422
    assert act(c, {"type": "done", "section": "projects", "id": "x"}).status_code == 422
    conn = sqlite3.connect(os.environ["MUSE_DB"])
    assert conn.execute("select count(*) from actions").fetchone()[0] == 6
    for sql in ("UPDATE actions SET type='x'", "DELETE FROM actions"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)


def test_sensitive_requires_explicit_ack(c):
    seed(c)
    r = act(c, {"type": "confirm", "section": "inbox", "id": "new_payee"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "confirmation_required"
    assert [i for i in c.get("/api/state").json()["inbox"] if i["id"] == "new_payee"][0]["status"] == "open"
    r = act(c, {"type": "decline", "section": "inbox", "id": "new_payee", "sensitive_ack": True})
    assert r.status_code == 200 and r.json()["status"] == "declined"


def test_actions_seen_and_unseen(c):
    seed(c)
    act(c, {"type": "done", "section": "queue", "id": "q1"}); act(c, {"type": "reopen", "section": "queue", "id": "q1"})
    s = c.get("/api/state").json()
    assert s["unseen_actions"] == 2 and not s["actions"][0]["seen"]
    assert post(c, {"actions_seen": 999}).json()["actions_seen"] == 2          # clamped to existing max
    s = c.get("/api/state").json()
    assert s["unseen_actions"] == 0 and s["actions"][0]["seen"]
    assert post(c, {"actions_seen": 1}).json()["actions_seen"] == 2            # monotonic


def test_protected_ui_served_and_not_overridable(c):
    r = c.get("/_ui/actions.js")
    assert r.status_code == 200 and "isTrusted" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert c.get("/static/actions.js").status_code == 404


def test_idempotent_migration_style_upserts(c):
    seed(c)
    body = {"queue": [{"id": "q1", "area": "purchase", "thread": "bike"}]}
    assert post(c, body).json().get("updated") == ["queue:q1"]
    assert post(c, body).json().get("unchanged") == ["queue:q1"]


def test_static_assets_revalidate(c):
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert c.get("/_ui/actions.js").headers["cache-control"] == "no-cache"
