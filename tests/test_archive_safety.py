import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
HUMAN = "human-token-0123456789abcdef"
M = {"Authorization": "Bearer " + TOKEN}
H = {"Authorization": "Bearer " + HUMAN}


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", HUMAN)
    from app.main import app
    with TestClient(app) as cl:
        yield cl


def pub(c, body):
    assert c.post("/api/update", json=body, headers=M).status_code == 200


def act(c, section, id, type, params=None):
    body = {"type": type, "section": section, "id": id}
    if params is not None: body["params"] = params
    return c.post("/api/action", json=body, headers=H)


def st(c, sec):
    return {x["id"]: x for x in c.get("/api/state").json()[sec]}


def seed(c):
    far = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pub(c, {"threads": [{"id": "paper", "title": "Paper", "area": "research"}, {"id": "empty", "title": "Empty", "area": "life"}],
            "queue": [{"id": "t1", "title": "Do it", "status": "active", "thread": "paper"}, {"id": "t2", "title": "Wait", "status": "waiting", "thread": "paper"},
                      {"id": "t3", "title": "Finished", "status": "done", "thread": "paper"}],
            "deadlines": [{"id": "d1", "title": "Formal", "due": "2999-01-01", "thread": "paper"}],
            "schedule": [{"id": "s1", "title": "Booked", "start": far, "thread": "paper"}],
            "automations": [{"id": "r1", "name": "Reminder", "kind": "once", "next_run": far, "thread": "paper"}, {"id": "a1", "name": "Routine", "kind": "daily", "thread": "paper"}]})


def test_archive_with_open_tasks_is_refused_by_the_backend(c):
    seed(c)
    r = act(c, "threads", "paper", "done")
    assert r.status_code == 409 and r.json()["error"]["code"] == "open_work"
    assert r.json()["error"]["details"][0]["impact"] == {"tasks": 2, "waiting": 1, "deadlines": 1, "events": 1, "reminders": 1, "routines": 1}
    assert st(c, "threads")["paper"].get("status", "active") != "done"                 # nothing changed
    for bad in ({"acknowledge_open": "yes"}, {"acknowledge_open": 1}, {"acknowledge_open": False}, {}):
        assert act(c, "threads", "paper", "done", bad).status_code == 409, bad          # only a real boolean true counts
    assert [a for a in c.get("/api/state").json()["actions"] if a["target"] == "paper"] == []


def test_acknowledged_archive_keeps_everything_attached(c):
    seed(c)
    keep = lambda: {s: {k: {f: v for f, v in x.items() if f in ("status", "due", "start", "next_run", "kind")} for k, x in st(c, s).items() if x.get("thread") == "paper"}
                    for s in ("queue", "deadlines", "schedule", "automations")}
    before = keep()
    r = act(c, "threads", "paper", "done", {"acknowledge_open": True})
    assert r.status_code == 200 and st(c, "threads")["paper"]["status"] == "done"
    assert keep() == before                                                              # tasks, deadline, event, reminder, routine untouched
    a = [x for x in c.get("/api/state").json()["actions"] if x["target"] == "paper"][0]
    assert a["params"]["impact"]["tasks"] == 2 and a["params"]["acknowledged_open_tasks"] is True


def test_archive_without_open_tasks_needs_no_acknowledgment(c):
    seed(c)
    assert act(c, "threads", "empty", "done").status_code == 200
    pub(c, {"queue": [{"id": "t1", "status": "done"}, {"id": "t2", "status": "dropped"}]})
    assert act(c, "threads", "paper", "done").status_code == 200                         # only reminders/deadlines/events left: allowed, recorded


def test_task_done_never_archives_thread(c):
    seed(c)
    act(c, "queue", "t1", "done"); act(c, "queue", "t2", "done")
    assert st(c, "threads")["paper"].get("status", "active") == "active"


def test_double_archive_and_reopen(c):
    seed(c)
    assert act(c, "threads", "paper", "done", {"acknowledge_open": True}).status_code == 200
    assert act(c, "threads", "paper", "done", {"acknowledge_open": True}).status_code == 422
    assert act(c, "threads", "paper", "reopen").json()["status"] == "active"
    assert st(c, "queue")["t2"]["status"] == "waiting"                                   # still tracked after the round trip


def test_other_actions_and_auth_unchanged(c):
    seed(c)
    assert act(c, "queue", "t1", "done").status_code == 200
    assert act(c, "threads", "paper", "set_stage", {"stage": "x"}).status_code == 422
    assert c.post("/api/action", json={"type": "done", "section": "threads", "id": "empty"}).status_code == 401
    assert c.post("/api/action", json={"type": "done", "section": "threads", "id": "empty"}, headers=M).status_code == 401
