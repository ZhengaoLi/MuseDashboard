import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-0123456789abcdef"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_TOKEN", TOKEN)
    from app.main import app
    with TestClient(app) as cl:
        yield cl


H = {"Authorization": "Bearer " + TOKEN}


def post(c, body, headers=None):
    return c.post("/api/update", json=body, headers={**H, **(headers or {})})


def test_dashboard_and_health(c):
    r = c.get("/")
    assert r.status_code == 200
    for s in ["focus", "needs-you", "upcoming", "in-progress", "matters", "week-grid", "timeline-full", "log", "routines", "system-status"]:
        assert 'id="%s"' % s in r.text  # frontend contract: fixed section ids
    assert "/_ui/actions.js" in r.text  # protected interaction layer
    assert c.get("/api/health").json()["status"] == "ok"
    st = c.get("/api/state").json()
    assert st["muse"]["freshness"] == "never" and st["focus"] is None and st["queue"] == []
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_auth(c):
    assert c.post("/api/update", json={"heartbeat": True}).status_code == 401
    assert c.post("/api/update", json={"heartbeat": True}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert post(c, {"heartbeat": True}).status_code == 200
    assert TOKEN not in c.get("/api/state").text


def test_write_disabled_without_token(c, monkeypatch):
    monkeypatch.delenv("MUSE_TOKEN")
    assert post(c, {"heartbeat": True}).status_code == 503


def test_read_only(c):
    assert c.put("/api/state").status_code == 405
    assert c.post("/api/state").status_code == 405


def test_partial_update_preserves_fields(c):
    post(c, {"projects": [{"id": "p1", "name": "Proj", "status": "active", "progress": "1", "next_step": "go"}]})
    r = post(c, {"projects": [{"id": "p1", "progress": "2"}]})
    assert r.json()["updated"] == ["projects:p1"]
    p = c.get("/api/state").json()["projects"][0]
    assert (p["name"], p["status"], p["progress"], p["next_step"]) == ("Proj", "active", "2", "go")
    post(c, {"projects": [{"id": "p1", "next_step": None}]})
    assert "next_step" not in c.get("/api/state").json()["projects"][0]
    assert post(c, {"projects": [{"id": "p1", "progress": "2"}]}).json()["unchanged"] == ["projects:p1"]


def test_empty_arrays_and_missing_sections_do_not_delete(c):
    post(c, {"queue": [{"id": "a", "title": "A"}], "focus": {"title": "F"}})
    post(c, {"queue": [], "journal": "x"})
    st = c.get("/api/state").json()
    assert len(st["queue"]) == 1 and st["focus"]["title"] == "F"


def test_focus_replace_and_clear(c):
    post(c, {"focus": {"title": "A", "reason": "r", "priority": "p1"}})
    post(c, {"focus": {"title": "B"}})
    f = c.get("/api/state").json()["focus"]
    assert f["title"] == "B" and "reason" not in f
    post(c, {"focus": None})
    assert c.get("/api/state").json()["focus"] is None


def test_delete(c):
    post(c, {"queue": [{"id": "a", "title": "A"}]})
    r = post(c, {"queue": [{"id": "a", "delete": True}, {"id": "zz", "delete": True}]}).json()
    assert r["deleted"] == ["queue:a"] and r["not_found"] == ["queue:zz"]
    assert c.get("/api/state").json()["queue"] == []


def test_validation_errors_and_atomicity(c):
    r = post(c, {"queue": [{"id": "ok", "title": "ok"}], "deadlines": [{"id": "d", "title": "x", "due": "not-a-date"}]})
    assert r.status_code == 422
    e = r.json()
    assert e["ok"] is False and e["error"]["details"][0]["path"].startswith("deadlines.0.due")
    assert c.get("/api/state").json()["queue"] == []  # nothing applied
    # new deadline without due -> rejected, and earlier valid change rolled back
    r = post(c, {"journal": "hi", "deadlines": [{"id": "d", "title": "x"}]})
    assert r.status_code == 422
    assert c.get("/api/state").json()["journal_total"] == 0
    assert post(c, {"queue": [{"id": "a", "status": "bogus"}]}).status_code == 422
    assert post(c, {"queue": [{"id": "bad id!", "title": "x"}]}).status_code == 422
    assert post(c, {"nope": 1}).status_code == 422
    assert post(c, {}).status_code == 422
    assert post(c, {"queue": [{"id": "a"}, {"id": "a"}]}).status_code == 422
    assert c.post("/api/update", content=b"{bad", headers=H).status_code == 400


def test_idempotency(c):
    body = {"journal": "once", "queue": [{"id": "a", "title": "A"}]}
    r1 = post(c, body, {"Idempotency-Key": "key-123"})
    r2 = post(c, body, {"Idempotency-Key": "key-123"})
    assert r2.json()["replayed"] is True and r1.json()["at"] == r2.json()["at"]
    assert c.get("/api/state").json()["journal_total"] == 1
    assert post(c, {"journal": "different"}, {"Idempotency-Key": "key-123"}).status_code == 409
    assert c.get("/api/state").json()["journal_total"] == 1


def test_journal_append_only(c):
    post(c, {"journal": ["a", {"message": "b", "kind": "decision"}]})
    post(c, {"journal": {"message": "c"}})
    j = c.get("/api/state").json()["journal"]
    assert [x["message"] for x in j] == ["c", "b", "a"]
    import sqlite3
    conn = sqlite3.connect(os.environ["MUSE_DB"])
    for sql in ("UPDATE journal SET message='x'", "DELETE FROM journal"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)


def test_deadline_sort_and_overdue(c):
    post(c, {"deadlines": [
        {"id": "late", "title": "L", "due": "2999-01-01"},
        {"id": "past", "title": "P", "due": "2000-01-01"},
        {"id": "pastdone", "title": "PD", "due": "2000-01-01", "status": "done"},
        {"id": "soon", "title": "S", "due": "2999-01-01T10:00:00Z"}]})
    d = c.get("/api/state").json()["deadlines"]
    assert [x["id"] for x in d] == ["past", "soon", "late", "pastdone"]
    assert d[0]["overdue"] and not d[3]["overdue"]


def test_freshness_and_audit(c):
    post(c, {"heartbeat": True})
    m = c.get("/api/state").json()["muse"]
    assert m["freshness"] == "fresh" and m["age_seconds"] < 5
    import sqlite3
    assert sqlite3.connect(os.environ["MUSE_DB"]).execute("select count(*) from audit").fetchone()[0] == 1


def test_xss_stored_verbatim_and_frontend_uses_textcontent(c):
    payload = "<script>alert(1)</script>"
    post(c, {"focus": {"title": payload}, "journal": payload})
    assert c.get("/api/state").json()["focus"]["title"] == payload
    js = c.get("/static/app.js").text
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_concurrent_writes(c):
    from concurrent.futures import ThreadPoolExecutor
    def w(i):
        return post(c, {"journal": "m%d" % i, "queue": [{"id": "q%d" % i, "title": "t"}]}).status_code
    with ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(w, range(24)))
    assert set(codes) == {200}
    st = c.get("/api/state").json()
    assert st["journal_total"] == 24 and len(st["queue"]) == 24
