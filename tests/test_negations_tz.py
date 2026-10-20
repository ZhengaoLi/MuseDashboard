from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
HUMAN = "human-token-0123456789abcdef"
M = {"Authorization": "Bearer " + TOKEN}
H = {"Authorization": "Bearer " + HUMAN}
NY = ZoneInfo("America/New_York")


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", HUMAN)
    from app import db, schema
    monkeypatch.setattr(schema, "LOCAL", NY)
    monkeypatch.setattr(db, "LOCAL", NY)
    from app.main import app
    with TestClient(app) as cl:
        yield cl


def pub(c, body):
    r = c.post("/api/update", json=body, headers=M)
    assert r.status_code == 200, r.text


def act(c, section, id, type, params=None, **kw):
    body = {"type": type, "section": section, "id": id, **kw}
    if params is not None: body["params"] = params
    return c.post("/api/action", json=body, headers=H)


def rec(c, section, id):
    return {x["id"]: x for x in c.get("/api/state").json()[section]}[id]


def future(days=3):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- time zones ----------------
def test_state_reports_the_display_timezone(c):
    assert c.get("/api/state").json()["tz"] == "America/New_York"


@pytest.mark.parametrize("raw,expected_utc", [
    ("2026-10-19T08:30", "2026-10-19T12:30:00+00:00"),        # EDT (-04:00)
    ("2026-11-04T10:00", "2026-11-04T15:00:00+00:00"),        # EST (-05:00) after the fall-back
    ("2026-11-01T09:00", "2026-11-01T14:00:00+00:00"),        # the day DST ends: 09:00 is already EST
    ("2027-03-14T10:00", "2027-03-14T14:00:00+00:00"),        # the day DST starts: 10:00 is already EDT
    ("2026-10-19T08:30:00-04:00", "2026-10-19T12:30:00+00:00"),
    ("2026-10-22T07:59:00-04:00", "2026-10-22T11:59:00+00:00"),   # an 'AoE' deadline: 23:59 on the 21st in UTC-12 = 07:59 EDT on the 22nd
])
def test_naive_and_offset_times_resolve_correctly_across_dst(raw, expected_utc):
    from app import schema
    schema.LOCAL = NY
    got = datetime.fromtimestamp(schema.to_ts(raw), timezone.utc).isoformat()
    assert got == expected_utc


def test_date_only_end_of_day_uses_local_zone_and_dst():
    from app import schema
    schema.LOCAL = NY
    assert datetime.fromtimestamp(schema.to_ts("2026-10-19", end_of_day=True), NY).strftime("%H:%M:%S") == "23:59:59"
    assert datetime.fromtimestamp(schema.to_ts("2026-11-01", end_of_day=True), NY).strftime("%Z %H:%M") == "EST 23:59"


def test_the_stored_dst_slip_is_detectable():
    """A reminder stored as 2026-11-01T09:00:00-04:00 is 08:00 EST: one hour early (data issue, not a code bug)."""
    from app import schema
    schema.LOCAL = NY
    stored = datetime.fromtimestamp(schema.to_ts("2026-11-01T09:00:00-04:00"), NY)
    assert stored.strftime("%H:%M %Z") == "08:00 EST"
    assert schema.to_ts("2026-11-01T09:00") - schema.to_ts("2026-11-01T09:00:00-04:00") == 3600


def test_api_when_ts_matches_the_real_instant(c):
    pub(c, {"schedule": [{"id": "e", "title": "E", "start": "2999-10-19T08:30"}, {"id": "e2", "title": "E2", "start": "2999-11-04T10:00:00-05:00"}]})
    for rid, utc in (("e", "2999-10-19T12:30:00+00:00"), ("e2", "2999-11-04T15:00:00+00:00")):
        assert datetime.fromtimestamp(rec(c, "schedule", rid)["when_ts"], timezone.utc).isoformat() == utc


# ---------------- negations ----------------
def seed(c):
    pub(c, {"threads": [{"id": "th", "title": "T", "area": "life"}],
            "schedule": [{"id": "ev", "title": "Workshop", "start": future(), "thread": "th", "status": "tentative", "note": "registered via email"},
                         {"id": "old", "title": "Over", "start": "2020-01-01T10:00:00-05:00"},
                         {"id": "gone", "title": "Cancelled by organiser", "start": future(), "status": "cancelled"}],
            "automations": [{"id": "rm", "name": "Remind", "kind": "once", "next_run": future(), "thread": "th", "status": "ok"},
                            {"id": "rt", "name": "Routine", "kind": "daily", "next_run": future(1)}],
            "queue": [{"id": "t1", "title": "Task", "status": "active", "thread": "th"}, {"id": "t2", "title": "Task 2", "status": "todo", "thread": "th"}],
            "deadlines": [{"id": "d1", "title": "Formal", "due": "2999-01-01", "thread": "th"}]})


def test_not_attending_is_not_done_and_not_cancelled(c):
    seed(c)
    r = act(c, "schedule", "ev", "not_attending")
    assert r.status_code == 200 and r.json()["changed"] is True
    e = rec(c, "schedule", "ev")
    assert e["attendance"] == "not_going" and e["status"] == "tentative"                   # the event itself is untouched (not 'cancelled', not 'done')
    assert e["start"] and e["note"] == "registered via email"
    a = [x for x in c.get("/api/state").json()["actions"] if x["type"] == "not_attending"][0]
    assert a["params"] == {"internal_only": True} and a["target"] == "ev"
    assert act(c, "schedule", "ev", "attend").status_code == 200 and "attendance" not in rec(c, "schedule", "ev")     # recoverable
    assert act(c, "schedule", "ev", "attend").status_code == 422                            # nothing to undo


def test_not_attending_validation(c):
    seed(c)
    assert act(c, "schedule", "old", "not_attending").status_code == 422                    # already over
    assert act(c, "schedule", "gone", "not_attending").status_code == 422                   # organiser cancelled it
    assert act(c, "schedule", "nope", "not_attending").status_code == 404
    assert act(c, "queue", "t1", "not_attending").status_code == 422                        # events only
    assert act(c, "deadlines", "d1", "not_attending").status_code == 422
    assert act(c, "schedule", "ev", "done").status_code == 422                              # events have no Done


def test_stop_reminding_is_a_request_until_muse_confirms(c):
    seed(c)
    r = act(c, "automations", "rm", "stop_reminding").json()
    assert r["ok"] and r["changed"] is False
    assert rec(c, "automations", "rm")["status"] == "ok"                                    # the mirror is NOT changed by the click
    a = [x for x in c.get("/api/state").json()["actions"] if x["type"] == "stop_reminding"][0]
    assert a["status"] == "accepted" and a["params"]["internal_only"] is True
    d = act(c, "automations", "rm", "stop_reminding").json()                                 # second click while pending: same request
    assert d["already_pending"] and d["action"] == r["action"]
    assert len([x for x in c.get("/api/state").json()["actions"] if x["type"] == "stop_reminding"]) == 1
    pub(c, {"actions_seen": r["action"]})
    assert [x for x in c.get("/api/state").json()["actions"] if x["id"] == r["action"]][0]["status"] == "seen"
    # Muse really stops its native task, then confirms: mirror + result
    pub(c, {"automations": [{"id": "rm", "status": "paused"}], "action_results": [{"id": r["action"], "status": "completed", "note": "native task stopped"}]})
    a = [x for x in c.get("/api/state").json()["actions"] if x["id"] == r["action"]][0]
    assert a["status"] == "completed" and a["note"] == "native task stopped" and rec(c, "automations", "rm")["status"] == "paused"
    assert act(c, "automations", "rm", "stop_reminding").status_code == 422                 # already stopped


def test_muse_cannot_stop_it_is_reported_not_faked(c):
    seed(c)
    aid = act(c, "automations", "rm", "stop_reminding").json()["action"]
    pub(c, {"actions_seen": aid, "action_results": [{"id": aid, "status": "unable", "note": "scheduler API refused"}]})
    a = [x for x in c.get("/api/state").json()["actions"] if x["id"] == aid][0]
    assert a["status"] == "unable" and a["note"] == "scheduler API refused"
    assert rec(c, "automations", "rm")["status"] == "ok"                                     # still active: nothing pretended
    assert act(c, "automations", "rm", "stop_reminding").json()["action"] != aid             # the user may ask again after a failure


def test_resume_and_withdraw(c):
    seed(c)
    assert act(c, "automations", "rm", "resume_reminding").status_code == 422               # nothing to resume
    s1 = act(c, "automations", "rm", "stop_reminding").json()["action"]
    w = act(c, "automations", "rm", "resume_reminding")                                       # withdraw a pending stop
    assert w.status_code == 200 and w.json()["action"] != s1 and rec(c, "automations", "rm")["status"] == "ok"
    pub(c, {"automations": [{"id": "rm", "status": "paused"}]})                              # Muse had already stopped it
    assert act(c, "automations", "rm", "resume_reminding").status_code == 200               # a resume is only a request too
    assert rec(c, "automations", "rm")["status"] == "paused"                                  # still stopped until Muse confirms the resume
    pub(c, {"automations": [{"id": "rm", "status": "ok"}]})
    assert rec(c, "automations", "rm")["status"] == "ok"
    assert act(c, "automations", "rt", "stop_reminding").status_code == 422                 # routines are not reminders
    assert act(c, "automations", "nope", "stop_reminding").status_code == 404
    assert act(c, "schedule", "ev", "stop_reminding").status_code == 422


def test_task_not_doing_and_restore(c):
    seed(c)
    assert act(c, "queue", "t1", "set_status", {"status": "dropped"}).json()["status"] == "dropped"
    assert rec(c, "queue", "t1")["title"] == "Task"
    assert act(c, "queue", "t1", "reopen").json()["status"] == "todo"


def test_negations_do_not_touch_related_records(c):
    seed(c)
    keep = lambda: {s: [{k: v for k, v in x.items() if k in ("id", "status", "due", "start", "next_run", "attendance")} for x in c.get("/api/state").json()[s] if x["id"] not in ("ev", "rm", "t1")]
                    for s in ("queue", "deadlines", "schedule", "automations", "threads")}
    before = keep()
    act(c, "schedule", "ev", "not_attending"); act(c, "automations", "rm", "stop_reminding"); act(c, "queue", "t1", "set_status", {"status": "dropped"})
    assert keep() == before                                                                 # deadline, thread, other tasks/reminders untouched
    assert rec(c, "threads", "th").get("status", "active") == "active"                      # none of them archives the thread


def test_idempotent_and_completed_items(c):
    seed(c)
    a = act(c, "schedule", "ev", "not_attending", request_id="rq-not-0001").json()
    b = act(c, "schedule", "ev", "not_attending", request_id="rq-not-0001").json()
    assert b["duplicate"] and b["action"] == a["action"]
    assert act(c, "schedule", "ev", "not_attending").json().get("noop") is True
    assert len([x for x in c.get("/api/state").json()["actions"] if x["type"] == "not_attending"]) == 1
    act(c, "queue", "t2", "done")
    assert act(c, "queue", "t2", "set_status", {"status": "dropped"}).status_code == 200    # finished task may still be marked not-doing explicitly


def test_authority_boundaries(c):
    seed(c)
    for t, s, i in (("not_attending", "schedule", "ev"), ("stop_reminding", "automations", "rm")):
        body = {"type": t, "section": s, "id": i}
        assert c.post("/api/action", json=body).status_code == 401
        assert c.post("/api/action", json=body, headers=M).status_code == 401              # Muse's token cannot click
    assert rec(c, "schedule", "ev").get("attendance") is None and rec(c, "automations", "rm")["status"] == "ok"
    assert [a for a in c.get("/api/state").json()["actions"] if a["target"] in ("ev", "rm")] == []


def test_sensitive_paths_unchanged(c):
    pub(c, {"inbox": [{"id": "z", "title": "Money", "kind": "confirm", "sensitive": True}]})
    assert act(c, "inbox", "z", "confirm").status_code == 403
    assert act(c, "inbox", "z", "confirm", sensitive_ack=True).status_code == 200
    assert act(c, "inbox", "z", "not_attending").status_code == 422


def test_old_records_without_attendance_still_work(c):
    pub(c, {"schedule": [{"id": "ev", "title": "Old style", "start": future()}]})
    e = rec(c, "schedule", "ev")
    assert "attendance" not in e and e["when_ts"] > 0
    assert act(c, "schedule", "ev", "not_attending").status_code == 200
    pub(c, {"schedule": [{"id": "ev", "location": "Room 1"}]})                               # Muse's later partial update keeps the human's decision
    assert rec(c, "schedule", "ev")["attendance"] == "not_going"


def _transition_days(year):
    """The real US DST changes for America/New_York: scan the calendar with the tz database, not with assumptions."""
    days = []
    prev = datetime(year, 1, 1, 12, tzinfo=NY).utcoffset()
    d = datetime(year, 1, 1, 12, tzinfo=NY)
    while d.year == year:
        d += timedelta(days=1)
        off = d.utcoffset()
        if off != prev:
            days.append(d.date().isoformat()); prev = off
    return days


def test_real_dst_dates_for_2026_and_2027():
    assert _transition_days(2026) == ["2026-03-08", "2026-11-01"]          # 2nd Sunday of March, 1st Sunday of November
    assert _transition_days(2027) == ["2027-03-14", "2027-11-07"]
    from datetime import date
    for d in ("2026-03-08", "2026-11-01", "2027-03-14", "2027-11-07"):
        assert date.fromisoformat(d).weekday() == 6                        # all Sundays


@pytest.mark.parametrize("naive,utc", [
    ("2026-03-08T10:00", "2026-03-08T14:00:00+00:00"),     # spring forward day: already EDT
    ("2026-03-07T10:00", "2026-03-07T15:00:00+00:00"),     # the day before: still EST
    ("2026-11-01T09:00", "2026-11-01T14:00:00+00:00"),     # fall back day: EST
    ("2026-10-31T09:00", "2026-10-31T13:00:00+00:00"),     # the day before: EDT
    ("2027-03-14T10:00", "2027-03-14T14:00:00+00:00"),
    ("2027-03-13T10:00", "2027-03-13T15:00:00+00:00"),
    ("2027-11-07T09:00", "2027-11-07T14:00:00+00:00"),
    ("2027-11-06T09:00", "2027-11-06T13:00:00+00:00"),
])
def test_naive_local_times_around_the_real_2026_2027_transitions(naive, utc):
    from app import schema
    schema.LOCAL = NY
    assert datetime.fromtimestamp(schema.to_ts(naive), timezone.utc).isoformat() == utc


def test_planned_for_is_a_date_not_a_timestamp(c, monkeypatch):
    from datetime import date
    pub(c, {"queue": [{"id": "p", "title": "P", "status": "todo", "planned_for": "2026-11-01"}, {"id": "q2", "title": "Q", "status": "todo", "planned_for": "2027-03-14"}]})
    from app import db
    for today, pid, expected in ((date(2026, 11, 1), "p", "today"), (date(2026, 10, 31), "p", "tomorrow"), (date(2026, 11, 2), "p", "overdue"),
                                 (date(2027, 3, 14), "q2", "today"), (date(2027, 3, 13), "q2", "tomorrow")):
        monkeypatch.setattr(db, "local_today", lambda t=today: t)               # DST days are 23h/25h long: the date logic must not care
        assert rec(c, "queue", pid)["plan_state"] == expected, (today, pid)
    assert rec(c, "queue", "p")["planned_for"] == "2026-11-01"                    # never rewritten into a time


def test_withdraw_races_never_pretend_the_native_task_is_unchanged(c):
    """Cancel-stop is only a request. Whatever Muse already did stays the truth; the mirror changes only on Muse's confirmation."""
    seed(c)
    s1 = act(c, "automations", "rm", "stop_reminding").json()["action"]
    pub(c, {"actions_seen": s1})                                                           # Muse is already working on the stop
    w = act(c, "automations", "rm", "resume_reminding")
    assert w.status_code == 200 and w.json()["changed"] is False
    assert rec(c, "automations", "rm")["status"] == "ok"                                   # nothing is claimed either way
    acts = {a["id"]: a for a in c.get("/api/state").json()["actions"]}
    assert acts[s1]["status"] == "seen" and acts[w.json()["action"]]["status"] == "accepted"      # both stay visible to Muse, unresolved
    # Muse had already stopped the native task: it reports the truth for both requests
    pub(c, {"automations": [{"id": "rm", "status": "paused"}], "action_results": [
        {"id": s1, "status": "completed", "note": "already stopped"}, {"id": w.json()["action"], "status": "unable", "note": "too late, it was stopped"}]})
    acts = {a["id"]: a for a in c.get("/api/state").json()["actions"]}
    assert rec(c, "automations", "rm")["status"] == "paused" and acts[w.json()["action"]]["status"] == "unable"


def test_resume_allowed_after_a_confirmed_stop_even_if_the_mirror_lags(c):
    seed(c)
    s1 = act(c, "automations", "rm", "stop_reminding").json()["action"]
    pub(c, {"action_results": [{"id": s1, "status": "completed"}]})                        # confirmed, mirror still 'ok'
    assert rec(c, "automations", "rm")["status"] == "ok"
    assert act(c, "automations", "rm", "resume_reminding").status_code == 200              # the UI offers Resume here, so the backend must accept it
