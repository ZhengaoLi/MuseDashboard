from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
HUMAN = "human-token-0123456789abcdef"
M = {"Authorization": "Bearer " + TOKEN}
TODAY = date(2026, 10, 18)


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", HUMAN)
    from app import db
    monkeypatch.setattr(db, "local_today", lambda: TODAY)
    from app.main import app
    with TestClient(app) as cl:
        cl.db = db
        yield cl


def pub(c, body):
    return c.post("/api/update", json=body, headers=M)


def q(c):
    return {x["id"]: x for x in c.get("/api/state").json()["queue"]}


def iso(d):
    return d.isoformat()


def test_legacy_tasks_unaffected(c):
    assert pub(c, {"queue": [{"id": "old", "title": "Old task", "status": "todo", "priority": "p1", "order": 2}]}).status_code == 200
    t = q(c)["old"]
    assert "planned_for" not in t and "time_hint" not in t and "plan_state" not in t
    assert c.get("/api/state").json()["today"] == "2026-10-18"


def test_field_validation(c):
    ok = lambda body: pub(c, {"queue": [dict(id="a", title="A", **body)]}).status_code
    assert ok({"planned_for": "2026-10-18", "time_hint": "tonight"}) == 200
    for bad in ("2026-13-01", "2026-02-30", "10/18/2026", "2026-10-18T20:00", "tonight", "", "2026-1-5"):
        assert ok({"planned_for": bad}) == 422, bad
    assert ok({"planned_for": "2026-10-18", "time_hint": "midnight"}) == 422
    assert ok({"planned_for": "2026-10-18", "time_hint": "20:00"}) == 422          # never a clock time
    assert pub(c, {"queue": [{"id": "n", "title": "N", "time_hint": "morning"}]}).status_code == 422   # hint needs a day
    assert pub(c, {"deadlines": [{"id": "d", "title": "D", "due": "2026-10-20", "planned_for": "2026-10-18"}]}).status_code == 422  # tasks only
    assert pub(c, {"schedule": [{"id": "s", "start": "2026-10-20", "time_hint": "evening"}]}).status_code == 422


def test_partial_update_and_clearing(c):
    pub(c, {"queue": [{"id": "a", "title": "A", "status": "todo", "planned_for": "2026-10-18", "time_hint": "tonight"}]})
    pub(c, {"queue": [{"id": "a", "note": "n"}]})
    assert q(c)["a"]["planned_for"] == "2026-10-18" and q(c)["a"]["time_hint"] == "tonight"      # preserved
    assert pub(c, {"queue": [{"id": "a", "time_hint": None}]}).status_code == 200
    assert "time_hint" not in q(c)["a"] and q(c)["a"]["planned_for"] == "2026-10-18"
    assert pub(c, {"queue": [{"id": "a", "time_hint": "morning", "planned_for": None}]}).status_code == 422   # would orphan the hint
    assert pub(c, {"queue": [{"id": "a", "planned_for": None}]}).status_code == 200
    assert "planned_for" not in q(c)["a"] and "plan_state" not in q(c)["a"]


def test_plan_state_today_tomorrow_future_overdue(c):
    t = TODAY
    pub(c, {"queue": [
        {"id": "today", "title": "T", "status": "todo", "planned_for": iso(t), "time_hint": "tonight"},
        {"id": "tmrw", "title": "T2", "status": "todo", "planned_for": iso(t + timedelta(days=1))},
        {"id": "later", "title": "T3", "status": "active", "planned_for": iso(t + timedelta(days=9))},
        {"id": "past", "title": "T4", "status": "todo", "planned_for": iso(t - timedelta(days=1))},
        {"id": "none", "title": "T5", "status": "todo"}]})
    s = q(c)
    assert [s[k].get("plan_state") for k in ("today", "tmrw", "later", "past", "none")] == ["today", "tomorrow", "future", "overdue", None]


def test_completed_and_dropped_tasks_have_no_plan_state(c):
    pub(c, {"queue": [{"id": "d1", "title": "D", "status": "done", "planned_for": iso(TODAY)},
                      {"id": "d2", "title": "D2", "status": "dropped", "planned_for": iso(TODAY)}]})
    assert "plan_state" not in q(c)["d1"] and "plan_state" not in q(c)["d2"]
    pub(c, {"queue": [{"id": "d1", "status": "todo"}]})               # reopened -> counts again
    assert q(c)["d1"]["plan_state"] == "today"


def test_status_and_priority_are_independent_of_plan(c):
    pub(c, {"queue": [{"id": "lowprio", "title": "L", "status": "waiting", "priority": "p3", "planned_for": iso(TODAY)},
                      {"id": "hiprio", "title": "H", "status": "todo", "priority": "p0"}]})
    s = q(c)
    assert s["lowprio"]["plan_state"] == "today" and "plan_state" not in s["hiprio"]     # priority never makes something 'today'
    assert s["lowprio"]["status"] == "waiting"                                           # planning never changes status


def test_cross_midnight_today_becomes_overdue(c):
    pub(c, {"queue": [{"id": "a", "title": "A", "status": "todo", "planned_for": iso(TODAY), "time_hint": "tonight"}]})
    assert q(c)["a"]["plan_state"] == "today"
    c.db.local_today = lambda: TODAY + timedelta(days=1)                      # the clock passes midnight
    s = c.get("/api/state").json()
    assert s["today"] == "2026-10-19" and q(c)["a"]["plan_state"] == "overdue"
    c.db.local_today = lambda: TODAY - timedelta(days=1)
    assert q(c)["a"]["plan_state"] == "tomorrow"


def test_formal_deadlines_unchanged_by_plans(c):
    pub(c, {"deadlines": [{"id": "d", "title": "Formal", "due": "2026-10-19"}],
            "queue": [{"id": "a", "title": "A", "planned_for": iso(TODAY)}]})
    d = c.get("/api/state").json()["deadlines"][0]
    assert d["due"] == "2026-10-19" and "plan_state" not in d and "planned_for" not in d


def test_merged_and_thread_links_still_work(c):
    pub(c, {"threads": [{"id": "t", "title": "T", "area": "research"}]})
    assert pub(c, {"queue": [{"id": "a", "title": "A", "thread": "t", "planned_for": iso(TODAY), "time_hint": "evening"},
                             {"id": "b", "title": "B", "thread": "t", "merged_into": "a"}]}).status_code == 200
    s = q(c)
    assert s["a"]["plan_state"] == "today" and s["b"]["merged_into"] == "a"


def test_overdue_plan_is_never_rolled_forward(c):
    pub(c, {"queue": [{"id": "a", "title": "A", "status": "todo", "planned_for": iso(TODAY), "time_hint": "tonight"}]})
    c.db.local_today = lambda: TODAY + timedelta(days=3)
    s = q(c)["a"]
    assert s["plan_state"] == "overdue" and s["planned_for"] == "2026-10-18" and s["time_hint"] == "tonight"   # data untouched
    assert pub(c, {"heartbeat": True}).status_code == 200
    assert q(c)["a"]["planned_for"] == "2026-10-18"                                                            # no side effects either


def test_focus_and_planned_task_coexist(c):
    pub(c, {"queue": [{"id": "a", "title": "Same matter", "status": "todo", "planned_for": iso(TODAY)}],
            "focus": {"title": "Same matter", "task": "a"}})
    st = c.get("/api/state").json()
    assert st["focus"]["task"] == "a" and [x["plan_state"] for x in st["queue"]] == ["today"]
    assert len(st["queue"]) == 1                                                                                # focus is not a queue row
