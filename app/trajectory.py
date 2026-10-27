"""Interaction-trajectory evidence: Context -> Proposal -> Human decision -> Execution -> Outcome.

Preservation only. Nothing here changes what Muse may read or execute, and nothing learns online.
* `events` is an append-only table (DB triggers) in the same SQLite file as everything else.
* Secrets are scrubbed BEFORE they are stored; long text is truncated so mail bodies are never copied wholesale.
* `python -m app.trajectory export` rebuilds trajectories offline (read-only connection, JSON Lines on stdout).
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys

MAX_STR, MAX_JSON = 400, 8000
_SECRET_KEY = re.compile(r"(pass(word|wd|code)?$|^pwd$|token|secret|api[_-]?key|session|cookie|authorization|bearer|otp|2fa|private[_-]?key|credential|cvv|ssn)", re.I)
_SECRET_VAL = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)\b(password|passwd|pwd|passcode|api[_ -]?key|secret|token)\b\s*(is|:|=)\s*\S+"),
    re.compile(r"(?i)\b(verification|security|login|one[- ]time|auth(entication)?)\s+code\b\s*(is|:)?\s*\d{4,8}"),
    re.compile(r"(?i)\botp\b\D{0,6}\d{4,8}"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"(?<![A-Za-z0-9/])[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9/])"),
]
RED = "[redacted]"


def scrub(o, _depth=0):
    """Return a copy of o with secrets redacted and long strings truncated."""
    if _depth > 8:
        return "[too deep]"
    if isinstance(o, dict):
        return {str(k): (RED if _SECRET_KEY.search(str(k)) else scrub(v, _depth + 1)) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [scrub(v, _depth + 1) for v in o[:50]]
    if isinstance(o, str):
        for p in _SECRET_VAL:
            o = p.sub(RED, o)
        return o if len(o) <= MAX_STR else o[:MAX_STR] + "...[truncated %d chars]" % (len(o) - MAX_STR)
    return o


# Long-term trajectories keep structured facts only. Free text is reduced to {text: first 120 chars, len, sha1} (a change stays detectable,
# a whole email/document is never copied); fields in neither list are dropped and only their NAMES are noted.
KEEP = ("title", "name", "status", "kind", "area", "thread", "template", "stage", "stages", "priority", "order", "project", "task", "planned_for", "time_hint",
        "due", "deadline", "start", "end", "attendance", "sensitive", "options", "choice", "basis", "frequency", "last_run", "next_run", "progress", "merged_into", "ui_actions",
        "arxiv_id", "doi", "rec_date", "rank", "projects", "sources", "quote_ok", "candidates", "published")
TEXT = ("detail", "reason", "summary", "next_step", "note", "blockers", "abstract", "quote")


def project(rec):
    if not isinstance(rec, dict):
        return rec
    out, omitted = {}, []
    for k, v in rec.items():
        if k in ("id", "human", "delete"):
            continue
        if k in KEEP:
            out[k] = v
        elif k in TEXT and isinstance(v, str):
            out[k] = {"text": scrub(v)[:120], "len": len(v), "sha1": hashlib.sha1(v.encode()).hexdigest()[:12]}
        elif v not in (None, "", [], {}):
            omitted.append(k)
    if omitted:
        out["_omitted_fields"] = sorted(omitted)
    return out


def _dump(data):
    s = json.dumps(scrub(data), ensure_ascii=False, sort_keys=True)
    if len(s) > MAX_JSON:
        s = json.dumps({"_truncated": True, "size": len(s), "keys": sorted(data) if isinstance(data, dict) else None}, ensure_ascii=False)
    return s


def emit(conn, ts, event_id, kind, actor, data, section=None, target=None, thread=None, action_id=None, corr=None):
    """Append one event; a repeated event_id (retry, duplicate click) is ignored. Returns True if newly recorded.
    `corr` ties every event of ONE logical operation together (an update transaction, or one human decision and everything that follows it)."""
    return conn.execute("INSERT OR IGNORE INTO events(event_id,corr,ts,kind,actor,section,target,thread,action_id,data) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (event_id, corr, ts, kind, actor, section, target, thread, action_id, _dump(data))).rowcount == 1


def diff(cur, new):
    cur, new = project(cur), project(new)
    keys = set(cur) | set(new)
    return {k: {"from": cur.get(k), "to": new.get(k)} for k in sorted(keys) if cur.get(k) != new.get(k)}


def short_id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


DECISION = {"confirm": "accept", "ack": "acknowledge", "done": "accept", "resolve": "accept", "choose": "choose", "decline": "reject",
            "ui_action": "accept", "set_status": "set", "set_stage": "set", "reopen": "reverse", "attend": "reverse", "resume_reminding": "reverse",
            "not_attending": "declare", "stop_reminding": "request_stop"}
REQUEST_TYPES = ("ui_action", "stop_reminding", "resume_reminding")        # these ask Muse to do something; everything else only changes the dashboard


def human_event(conn, aid, ts, req, params, cur, new, sens):
    """Context + decision for one click (called inside the action's transaction)."""
    from .db import _impact
    tid = req.id if req.section == "threads" else cur.get("thread")
    trow = conn.execute("SELECT data FROM records WHERE section='threads' AND id=?", (tid,)).fetchone() if tid else None
    t = json.loads(trow["data"]) if trow else None
    prev = conn.execute("SELECT id,type FROM actions WHERE section=? AND target=? AND id<? ORDER BY id DESC LIMIT 1", (req.section, req.id, aid)).fetchone()
    d = {"action_id": aid, "type": req.type, "decision": DECISION.get(req.type, "state_change"),
         "effect": "request_to_muse" if req.type in REQUEST_TYPES else "dashboard_state_only",
         "params": params, "sensitive": bool(sens), "request_id": req.request_id,
         "status_before": cur.get("status"), "status_after": new.get("status"),
         "context": {"thread": ({k: t.get(k) for k in ("title", "status", "stage", "area", "basis") if k in t} | {"id": tid}) if t else None,
                     "open_work": _impact(conn, tid) if t else None,
                     "basis": cur.get("basis"), "source": cur.get("source")},
         "reverses_action": prev["id"] if (prev and req.type in ("reopen", "attend", "resume_reminding")) else None}
    emit(conn, ts, "act:%d" % aid, "human.action", "human", d, req.section, req.id, tid, aid, "act:%d" % aid)


# ------------------------------------------------------------------ offline export
def _rows(conn, sql, *a):
    return [dict(r) for r in conn.execute(sql, a)]


def _j(s):
    try:
        return json.loads(s) if s else None
    except ValueError:
        return s


# Harmless acceptance/E2E test records are excluded from research exports unless --include-test (events are never deleted).
# A record is a test record when its id, its thread's id, or its title says so; ordinary ids such as "test-ride" are NOT matched.
TEST_ID = re.compile(r"^(zz-|ux-check|e2e-|hook-e2e)", re.I)
TEST_TITLE = re.compile(r"^(E2E\b|ACCEPTANCE TEST)", re.I)


def is_test(rec_id, rec=None):
    rec = rec if isinstance(rec, dict) else {}
    return bool(TEST_ID.search(str(rec_id or "")) or TEST_ID.search(str(rec.get("thread") or "")) or TEST_TITLE.search(str(rec.get("title") or rec.get("name") or "")))


def export(db_path, since=0, include_test=False):
    """Yield one trajectory dict per human decision, then one per proposal that never received a response."""
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    have_events = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='events'").fetchone())
    ev = _rows(conn, "SELECT * FROM events ORDER BY id") if have_events else []
    for e in ev:
        e["data"] = _j(e["data"])
    by_act = {}
    for e in ev:
        if e["action_id"] is not None:
            by_act.setdefault(e["action_id"], []).append(e)
    results = {}
    for r in _rows(conn, "SELECT * FROM action_results"):
        results[r["action_id"]] = r
    acts = _rows(conn, "SELECT * FROM actions ORDER BY id")
    responded = {(a["section"], a["target"]) for a in acts}
    for a in acts:
        if a["id"] <= since or (not include_test and is_test(a["target"], _j(a["before"]))):
            continue
        mine = by_act.get(a["id"], [])
        human_ev = next((e for e in mine if e["kind"] == "human.action"), None)
        later = [x for x in acts if x["section"] == a["section"] and x["target"] == a["target"] and x["id"] > a["id"]]
        hist = [e for e in ev if e["section"] == a["section"] and e["target"] == a["target"] and e["kind"].startswith("record.") and e["ts"] <= a["ts"]]
        yield {
            "schema": "muse-trajectory/1", "trajectory_id": "act:%d" % a["id"],
            "context": (human_ev["data"]["context"] if human_ev else None) or {"unavailable": "action predates trajectory events"},
            "proposal": {"record_at_decision": project(_j(a["before"])), "history": [{"at": h["ts"], "kind": h["kind"], "corr": h["corr"], "data": h["data"]} for h in hist]},
            "human": {"at": a["ts"], "type": a["type"], "decision": (human_ev or {}).get("data", {}).get("decision", DECISION.get(a["type"], "state_change")),
                      "effect": "request_to_muse" if a["type"] in REQUEST_TYPES else "dashboard_state_only",
                      "params": scrub(_j(a["params"])), "sensitive": bool(a["sensitive"]), "request_id": a["request_id"],
                      "reverses_action": ((human_ev or {}).get("data") or {}).get("reverses_action")},
            "execution": {"primary_result": ({"status": results[a["id"]]["status"], "note": scrub(results[a["id"]]["note"]), "at": results[a["id"]]["ts"]}
                                              if a["id"] in results else None),
                          "events": [{"at": e["ts"], "kind": e["kind"], "corr": e["corr"], "data": e["data"]} for e in mine if e["kind"] == "muse.result"],
                          "note": "a click is a request, not proof of execution; only Muse results count" if a["type"] in REQUEST_TYPES else "dashboard state only; no external action implied"},
            "outcome": {"muse_events": [{"at": e["ts"], "kind": e["kind"], "corr": e["corr"], "data": e["data"]} for e in mine if e["kind"] in ("prediction", "outcome", "correction", "evidence")],
                        "later_human_actions": [{"action_id": x["id"], "type": x["type"], "at": x["ts"]} for x in later]},
            "flags": {"silence_is_consent": False},
        }
    for r in _rows(conn, "SELECT section,id,data,created_at FROM records WHERE section IN ('inbox','threads')"):
        d = _j(r["data"])
        if r["section"] == "inbox" and (r["section"], r["id"]) not in responded and d.get("status") in (None, "open") and (include_test or not is_test(r["id"], d)):
            yield {"schema": "muse-trajectory/1", "trajectory_id": "prop:%s:%s" % (r["section"], r["id"]), "context": None,
                   "proposal": {"record_now": project(d), "created_at": r["created_at"]}, "human": None,
                   "human_response": "none_observed", "flags": {"silence_is_consent": False, "note": "no response is NOT a rejection"}}
    conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.trajectory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="write trajectories as JSON Lines (read-only on the DB)")
    e.add_argument("--db", default=None); e.add_argument("--since", type=int, default=0, help="only actions with id greater than this")
    e.add_argument("--out", default="-"); e.add_argument("--include-test", action="store_true", help="also export zz-accept* acceptance-test records")
    a = ap.parse_args(argv)
    from .db import db_path
    out = sys.stdout if a.out == "-" else open(a.out, "w")
    n = 0
    for t in export(a.db or db_path(), a.since, a.include_test):
        out.write(json.dumps(t, ensure_ascii=False) + "\n"); n += 1
    if out is not sys.stdout:
        out.close()
    print("%d trajectories" % n, file=sys.stderr)


if __name__ == "__main__":
    main()
