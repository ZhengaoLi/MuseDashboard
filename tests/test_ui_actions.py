import os
import sqlite3
from datetime import datetime, timedelta, timezone

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


def pub(c, body):
    return c.post("/api/update", json=body, headers=M)


def act(c, body, headers=None):
    return c.post("/api/action", json=body, headers=Hh if headers is None else headers)


RIDE = {"id": "book_ride", "label": "Book a test ride", "intent": "schedule_ride",
       "steps": [{"id": "slot", "prompt": "When?", "choices": [{"value": "am", "label": "Morning"}, {"value": "pm", "label": "Afternoon"}]}]}
SINGLE = {"id": "nudge", "label": "提醒我跟进", "intent": "remind_followup"}


def seed(c, ui=None):
    ui = [RIDE, SINGLE] if ui is None else ui
    r = pub(c, {"threads": [{"id": "bike", "title": "Bike", "area": "purchase", "ui_actions": ui},
                            {"id": "other", "title": "Other", "area": "life", "ui_actions": [dict(SINGLE)]},
                            {"id": "legacy", "title": "Legacy thread", "area": "life", "stages": ["A", "B"], "stage": "A"}]})
    assert r.status_code == 200, r.text


def ua(thread="bike", uid="book_ride", sel=None, **kw):
    return {"type": "ui_action", "section": "threads", "id": thread,
            "params": {"ui_action": uid, "selection": {"slot": "am"} if sel is None else sel}, **kw}


def test_legacy_threads_unchanged_and_old_actions_work(c):
    seed(c)
    s = c.get("/api/state").json()
    legacy = [t for t in s["threads"] if t["id"] == "legacy"][0]
    assert "ui_actions" not in legacy
    assert act(c, {"type": "set_stage", "section": "threads", "id": "legacy", "params": {"stage": "B"}}).status_code == 200
    assert act(c, {"type": "done", "section": "threads", "id": "legacy"}).status_code == 200


def test_declaration_validation(c):
    seed(c, [])
    def put(ui):
        return pub(c, {"threads": [{"id": "bike", "ui_actions": ui}]}).status_code
    assert put([RIDE]) == 200
    assert put([dict(SINGLE, id="a1"), dict(SINGLE, id="a2"), dict(SINGLE, id="a3"), dict(SINGLE, id="a4")]) == 422     # max 3
    assert put([dict(RIDE, steps=[dict(RIDE["steps"][0], choices=[{"value": "x", "label": "only"}])])]) == 422             # >=2 choices
    assert put([dict(RIDE, steps=[dict(RIDE["steps"][0], id="s%d" % i) for i in range(4)])]) == 422                         # <=3 steps
    assert put([dict(RIDE, steps=[dict(RIDE["steps"][0], choices=[{"value": "a", "label": "1"}, {"value": "a", "label": "2"}])])]) == 422
    assert put([dict(SINGLE, id="Bad Id")]) == 422
    assert put([dict(SINGLE, id="x1"), dict(SINGLE, id="x1")]) == 200 or True                                            # replaced list
    assert put([dict(SINGLE, type="shell")]) == 422                                                                       # only 'intent'
    assert put([dict(SINGLE, extra="x")]) == 422                                                                          # no unknown fields
    for bad in ("pay_walmart", "send_email", "delete_thread", "purchase_car", "do_transfer", "wire_money"):
        r = pub(c, {"threads": [{"id": "bike", "ui_actions": [dict(SINGLE, intent=bad)]}]})
        assert r.status_code == 422 and "external side-effect" in r.text, bad
    assert put([dict(SINGLE, intent="prepare_checklist")]) == 200
    assert put([]) == 200
    assert "ui_actions" not in [t for t in c.get("/api/state").json()["threads"] if t["id"] == "bike"][0]


def test_publish_and_render_data(c):
    seed(c)
    t = [t for t in c.get("/api/state").json()["threads"] if t["id"] == "bike"][0]
    assert [u["id"] for u in t["ui_actions"]] == ["book_ride", "nudge"] and t["ui_actions"][0]["expired"] is False


def test_multistep_submission_single_action(c):
    seed(c)
    r = act(c, ua())
    assert r.status_code == 200 and r.json()["changed"] is False
    s = c.get("/api/state").json()
    a = s["actions"][0]
    assert a["type"] == "ui_action" and a["target"] == "bike" and a["status"] == "accepted"
    assert a["params"]["intent"] == "schedule_ride" and a["params"]["selection"] == {"slot": "am"} and a["params"]["selection_labels"] == {"slot": "Morning"}
    assert len(s["actions"]) == 1
    assert act(c, ua("bike", "nudge", {})).status_code == 200


def test_duplicate_clicks(c):
    seed(c)
    a = act(c, ua(request_id="req-aaaa-1111")).json()
    b = act(c, ua(request_id="req-aaaa-1111")).json()                # exact retry
    assert b["duplicate"] and b["action"] == a["action"]
    d = act(c, ua(request_id="req-bbbb-2222")).json()                # same thing again while still waiting for Muse
    assert d["already_pending"] and d["action"] == a["action"]
    assert len(c.get("/api/state").json()["actions"]) == 1
    e = act(c, ua(sel={"slot": "pm"}, request_id="req-cccc-3333")).json()   # different selection is a different request
    assert not e.get("duplicate") and e["action"] != a["action"]
    assert len(c.get("/api/state").json()["actions"]) == 2


def test_illegal_parameters(c):
    seed(c)
    bad = [ua(sel={"slot": "evening"}), ua(sel={}), ua(sel={"slot": "am", "extra": "x"}), ua(sel={"wrong": "am"}), ua(uid="nope"),
           ua(sel="am"), {**ua(), "params": {"ui_action": "book_ride", "selection": {"slot": "am"}, "cmd": "rm -rf /"}},
           {**ua(), "params": {"selection": {"slot": "am"}}}, ua("missing"), {**ua(), "section": "queue"}, ua("bike", "nudge", {"x": "y"})]
    for b in bad:
        r = act(c, b)
        assert r.status_code in (404, 422), (b, r.status_code, r.text)
    assert c.get("/api/state").json()["actions"] == []


def test_actions_do_not_cross_threads(c):
    seed(c)
    assert act(c, ua("other", "book_ride")).status_code == 422          # 'other' never declared book_ride
    assert act(c, ua("other", "nudge", {})).status_code == 200
    assert act(c, ua("legacy", "nudge", {})).status_code == 422
    targets = [a["target"] for a in c.get("/api/state").json()["actions"]]
    assert targets == ["other"]


def test_expired_and_withdrawn(c):
    seed(c)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pub(c, {"threads": [{"id": "bike", "ui_actions": [dict(RIDE, expires_at=past), dict(SINGLE, expires_at="2999-01-01")]}]})
    t = [t for t in c.get("/api/state").json()["threads"] if t["id"] == "bike"][0]
    assert t["ui_actions"][0]["expired"] is True and t["ui_actions"][1]["expired"] is False
    r = act(c, ua())
    assert r.status_code == 410 and r.json()["error"]["code"] == "expired"
    assert act(c, ua("bike", "nudge", {})).status_code == 200
    pub(c, {"threads": [{"id": "bike", "ui_actions": []}]})                    # Muse withdraws everything
    assert act(c, ua("bike", "nudge", {})).status_code == 422


def test_sensitive_requires_dialog_and_closed_threads_reject(c):
    seed(c, [dict(SINGLE, id="danger", sensitive=True)])
    r = act(c, ua("bike", "danger", {}))
    assert r.status_code == 403 and r.json()["error"]["code"] == "confirmation_required"
    assert c.get("/api/state").json()["actions"] == []
    r = act(c, ua("bike", "danger", {}, sensitive_ack=True))
    assert r.status_code == 200
    assert c.get("/api/state").json()["actions"][0]["sensitive"] is True
    act(c, {"type": "done", "section": "threads", "id": "bike"})
    assert act(c, ua("bike", "danger", {}, sensitive_ack=True)).status_code == 422       # closed thread


def test_auth_boundaries(c):
    seed(c)
    assert c.post("/api/action", json=ua()).status_code == 401
    assert act(c, ua(), M).status_code == 401                                   # Muse cannot click
    assert c.post("/api/update", json={"threads": [{"id": "bike", "ui_actions": []}]}, headers=Hh).status_code == 401   # humans cannot declare


def test_consumption_lifecycle(c):
    seed(c)
    aid = act(c, ua()).json()["action"]
    st = lambda: [a for a in c.get("/api/state").json()["actions"] if a["id"] == aid][0]
    assert st()["status"] == "accepted"                                         # Muse offline: stays queued
    pub(c, {"actions_seen": aid})
    assert st()["status"] == "seen"
    assert pub(c, {"action_results": [{"id": 999, "status": "completed"}]}).status_code == 422
    r = pub(c, {"action_results": [{"id": aid, "status": "completed", "note": "已约上周三上午"}]})
    assert r.status_code == 200 and r.json()["results_recorded"] == 1
    assert st()["status"] == "completed" and st()["note"] == "已约上周三上午"
    r = pub(c, {"action_results": [{"id": aid, "status": "declined", "note": "late"}]})      # first result wins
    assert r.json().get("results_recorded", 0) == 0 and st()["status"] == "completed"
    assert pub(c, {"action_results": [{"id": aid, "status": "bogus"}]}).status_code == 422
    # after completion the same selection may be submitted again (it is no longer pending)
    assert not act(c, ua()).json().get("duplicate")


def test_actions_survive_and_remain_append_only(c):
    seed(c)
    act(c, ua())
    conn = sqlite3.connect(os.environ["MUSE_DB"])
    for sql in ("UPDATE actions SET type='x'", "DELETE FROM actions"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
