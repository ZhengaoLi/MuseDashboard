"""Secrets: never stored from /api/update, never served by /api/state (even if stored earlier)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"
M = {"Authorization": "Bearer " + TOKEN}
SECRET = "d25t4"            # a 5-char shared paper password, as in the real incident
SENTENCE_ZH = "admin \u5bc6\u7801 " + SECRET + " \u9700\u8981\u5171\u4eab"   # Chinese: "admin password <secret> needs sharing"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    monkeypatch.setenv("MUSE_HUMAN_TOKEN", "human-token-0123456789abcdef")
    from app.main import app
    with TestClient(app) as cl:
        cl.dbp = str(tmp_path / "t.db")
        yield cl


def raw_dump(c):
    d = sqlite3.connect(c.dbp)
    return " ".join(str(r) for t in ("journal", "records", "audit", "events", "actions") for r in d.execute("select * from %s" % t))


def test_secret_in_journal_and_fields_is_not_stored_and_caller_is_warned(c):
    r = c.post("/api/update", json={"journal": {"kind": "digest", "message": SENTENCE_ZH}, "inbox": [{"id": "i1", "title": "share", "detail": "password: hunter2x!", "kind": "todo"}],
                                    "queue": [{"id": "q1", "title": "t", "note": "your verification code is 123456 ok"}]}, headers=M)
    assert r.status_code == 200
    warns = r.json()["warnings"]
    assert {w["path"] for w in warns} == {"journal.message", "inbox.0.detail", "queue.0.note"}
    assert SECRET not in r.text and "hunter2x" not in r.text and "123456" not in r.text           # the response never echoes a value
    assert SECRET not in raw_dump(c) and "hunter2x" not in raw_dump(c) and "123456" not in raw_dump(c)   # nothing reached any table (incl. audit/events)
    j = c.get("/api/state").json()["journal"][0]["message"]
    assert "[redacted]" in j and "\u9700\u8981\u5171\u4eab" in j                                  # the rest of the sentence survives


def test_normal_text_is_untouched_and_no_warning(c):
    body = {"journal": {"kind": "note", "message": "password reset email sent; token expired, will retry; order 123456789012 shipped; Pset 4 due 10/27"},
            "queue": [{"id": "q1", "title": "Change your password this week", "note": "code review at 3pm"}]}
    r = c.post("/api/update", json=body, headers=M)
    assert r.status_code == 200 and "warnings" not in r.json()
    assert c.get("/api/state").json()["journal"][0]["message"] == body["journal"]["message"]


def test_legacy_secret_already_in_the_database_is_not_served(c):
    c.post("/api/update", json={"journal": "seed", "queue": [{"id": "old", "title": "x", "note": "n"}], "inbox": [{"id": "ib", "title": "y", "kind": "todo"}]}, headers=M)
    d = sqlite3.connect(c.dbp)
    d.execute("DROP TRIGGER journal_no_update"); d.execute("DROP TRIGGER journal_no_delete")           # simulate rows written by an older version
    d.execute("INSERT INTO journal(ts,kind,message) VALUES('2026-10-20T00:00:00Z','digest',?)", (SENTENCE_ZH,))
    d.execute("UPDATE records SET data=json_set(data,'$.note',?) WHERE id='old'", (SENTENCE_ZH,))
    d.execute("UPDATE records SET data=json_set(data,'$.detail',?) WHERE id='ib'", (SENTENCE_ZH,)); d.commit()
    assert SECRET in raw_dump(c)                                                                  # still in the audit history ...
    for url in ("/api/state", "/api/state?journal=500"):
        assert SECRET not in c.get(url).text                                                       # ... but never served
    assert "[redacted]" in c.get("/api/state").text


def test_ids_and_keys_are_not_mangled_and_idempotency_still_works(c):
    long_id = "a" * 40
    body = {"queue": [{"id": long_id[:30], "title": "ok"}], "idempotency_key": "idem-key-9999"}
    r1 = c.post("/api/update", json=body, headers=M); r2 = c.post("/api/update", json=body, headers=M)
    assert r1.status_code == r2.status_code == 200 and r2.json().get("replayed") is True
    assert any(x["id"] == long_id[:30] for x in c.get("/api/state").json()["queue"])


def test_trajectory_events_stay_compatible(c):
    c.post("/api/update", json={"inbox": [{"id": "i1", "title": "ask", "detail": "see password: hunter2x!", "kind": "decision", "options": ["a", "b"], "status": "open"}]}, headers=M)
    a = c.post("/api/action", json={"type": "choose", "section": "inbox", "id": "i1", "params": {"option": "a"}}, headers={"Authorization": "Bearer human-token-0123456789abcdef"}).json()["action"]
    from app.trajectory import export
    t = {x["trajectory_id"]: x for x in export(c.dbp)}["act:%d" % a]
    assert t["human"]["decision"] == "choose" and "hunter2x" not in str(t)
