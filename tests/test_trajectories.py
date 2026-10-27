"""Trajectory evidence: Context -> Proposal -> Human decision -> Execution -> Outcome (preservation only, no learning)."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
HUMAN = "human-token-0123456789abcdef"
M = {"Authorization": "Bearer " + TOKEN}
Hh = {"Authorization": "Bearer " + HUMAN}


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", HUMAN)
    from app.main import app
    with TestClient(app) as cl:
        cl.dbp = str(tmp_path / "t.db")
        yield cl


def pub(c, body):
    r = c.post("/api/update", json=body, headers=M)
    assert r.status_code == 200, r.text
    return r.json()


def act(c, body):
    return c.post("/api/action", json=body, headers=Hh)


def db(c):
    d = sqlite3.connect(c.dbp); d.row_factory = sqlite3.Row
    return d


def events(c, **kw):
    q, a = "SELECT * FROM events WHERE 1=1", []
    for k, v in kw.items():
        q += " AND %s=?" % k; a.append(v)
    return [dict(r, data=json.loads(r["data"])) for r in db(c).execute(q + " ORDER BY id", a)]


def traj(c):
    from app.trajectory import export
    return {t["trajectory_id"]: t for t in export(c.dbp)}


def seed(c):
    pub(c, {"threads": [{"id": "bike", "title": "Bike", "area": "purchase", "status": "active"}],
            "queue": [{"id": "q1", "title": "Call shop", "thread": "bike", "status": "todo"}],
            "inbox": [{"id": "pay", "title": "Pay deposit?", "detail": "Shop asks $50", "kind": "confirm", "sensitive": True, "status": "open", "thread": "bike", "basis": "planned"},
                      {"id": "pick", "title": "Which day?", "kind": "decision", "options": ["Mon", "Tue"], "status": "open", "thread": "bike"}]})


def test_1_proposal_accept_execute(c):
    seed(c)
    r = act(c, {"type": "confirm", "section": "inbox", "id": "pay", "sensitive_ack": True, "request_id": "req-accept-0001"}).json()
    aid = r["action"]
    pub(c, {"action_results": [{"id": aid, "status": "completed", "note": "deposit sent"}]})
    t = traj(c)["act:%d" % aid]
    assert t["human"]["decision"] == "accept" and t["human"]["sensitive"] is True
    assert t["proposal"]["record_at_decision"]["detail"]["text"] == "Shop asks $50"                # what was proposed, as it was then
    assert [h["kind"] for h in t["proposal"]["history"]] == ["record.created"]                   # when it was proposed
    assert t["context"]["thread"]["id"] == "bike" and t["context"]["basis"] == "planned"
    assert t["execution"]["primary_result"]["status"] == "completed"
    assert t["flags"]["silence_is_consent"] is False


def test_2_proposal_declined_has_no_execution(c):
    seed(c)
    aid = act(c, {"type": "decline", "section": "inbox", "id": "pay", "sensitive_ack": True}).json()["action"]
    t = traj(c)["act:%d" % aid]
    assert t["human"]["decision"] == "reject" and t["execution"]["primary_result"] is None
    assert "prop:inbox:pay" not in traj(c)                                                        # answered, so not counted as unanswered


def test_silence_is_recorded_as_no_response_not_rejection(c):
    seed(c)
    t = traj(c)["prop:inbox:pick"]
    assert t["human"] is None and t["human_response"] == "none_observed" and t["flags"]["silence_is_consent"] is False


def test_3_modified_then_executed(c):
    seed(c)
    pub(c, {"inbox": [{"id": "pick", "options": ["Mon", "Tue", "Wed"]}]})                          # Muse revises its own proposal first
    aid = act(c, {"type": "choose", "section": "inbox", "id": "pick", "params": {"option": "Wed"}}).json()["action"]
    t = traj(c)["act:%d" % aid]
    assert t["human"]["decision"] == "choose" and t["human"]["params"] == {"option": "Wed"}
    assert t["proposal"]["record_at_decision"]["options"] == ["Mon", "Tue", "Wed"]
    upd = [h for h in t["proposal"]["history"] if h["kind"] == "record.updated"]
    assert upd and upd[0]["data"]["changed"]["options"]["from"] == ["Mon", "Tue"]                  # before/after of the revision is kept
    # user-modified execution through a declared ui_action: the selection is the modification
    pub(c, {"threads": [{"id": "bike", "ui_actions": [{"id": "book", "label": "Book", "type": "intent", "intent": "schedule_ride",
            "steps": [{"id": "slot", "prompt": "When?", "choices": [{"value": "am", "label": "AM"}, {"value": "pm", "label": "PM"}]}]}]}]})
    a2 = act(c, {"type": "ui_action", "section": "threads", "id": "bike", "params": {"ui_action": "book", "selection": {"slot": "pm"}}}).json()["action"]
    t2 = traj(c)["act:%d" % a2]
    assert t2["human"]["effect"] == "request_to_muse" and "selection" in json.dumps(t2["human"]["params"])


def test_4_request_then_execution_failed(c):
    seed(c)
    pub(c, {"threads": [{"id": "bike", "ui_actions": [{"id": "nudge", "label": "Nudge", "type": "intent", "intent": "remind_followup"}]}]})
    aid = act(c, {"type": "ui_action", "section": "threads", "id": "bike", "params": {"ui_action": "nudge", "selection": {}}}).json()["action"]
    t = traj(c)["act:%d" % aid]
    assert t["execution"]["primary_result"] is None and "request, not proof" in t["execution"]["note"]     # click != success
    pub(c, {"action_results": [{"id": aid, "status": "unable", "note": "calendar API refused"}]})
    t = traj(c)["act:%d" % aid]
    assert t["execution"]["primary_result"]["status"] == "unable" and "refused" in t["execution"]["primary_result"]["note"]


def test_5_reversal_and_correction(c):
    seed(c)
    a1 = act(c, {"type": "done", "section": "queue", "id": "q1"}).json()["action"]
    pub(c, {"action_results": [{"id": a1, "status": "completed"}]})
    a2 = act(c, {"type": "reopen", "section": "queue", "id": "q1"}).json()["action"]
    pub(c, {"events": [{"event_id": "fix-1", "kind": "correction", "ref_action": a1, "basis": "user_stated", "source": "chat:2026-10-20", "data": {"what": "call was not made"}},
                       {"event_id": "pred-1", "kind": "prediction", "ref_action": a1, "basis": "agent_inference", "data": {"expected": "shop confirms"}},
                       {"event_id": "out-1", "kind": "outcome", "ref_action": a1, "basis": "external_evidence", "source": "gmail:msg-123", "data": {"actual": "no reply"}}]})
    pub(c, {"action_results": [{"id": a1, "status": "unable", "note": "later found not done"}]})      # a second, later report is kept, not dropped
    t = traj(c)["act:%d" % a1]
    assert [x["type"] for x in t["outcome"]["later_human_actions"]] == ["reopen"]
    kinds = {e["kind"]: e["data"] for e in t["outcome"]["muse_events"]}
    assert set(kinds) == {"correction", "prediction", "outcome"}
    assert kinds["correction"]["basis"] == "user_stated" and kinds["outcome"]["basis"] == "external_evidence"     # claim vs evidence stay apart
    assert t["execution"]["primary_result"]["status"] == "completed"                               # first report preserved as primary
    assert len(t["execution"]["events"]) == 2                                                      # and the later one is still there
    assert traj(c)["act:%d" % a2]["human"]["reverses_action"] == a1


def test_6_retries_do_not_duplicate_logical_events(c):
    seed(c)
    body = {"type": "confirm", "section": "inbox", "id": "pay", "sensitive_ack": True, "request_id": "req-dup-000001"}
    a = act(c, body).json(); b = act(c, body).json(); d = act(c, dict(body, request_id="req-dup-000002")).json()    # retry + stale button
    assert a["action"] == b["action"] and b["duplicate"] and d["noop"]
    assert len(events(c, kind="human.action")) == 1
    for _ in range(3):
        pub(c, {"action_results": [{"id": a["action"], "status": "completed", "note": "ok"}]})
    assert len(events(c, kind="muse.result")) == 1
    for _ in range(3):
        pub(c, {"events": [{"event_id": "same-1", "kind": "outcome", "ref_action": a["action"], "data": {"x": 1}}]})
    assert len(events(c, kind="outcome")) == 1
    body2 = {"queue": [{"id": "q9", "title": "New"}], "idempotency_key": "idem-key-0001"}
    pub(c, body2); pub(c, body2); pub(c, {"queue": [{"id": "q9", "title": "New"}]})                 # replay and no-op update
    assert len(events(c, kind="record.created", target="q9")) == 1 and not events(c, kind="record.updated", target="q9")


def test_7_secrets_never_reach_the_log(c):
    seed(c)
    secrets = ["hunter2-pass", "sk-abcdefghijklmnopqrstuvwx", "Bearer abcdefghijklmnop12345", "123456", "4111 1111 1111 1111", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
               "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk", "078-05-1120", "a" * 40 + "b" * 8]
    pub(c, {"inbox": [{"id": "leak", "title": "Login", "detail": "password: hunter2-pass and key sk-abcdefghijklmnopqrstuvwx, auth Bearer abcdefghijklmnop12345; your verification code is 123456; card 4111 1111 1111 1111; "
                        "gh ghp_abcdefghijklmnopqrstuvwxyz0123456789 jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk ssn 078-05-1120 blob " + "a" * 40 + "b" * 8}]})
    aid = act(c, {"type": "ack", "section": "inbox", "id": "leak"}).json()["action"]
    pub(c, {"action_results": [{"id": aid, "status": "completed", "note": "used password: hunter2-pass"}],
            "events": [{"event_id": "leak-1", "kind": "evidence", "ref_action": aid, "source": "gmail:abc", "data": {"api_key": "zzz-real-key", "session": "cookie123", "body": "x" * 3000}}]})
    dump = json.dumps(events(c)) + json.dumps(list(traj(c).values()))
    for s in secrets + ["zzz-real-key", "cookie123"]:
        assert s not in dump, s
    ev = events(c, event_id="m:leak-1")[0]["data"]["data"]
    assert ev["api_key"] == "[redacted]" and len(ev["body"]) < 450                                  # no wholesale copies of long text
    assert "gmail:abc" in dump                                                                       # references are kept


def test_append_only_and_export_is_read_only(c, tmp_path, capsys):
    seed(c)
    d = db(c)
    with pytest.raises(sqlite3.DatabaseError):
        d.execute("UPDATE events SET kind='x'")
    with pytest.raises(sqlite3.DatabaseError):
        d.execute("DELETE FROM events")
    from app import trajectory
    out = tmp_path / "t.jsonl"
    trajectory.main(["export", "--db", c.dbp, "--out", str(out)])
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines and all(x["schema"] == "muse-trajectory/1" for x in lines)
    with pytest.raises(sqlite3.OperationalError):
        sqlite3.connect("file:%s?mode=ro" % c.dbp, uri=True).execute("INSERT INTO meta(key,value) VALUES('x','y')")
    before = db(c).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    list(trajectory.export(c.dbp)); assert db(c).execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_events_do_not_widen_what_muse_or_the_page_can_do(c):
    seed(c)
    assert "events" not in c.get("/api/state").json()                                               # not exposed to readers
    r = c.post("/api/update", json={"events": [{"event_id": "e1", "kind": "outcome", "ref_action": 99999}]}, headers=M)
    assert r.status_code == 422                                                                      # cannot attach to an action that does not exist
    assert c.post("/api/update", json={"events": [{"event_id": "e1", "kind": "approval"}]}, headers=M).status_code == 422     # no grant/authorize kinds


def test_only_whitelisted_structured_fields_are_stored(c):
    body = "Dear customer, your account statement and medical results follow. " * 28                  # ~1.8k chars of mail-like text (API max is 2000)
    pub(c, {"threads": [{"id": "t1", "title": "Mail thread", "area": "admin", "summary": body, "status": "active"}],
            "schedule": [{"id": "s1", "title": "Visit", "start": "2999-01-01T10:00:00-05:00", "location": "12 Private St", "note": body, "status": "confirmed"}],
            "inbox": [{"id": "i1", "title": "Reply?", "detail": body, "kind": "decision", "options": ["yes", "no"], "status": "open", "thread": "t1"}]})
    dump = json.dumps(events(c))
    assert "12 Private St" not in dump                                                               # not whitelisted: dropped, only the name is noted
    sch = events(c, kind="record.created", target="s1")[0]["data"]["record"]
    assert sch["_omitted_fields"] == ["location"] and set(sch["note"]) == {"text", "len", "sha1"} and sch["note"]["len"] == len(body.strip())
    assert len(sch["note"]["text"]) <= 120 and body[:200] not in dump                                # a summary, never the whole text
    assert len(dump) < 3500                                                                          # three ~1.8k-char texts were not copied
    pub(c, {"inbox": [{"id": "i1", "detail": body.strip() + " changed"}]})                                   # the edit is still detectable, structurally
    ch = events(c, kind="record.updated", target="i1")[0]["data"]["changed"]["detail"]
    assert ch["from"]["sha1"] != ch["to"]["sha1"] and ch["to"]["len"] == len(body.strip()) + 8
    aid = act(c, {"type": "ack", "section": "inbox", "id": "i1"}).json()["action"]
    rec = traj(c)["act:%d" % aid]["proposal"]["record_at_decision"]
    assert set(rec["detail"]) == {"text", "len", "sha1"}


def test_one_logical_operation_shares_a_correlation_id(c):
    pub(c, {"threads": [{"id": "t1", "title": "T", "area": "admin"}], "queue": [{"id": "qa", "title": "A", "thread": "t1"}, {"id": "qb", "title": "B", "thread": "t1"}]})
    corr = {e["corr"] for e in events(c, kind="record.created")}
    assert len(corr) == 1 and corr.pop().startswith("upd:")                                          # three records, one update
    pub(c, {"queue": [{"id": "qa", "title": "A2"}]})
    assert len({e["corr"] for e in events(c, kind="record.updated")}) == 1 and len({e["corr"] for e in events(c)}) == 2
    aid = act(c, {"type": "done", "section": "queue", "id": "qa"}).json()["action"]
    pub(c, {"action_results": [{"id": aid, "status": "completed"}, ],
            "events": [{"event_id": "o1", "kind": "outcome", "ref_action": aid, "basis": "external_evidence", "data": {"ok": True}}]})
    pub(c, {"action_results": [{"id": aid, "status": "unable", "note": "later"}]})
    chain = [e for e in events(c) if e["corr"] == "act:%d" % aid]
    assert sorted(e["kind"] for e in chain) == ["human.action", "muse.result", "muse.result", "outcome"]      # decision + everything that followed it
    n_decisions = len([t for k, t in traj(c).items() if k.startswith("act:")])
    assert n_decisions == db(c).execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1               # four events, still ONE decision


def test_periodic_push_and_noop_upserts_create_no_events(c):
    payload = {"focus": {"title": "F", "reason": "r"}, "threads": [{"id": "t1", "title": "T", "area": "admin"}], "queue": [{"id": "qa", "title": "A", "thread": "t1"}],
               "automations": [{"id": "au", "name": "Daily push", "frequency": "every morning", "kind": "daily", "status": "ok"}], "heartbeat": True}
    pub(c, payload)
    n = len(events(c))
    for _ in range(25):                                                                              # a cron-style dashboard push that changes nothing
        pub(c, payload)
        pub(c, {"heartbeat": True, "journal": []})
    assert len(events(c)) == n
    for _ in range(3):
        pub(c, dict(payload, idempotency_key="same-key-0001"))
    assert len(events(c)) == n
    pub(c, {"queue": [{"id": "qa", "title": "A", "status": "done"}]})
    assert len(events(c)) == n + 1


def test_test_data_is_excluded_from_exports_by_default(c):
    pub(c, {"inbox": [{"id": "zz-accept-1", "title": "test", "kind": "decision", "options": ["a", "b"], "status": "open"}]})
    aid = act(c, {"type": "choose", "section": "inbox", "id": "zz-accept-1", "params": {"option": "a"}}).json()["action"]
    from app.trajectory import export
    assert "act:%d" % aid not in {t["trajectory_id"] for t in export(c.dbp)}
    assert "act:%d" % aid in {t["trajectory_id"] for t in export(c.dbp, include_test=True)}


def test_default_export_excludes_every_known_kind_of_test_data_but_never_real_records(c):
    seed(c)                                                                              # real: bike thread, pay/pick inbox, q1
    pub(c, {"threads": [{"id": "test-ride", "title": "Test ride booking", "area": "purchase"}, {"id": "ux-check-77", "title": "UX acceptance check", "area": "life"},
                        {"id": "hook-e2e-thing", "title": "E2E consume click", "area": "system"}, {"id": "real-one", "title": "E2E-looking? no: ordinary", "area": "life"}],
            "inbox": [{"id": "zz-accept-9", "title": "t", "kind": "decision", "options": ["a", "b"], "status": "open"},
                      {"id": "child", "title": "ask about test thread", "kind": "decision", "options": ["a", "b"], "status": "open", "thread": "ux-check-77"},
                      {"id": "titled", "title": "ACCEPTANCE TEST marker", "kind": "decision", "options": ["a", "b"], "status": "open"},
                      {"id": "real-ask", "title": "Ordinary question", "kind": "decision", "options": ["a", "b"], "status": "open", "thread": "test-ride"}]})
    ids = {}
    for sec, rid, typ, extra in (("inbox", "zz-accept-9", "choose", {"option": "a"}), ("inbox", "child", "choose", {"option": "a"}), ("inbox", "titled", "choose", {"option": "a"}),
                                 ("threads", "ux-check-77", "set_status", None), ("inbox", "real-ask", "choose", {"option": "a"}), ("inbox", "pick", "choose", {"option": "Mon"})):
        if typ == "set_status": continue
        r = act(c, {"type": typ, "section": sec, "id": rid, "params": extra}); assert r.status_code == 200, (rid, r.text)
        ids[rid] = r.json()["action"]
    ids["bike"] = act(c, {"type": "set_stage", "section": "threads", "id": "bike", "params": {"stage": "x"}}).status_code
    from app.trajectory import export
    shown = {t["trajectory_id"] for t in export(c.dbp)}
    for rid in ("zz-accept-9", "child", "titled"):
        assert "act:%d" % ids[rid] not in shown, rid                                      # id prefix, test parent thread, test title: all excluded
    assert "act:%d" % ids["real-ask"] in shown and "act:%d" % ids["pick"] in shown        # a real ask on a thread called "test-ride" is real
    everything = {t["trajectory_id"] for t in export(c.dbp, include_test=True)}
    assert {"act:%d" % ids[r] for r in ("zz-accept-9", "child", "titled")} <= everything     # still available for audits
    assert len(events(c, kind="human.action")) >= 5                                         # raw events untouched


def test_unanswered_proposal_without_a_status_field_is_still_reported_as_no_response(c):
    pub(c, {"inbox": [{"id": "nostatus", "title": "Ask without status", "kind": "todo"}]})
    t = traj(c)["prop:inbox:nostatus"]
    assert t["human_response"] == "none_observed" and t["flags"]["silence_is_consent"] is False
