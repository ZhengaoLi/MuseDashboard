"""Read-only alphaXiv wrapper: default deny in code, key never leaks, failures degrade to 'ok:false' (never an error status)."""
import importlib.util
import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import alphaxiv as ax

KEY = "axk-SECRET-0123456789abcdef"
DOC_TOOLS = ["discover_papers", "get_paper_content", "answer_pdf_queries", "read_files_from_github_repository", "find_researchers", "get_researcher", "get_researcher_papers",
             "resolve_researchers", "list_followed_researchers", "follow_researcher", "unfollow_researcher", "list_library", "save_papers_to_folder", "remove_papers_from_folder",
             "move_papers_between_folders", "create_folder", "rename_folder", "delete_folder", "edit_private_paper_metadata", "get_paper_notes", "list_annotated_papers", "tools/list", "", "GET_HOT_FEED", "get_hot_feed ", "../get_hot_feed"]
FEED = {"papers": [{"universal_paper_id": "2610.11111"}, {"universal_paper_id": "2610.22222v2"}, {"universal_paper_id": "cs/0101001"}, {"universal_paper_id": "upload-9f3a21"},
                   {"universal_paper_id": "2610.11111"}, {"universal_paper_id": "bad id with spaces"}, {"universal_paper_id": None}, {"x": 1}]}


class Mock(BaseHTTPRequestHandler):
    log = []
    mode = "json"
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        Mock.log.append((body.get("method"), (body.get("params") or {}).get("name"), self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer " + KEY:
            self.send_response(401); self.end_headers(); self.wfile.write(b"{}"); return
        if Mock.mode == "429": self.send_response(429); self.send_header("Retry-After", "60"); self.end_headers(); return
        if Mock.mode == "redirect": self.send_response(302); self.send_header("Location", "https://evil.example.com/x"); self.end_headers(); return
        if Mock.mode == "big": self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b"x" * 1_100_000); return
        if Mock.mode == "slow": time.sleep(1.5)
        m = body.get("method")
        if m == "notifications/initialized": self.send_response(202); self.end_headers(); return
        if m == "initialize": res = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "mock"}}
        elif m == "tools/call":
            if Mock.mode == "toolerr": res = {"isError": True, "content": [{"type": "text", "text": "assistant quota exceeded"}]}
            else: res = {"content": [{"type": "text", "text": json.dumps(FEED)}]}
        else: res = {}
        msg = {"jsonrpc": "2.0", "id": body.get("id"), "result": res}
        self.send_response(200); self.send_header("RateLimit", "limit=100, remaining=99, reset=60"); self.send_header("Mcp-Session-Id", "sess-1")
        if Mock.mode == "sse":
            self.send_header("Content-Type", "text/event-stream"); self.end_headers(); self.wfile.write(("event: message\ndata: %s\n\n" % json.dumps(msg)).encode())
        else:
            self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(msg).encode())


@pytest.fixture()
def mock():
    srv = HTTPServer(("127.0.0.1", 0), Mock); threading.Thread(target=srv.serve_forever, daemon=True).start()
    Mock.log, Mock.mode = [], "json"; ax.reset_for_tests()
    yield "http://127.0.0.1:%d/mcp/v1" % srv.server_port
    srv.shutdown()


def test_default_deny_every_other_tool_is_rejected_before_any_network_use(mock):
    c = ax.Client(key=KEY, base=mock)
    for name in DOC_TOOLS:
        with pytest.raises(ax.AlphaXivError) as e: c.call(name, {})
        assert e.value.code == "denied", name
    assert Mock.log == []                                                              # nothing at all was sent
    with pytest.raises(ax.AlphaXivError) as e: c.call("get_hot_feed", {"limit": 5, "folder_id": "x"})
    assert e.value.code == "bad_args"
    for bad in (0, 201, "30", 2.5, True, -1):
        with pytest.raises(ax.AlphaXivError): c.call("get_hot_feed", {"limit": bad})
    assert Mock.log == []
    assert set(ax.ALLOWED) == {"get_for_you_feed", "get_hot_feed"}                    # the whole allow-list; changing it is a code review


def test_a_custom_base_url_is_only_allowed_for_local_tests():
    for base in ("https://evil.example.com/mcp/v1", "http://api.alphaxiv.org/mcp/v1", "http://127.0.0.1.evil.com/x"):
        with pytest.raises(ax.AlphaXivError): ax.Client(key=KEY, base=base)
    assert ax.DEFAULT_BASE == "https://api.alphaxiv.org/mcp/v1"


@pytest.mark.parametrize("mode", ["json", "sse"])
def test_feed_works_over_json_and_event_stream_and_ids_are_normalised_not_assumed(mock, mode):
    Mock.mode = mode
    r = ax.feed("hot", 30, client=ax.Client(key=KEY, base=mock))
    assert r["ok"] and r["source"] == "alphaxiv" and r["count"] == 4 and r["mapped"] == 3
    got = {i["paper_id"]: i["arxiv_id"] for i in r["ids"]}
    assert got == {"2610.11111": "2610.11111", "2610.22222v2": "2610.22222", "cs/0101001": "cs/0101001", "upload-9f3a21": None}        # an uploaded paper id is NOT treated as arXiv
    methods = [m for m, _, _ in Mock.log]; assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert {n for _, n, _ in Mock.log if n} == {"get_hot_feed"} and r["rate_headers"].get("ratelimit")


def test_errors_are_short_codes_and_never_contain_the_key(mock):
    for mode, code in (("429", "quota"), ("redirect", "network"), ("big", "too_large"), ("toolerr", "quota")):
        Mock.mode = mode; ax.reset_for_tests()
        with pytest.raises(ax.AlphaXivError) as e: ax.Client(key=KEY, base=mock).call("get_hot_feed", {})
        assert e.value.code == code and KEY not in repr(e.value) + str(e.value) + e.value.detail, mode
    Mock.mode = "json"
    with pytest.raises(ax.AlphaXivError) as e: ax.Client(key="wrong-key", base=mock).call("get_hot_feed", {})
    assert e.value.code == "auth" and "wrong-key" not in repr(e.value)
    ax.TIMEOUT, Mock.mode = 0.3, "slow"
    try:
        with pytest.raises(ax.AlphaXivError) as e: ax.Client(key=KEY, base=mock).call("get_hot_feed", {})
        assert e.value.code in ("timeout", "network")
    finally:
        ax.TIMEOUT = 15


def test_without_a_key_nothing_is_sent_and_the_failure_is_a_code(mock, monkeypatch):
    monkeypatch.delenv("ALPHAXIV_API_KEY", raising=False); monkeypatch.delenv("ALPHAXIV_API_KEY_FILE", raising=False)
    r = ax.safe_feed("hot", 30, client=ax.Client(base=mock))
    assert r == {"ok": False, "source": "alphaxiv", "feed": "hot", "error": "not_configured"} and Mock.log == []


def test_cache_and_hard_daily_cap_protect_the_quota(mock):
    c = ax.Client(key=KEY, base=mock)
    a = ax.feed("hot", 30, client=c, now=1_000_000.0); b = ax.feed("hot", 30, client=c, now=1_000_100.0)
    assert a["cached"] is False and b["cached"] is True and len([1 for m, _, _ in Mock.log if m == "tools/call"]) == 1
    for lim in (31, 32, 33):
        ax.feed("hot", lim, client=c, now=1_000_200.0)                                  # different keys defeat the cache: only the cap stops a loop
    r = ax.safe_feed("hot", 34, client=c, now=1_000_300.0)
    assert r["ok"] is False and r["error"] == "guard"
    assert len([1 for m, _, _ in Mock.log if m == "tools/call"]) == ax.DAILY_CAP        # never more than the cap upstream per day
    assert ax.safe_feed("for_you", 30, client=c, now=1_000_300.0)["ok"] is True          # the cap is per feed
    assert ax.safe_feed("nonsense", 30)["error"] == "bad_args"


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_DB", str(tmp_path / "t.db")); monkeypatch.setenv("MUSE_TOKEN", "test-token-0123456789abcdef"); monkeypatch.setenv("MUSE_HUMAN_TOKEN", "human-token-0123456789abcdef")
    ax.reset_for_tests()
    from app.main import app
    with TestClient(app) as cl: yield cl


def test_route_needs_the_muse_token_and_never_exposes_the_key_or_an_error_status(app_client, monkeypatch, mock):
    M = {"Authorization": "Bearer test-token-0123456789abcdef"}; H = {"Authorization": "Bearer human-token-0123456789abcdef"}
    assert app_client.get("/api/papers/source/alphaxiv").status_code == 401
    assert app_client.get("/api/papers/source/alphaxiv", headers=H).status_code == 401                     # the human token cannot use it
    monkeypatch.delenv("ALPHAXIV_API_KEY", raising=False)
    r = app_client.get("/api/papers/source/alphaxiv", headers=M)
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"] == "not_configured"      # upstream/config trouble is data, not a crash
    assert app_client.get("/api/papers/source/alphaxiv?feed=delete_folder", headers=M).status_code == 422
    assert app_client.get("/api/papers/source/alphaxiv?limit=9999", headers=M).status_code == 422
    real = ax.Client
    monkeypatch.setattr(ax, "Client", lambda *a, **k: real(key=KEY, base=mock))
    r = app_client.get("/api/papers/source/alphaxiv?feed=for_you&limit=10", headers=M)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["count"] == 4 and KEY not in r.text
    assert not any(m == "tools/call" and n != "get_for_you_feed" for m, n, _ in Mock.log)
    st = app_client.get("/api/state").json()
    assert KEY not in json.dumps(st) and st["queue"] == [] and st["inbox"] == []                           # nothing is stored or turned into work


def test_alphaxiv_is_an_accepted_source_and_dedup_only_adds_the_link(app_client):
    M = {"Authorization": "Bearer test-token-0123456789abcdef"}
    base = {"id": "p", "title": "Balancing LoRA adapters for multi-task learning", "arxiv_id": "2610.12345", "rec_date": "2026-10-24", "reason": "Original reason.", "rank": 3,
            "sources": [{"name": "arxiv", "url": "https://arxiv.org/abs/2610.12345"}]}
    assert app_client.post("/api/update", json={"papers": [base]}, headers=M).status_code == 200
    again = dict(base, id="q", reason="alphaXiv said so", rank=1, sources=[{"name": "alphaxiv", "url": "https://www.alphaxiv.org/abs/2610.12345"}])
    assert app_client.post("/api/update", json={"papers": [again]}, headers=M).status_code == 200
    p = [x for d in app_client.get("/api/papers").json()["days"] for x in d["papers"]]
    assert len(p) == 1 and p[0]["reason"] == "Original reason." and p[0]["rank"] == 3 and {s["name"] for s in p[0]["sources"]} == {"arxiv", "alphaxiv"}
    bad = dict(base, id="z", arxiv_id="2610.99999", title="Another paper with a long enough title", sources=[{"name": "alphaxiv", "url": "https://alphaxiv.org.evil.com/abs/1"}])
    assert app_client.post("/api/update", json={"papers": [bad]}, headers=M).status_code == 422


def test_client_helper_uses_only_ids_that_really_are_arxiv_ids_and_degrades(monkeypatch):
    spec = importlib.util.spec_from_file_location("pc", pathlib.Path(__file__).resolve().parents[1] / "client" / "papers.py"); pc = importlib.util.module_from_spec(spec); spec.loader.exec_module(pc)
    calls = []
    def gj(url, headers, timeout=30):
        calls.append((url, headers["Authorization"]))
        if "feed=for_you" in url: return {"ok": False, "source": "alphaxiv", "error": "not_configured"}
        return {"ok": True, "ids": [{"paper_id": "2610.11111", "arxiv_id": "2610.11111"}, {"paper_id": "upload-1", "arxiv_id": None}]}
    monkeypatch.setattr(pc, "_get_json", gj)
    monkeypatch.setattr(pc, "fetch_arxiv_ids", lambda ids, timeout=30: [{"arxiv_id": i, "title": "T " + i, "authors": [], "abstract": "a", "published": "2026-10-20", "updated": None, "sources": [{"name": "arxiv", "url": "https://arxiv.org/abs/" + i}], "signals": {}} for i in ids])
    out, errs = pc.fetch_alphaxiv("http://127.0.0.1:5050", "tok")
    assert [c["arxiv_id"] for c in out] == ["2610.11111"] and errs == ["alphaxiv(for_you): not_configured"]       # the unmapped upload id is skipped, never guessed
    assert {s["name"] for s in out[0]["sources"]} == {"arxiv", "alphaxiv"} and out[0]["signals"]["alphaxiv_feed"] == "hot"
    monkeypatch.setattr(pc, "_get_json", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert pc.fetch_alphaxiv("http://127.0.0.1:5050", "tok") == ([], ["alphaxiv(hot): OSError", "alphaxiv(for_you): OSError"])


def _pc():
    spec = importlib.util.spec_from_file_location("pc2", pathlib.Path(__file__).resolve().parents[1] / "client" / "papers.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_for_you_is_not_age_filtered_and_unmapped_ids_are_skipped_with_statistics(monkeypatch):
    pc = _pc()
    def gj(url, headers, timeout=30):
        ids = [{"paper_id": "2610.10001", "arxiv_id": "2610.10001"}, {"paper_id": "2506.00001", "arxiv_id": "2506.00001"}, {"paper_id": "2610.some-link-blog", "arxiv_id": None}]
        return {"ok": True, "ids": ids}
    monkeypatch.setattr(pc, "_get_json", gj)
    monkeypatch.setattr(pc, "fetch_arxiv_ids", lambda ids, timeout=30: [{"arxiv_id": i, "title": "T " + i, "authors": [], "abstract": "a", "published": "2025-06-01" if i.startswith("2506") else "2026-10-20", "updated": None, "sources": [{"name": "arxiv", "url": "https://arxiv.org/abs/" + i}], "signals": {}} for i in ids])
    st = {}
    out, errs = pc.fetch_alphaxiv("http://127.0.0.1:5050", "tok", feeds=("hot", "for_you"), stats=st)
    assert {c["arxiv_id"] for c in out} == {"2610.10001", "2506.00001"}                                  # an old paper is kept: Muse decides, there is no age cut-off
    assert st["hot"] == {"returned": 3, "mapped": 2, "unmapped_skipped": 1} and st["for_you"]["unmapped_skipped"] == 1 and errs == []
    assert next(c for c in out if c["arxiv_id"] == "2610.10001")["signals"]["alphaxiv_feed"] == "hot"      # hot wins as the label when a paper is in both


def test_papers_already_recommended_on_any_day_are_dropped_from_the_candidates(monkeypatch):
    pc = _pc()
    pages = {None: {"days": [{"date": "2026-10-24", "papers": [{"id": "arxiv:2610.12345", "arxiv_id": "2610.12345", "title": "Balancing LoRA adapters for multi-task learning", "status": "not_relevant"}]}], "next_before": "2026-10-24"},
             "2026-10-24": {"days": [{"date": "2026-10-20", "papers": [{"id": "doi:abc", "doi": "10.1234/xyz.9", "title": "A previously recommended paper found by DOI only", "status": "saved"}]}], "next_before": None}}
    monkeypatch.setattr(pc, "_get_json", lambda url, headers, timeout=30: pages[url.split("before=")[1] if "before=" in url else None])
    known = pc.known_papers("http://127.0.0.1:5050")
    cands = [{"arxiv_id": "2610.12345v2", "title": "x"}, {"title": "Balancing LoRA Adapters for Multi-Task Learning!", "sources": []}, {"doi": "https://doi.org/10.1234/XYZ.9", "title": "some other title entirely here"},
             {"arxiv_id": "2610.55555", "title": "A genuinely new paper about something else entirely"}]
    fresh, dropped = pc.drop_known(cands, known)
    assert dropped == 3 and [c["arxiv_id"] for c in fresh] == ["2610.55555"]                              # by arXiv id (with version), by title, by DOI; a 'not relevant' paper stays excluded
