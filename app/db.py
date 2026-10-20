"""SQLite storage: schema, atomic updates, state snapshot."""
import hashlib
import json
import os
import sqlite3
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone

from .schema import AREAS, LOCAL, SECTIONS, JournalEntry, to_ts

_write_lock = threading.Lock()
STALE_FRESH_MIN = int(os.environ.get("FRESH_MINUTES", "60"))
STALE_AGING_MIN = int(os.environ.get("STALE_MINUTES", "360"))

DDL = """
CREATE TABLE IF NOT EXISTS records(section TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(section,id));
CREATE TABLE IF NOT EXISTS focus(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, idem_key TEXT, changes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, type TEXT NOT NULL,
  section TEXT NOT NULL, target TEXT NOT NULL, params TEXT, before TEXT, after TEXT, sensitive INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS context_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
  type TEXT NOT NULL, place TEXT NOT NULL, observed_ts REAL NOT NULL, observed_at TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS context_events_obs ON context_events(observed_ts);
CREATE TABLE IF NOT EXISTS action_results(action_id INTEGER PRIMARY KEY, status TEXT NOT NULL, note TEXT, ts TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS actions_no_update BEFORE UPDATE ON actions BEGIN SELECT RAISE(ABORT,'actions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS actions_no_delete BEFORE DELETE ON actions BEGIN SELECT RAISE(ABORT,'actions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal BEGIN SELECT RAISE(ABORT,'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal BEGIN SELECT RAISE(ABORT,'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit is append-only'); END;
"""


def local_today():
    """The user's current local calendar date (server TZ). Isolated so tests can move the clock across midnight."""
    return datetime.now(LOCAL).date()


def _plan_violation(rec, errors, i):
    if rec.get("time_hint") and not rec.get("planned_for"):
        errors.append({"path": "queue.%d.time_hint" % i, "msg": "time_hint needs planned_for (it is only a part of that day, never a time)"}); return True
    return False


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or []


def db_path():
    return os.environ.get("MUSE_DB", "/data/muse.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    os.makedirs(os.path.dirname(db_path()) or ".", exist_ok=True)
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(DDL)
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(actions)")}
    if "request_id" not in acols:                    # v2.1 additive migration
        conn.execute("ALTER TABLE actions ADD COLUMN request_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS actions_req ON actions(request_id) WHERE request_id IS NOT NULL")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(journal)")}
    for c in ("title", "area", "thread"):          # v2 additive migration (existing rows keep NULLs)
        if c not in cols:
            conn.execute("ALTER TABLE journal ADD COLUMN %s TEXT" % c)
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def body_hash(payload: dict) -> str:
    p = {k: v for k, v in payload.items() if k != "idempotency_key"}
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def apply_update(req, payload: dict, idem_key):
    """Apply a validated UpdateRequest atomically. Returns compact result dict."""
    h = body_hash(payload)
    with _write_lock:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if idem_key:
                    row = conn.execute("SELECT body_hash,response FROM idempotency WHERE key=?", (idem_key,)).fetchone()
                    if row:
                        conn.execute("ROLLBACK")
                        if row["body_hash"] != h:
                            raise ApiError(409, "idempotency_conflict",
                                           "idempotency key was already used with a different payload")
                        res = json.loads(row["response"])
                        res["replayed"] = True
                        return res
                res = _apply(conn, req, idem_key, h)
                conn.execute("COMMIT")
                return res
            except ApiError:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def _apply(conn, req, idem_key, h):
    now = now_iso()
    out = {"created": [], "updated": [], "deleted": [], "unchanged": [], "not_found": []}
    errors, touched, deleted = [], set(), set()

    if "focus" in req.model_fields_set:
        if req.focus is None:
            if conn.execute("DELETE FROM focus").rowcount:
                out["deleted"].append("focus")
        else:
            data = req.focus.model_dump(exclude_none=True)
            conn.execute("INSERT INTO focus(id,data,updated_at) VALUES(1,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                         (json.dumps(data, ensure_ascii=False), now))
            out["updated"].append("focus")

    for sec, (_, label, required) in SECTIONS.items():
        items = getattr(req, sec)
        if not items:
            continue
        seen = set()
        for i, it in enumerate(items):
            if it.id in seen:
                errors.append({"path": f"{sec}.{i}.id", "msg": f"duplicate id '{it.id}' in one request"})
                continue
            seen.add(it.id)
            ref = f"{sec}:{it.id}"
            row = conn.execute("SELECT data FROM records WHERE section=? AND id=?", (sec, it.id)).fetchone()
            if it.delete:
                if row:
                    conn.execute("DELETE FROM records WHERE section=? AND id=?", (sec, it.id))
                    out["deleted"].append(ref); deleted.add((sec, it.id))
                else:
                    out["not_found"].append(ref)
                continue
            patch = it.model_dump(exclude_unset=True, exclude={"id", "delete"})
            if sec == "threads" and "ui_actions" in patch and not patch["ui_actions"]:
                patch["ui_actions"] = None                  # [] and null both clear the declarations
            if row:
                cur = json.loads(row["data"])
                new = {**cur, **patch}
                new = {k: v for k, v in new.items() if v is not None}
                if sec == "inbox" and _inbox_violation(cur, new, patch, errors, i):
                    continue
                if sec == "queue" and _plan_violation(new, errors, i):
                    continue
                if new == cur:
                    out["unchanged"].append(ref)
                    continue
                if not new.get(label):
                    errors.append({"path": f"{sec}.{i}.{label}", "msg": "cannot be null"})
                    continue
                conn.execute("UPDATE records SET data=?, updated_at=? WHERE section=? AND id=?",
                             (json.dumps(new, ensure_ascii=False), now, sec, it.id))
                out["updated"].append(ref); touched.add((sec, it.id))
            else:
                new = {k: v for k, v in patch.items() if v is not None}
                if sec == "inbox" and _inbox_violation({}, new, patch, errors, i):
                    continue
                if sec == "queue" and _plan_violation(new, errors, i):
                    continue
                new.setdefault(label, it.id)
                missing = [f for f in required if f not in new]
                if missing:
                    errors.append({"path": f"{sec}.{i}", "msg": f"new record '{it.id}' requires: {', '.join(missing)}"})
                    continue
                conn.execute("INSERT INTO records(section,id,data,created_at,updated_at) VALUES(?,?,?,?,?)",
                             (sec, it.id, json.dumps(new, ensure_ascii=False), now, now))
                out["created"].append(ref); touched.add((sec, it.id))

    _check_refs(conn, errors, touched, deleted)
    if errors:
        raise ApiError(422, "validation_error", "update rejected; nothing was applied", errors)

    jn = 0
    if req.journal is not None:
        entries = req.journal if isinstance(req.journal, list) else [req.journal]
        for e in entries:
            if isinstance(e, str):
                e = JournalEntry(message=e)
            conn.execute("INSERT INTO journal(ts,kind,message,title,area,thread) VALUES(?,?,?,?,?,?)",
                         (now, e.kind, e.message, e.title, e.area, e.thread))
            jn += 1

    if req.action_results:
        n_res = 0
        for r in req.action_results:
            if not conn.execute("SELECT 1 FROM actions WHERE id=?", (r.id,)).fetchone():
                errors.append({"path": "action_results.%d.id" % r.id, "msg": "no such action"}); continue
            n_res += conn.execute("INSERT OR IGNORE INTO action_results(action_id,status,note,ts) VALUES(?,?,?,?)", (r.id, r.status, r.note, now)).rowcount
        if errors:
            raise ApiError(422, "validation_error", "update rejected; nothing was applied", errors)
        out["results_recorded"] = n_res
    if req.actions_seen is not None:
        top = conn.execute("SELECT COALESCE(MAX(id),0) m FROM actions").fetchone()["m"]
        cur_seen = int((conn.execute("SELECT value FROM meta WHERE key='actions_seen'").fetchone() or {"value": 0})["value"])
        new_seen = max(cur_seen, min(req.actions_seen, top))
        conn.execute("INSERT INTO meta(key,value) VALUES('actions_seen',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(new_seen),))
        out["actions_seen"] = new_seen
    res = {"ok": True, "at": now}
    res.update({k: v for k, v in out.items() if v})
    if jn:
        res["journal"] = jn
    conn.execute("INSERT INTO meta(key,value) VALUES('last_update',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
    conn.execute("INSERT INTO audit(ts,idem_key,changes) VALUES(?,?,?)", (now, idem_key, json.dumps(res, ensure_ascii=False)))
    if idem_key:
        conn.execute("DELETE FROM idempotency WHERE created_at < datetime('now','-30 days')")
        conn.execute("INSERT INTO idempotency(key,body_hash,response,created_at) VALUES(?,?,?,?)",
                     (idem_key, h, json.dumps(res, ensure_ascii=False), now))
    return res


def _inbox_violation(cur, new, patch, errors, i):
    """Muse may request confirmation of sensitive items, never answer or downgrade them."""
    if cur.get("sensitive") and patch.get("sensitive") is not True and "sensitive" in patch:
        errors.append({"path": "inbox.%d.sensitive" % i, "msg": "a sensitive item cannot be downgraded"}); return True
    if new.get("sensitive") and patch.get("status") in ("confirmed", "declined", "acknowledged"):
        errors.append({"path": "inbox.%d.status" % i, "msg": "sensitive items can only be confirmed/declined/acknowledged by the human via the UI"}); return True
    if new.get("sensitive") and "choice" in patch:
        errors.append({"path": "inbox.%d.choice" % i, "msg": "choices on sensitive items are set by the human via the UI"}); return True
    return False


def _check_refs(conn, errors, touched, deleted):
    """Touched records' thread / merged_into must exist; deleting a still-referenced record is refused."""
    for sec, rid in sorted(touched):
        r = conn.execute("SELECT data FROM records WHERE section=? AND id=?", (sec, rid)).fetchone()
        d = json.loads(r["data"])
        if d.get("thread") and not conn.execute("SELECT 1 FROM records WHERE section='threads' AND id=?", (d["thread"],)).fetchone():
            errors.append({"path": "%s.%s.thread" % (sec, rid), "msg": "unknown thread '%s' (create it in `threads` first)" % d["thread"]})
        m = d.get("merged_into")
        if m and (m == rid or not conn.execute("SELECT 1 FROM records WHERE section=? AND id=?", (sec, m)).fetchone()):
            errors.append({"path": "%s.%s.merged_into" % (sec, rid), "msg": "merged_into must be another existing id in the same section"})
        if sec == "threads" and d.get("stage") and d.get("stages") and d["stage"] not in d["stages"]:
            errors.append({"path": "threads.%s.stage" % rid, "msg": "stage must be one of its stages"})
    for sec, rid in sorted(deleted):
        for other in SECTIONS:
            for r in conn.execute("SELECT id,data FROM records WHERE section=?", (other,)):
                d = json.loads(r["data"])
                if (sec == "threads" and d.get("thread") == rid) or (other == sec and d.get("merged_into") == rid):
                    errors.append({"path": "%s.%s" % (sec, rid), "msg": "cannot delete: still referenced by %s:%s" % (other, r["id"])}); break


_HUMAN = {
    "inbox": {"ack": "acknowledged", "confirm": "confirmed", "decline": "declined", "resolve": "resolved", "reopen": "open"},
    "queue": {"done": "done", "reopen": "todo"},
    "deadlines": {"done": "done", "reopen": "open"},
    "threads": {"done": "done", "reopen": "active"},
}
def _impact(conn, tid):
    """What is still attached to a thread when it is archived. Informational: archiving never cascades or cancels anything."""
    out = {"tasks": 0, "waiting": 0, "deadlines": 0, "events": 0, "reminders": 0, "routines": 0}
    now = _time.time()
    for r in conn.execute("SELECT section,data FROM records WHERE section IN ('queue','deadlines','schedule','automations')"):
        d = json.loads(r["data"])
        if d.get("thread") != tid or d.get("merged_into"):
            continue
        sec = r["section"]
        if sec == "queue" and d.get("status") not in ("done", "dropped"):
            out["tasks"] += 1; out["waiting"] += d.get("status") == "waiting"
        elif sec == "deadlines" and d.get("status", "open") in ("open", "unknown"):
            out["deadlines"] += 1
        elif sec == "schedule" and d.get("status") != "cancelled" and to_ts(d.get("end") or d["start"], end_of_day=True) >= now:
            out["events"] += 1
        elif sec == "automations" and d.get("status") != "paused":
            if d.get("kind") == "once":
                out["reminders"] += bool(d.get("next_run") and to_ts(d["next_run"]) >= now)
            else:
                out["routines"] += 1
    return out


_QSTAT = ("todo", "active", "waiting", "blocked", "done", "dropped")


def _ua_expired(ua, now_ts):
    return bool(ua.get("expires_at")) and to_ts(ua["expires_at"], end_of_day=True) < now_ts


def _validate_ui_action(conn, req, thread):
    """The browser may only echo back one of the thread's *stored* declarations with in-range choice values."""
    if req.section != "threads":
        raise ApiError(422, "validation_error", "ui_action targets a thread")
    if thread.get("status") in ("done", "dropped"):
        raise ApiError(422, "validation_error", "thread is closed")
    p = req.params or {}
    if set(p) - {"ui_action", "selection"}:
        raise ApiError(422, "validation_error", "params may only contain ui_action and selection")
    ua = next((u for u in (thread.get("ui_actions") or []) if u["id"] == p.get("ui_action")), None)
    if not ua:
        raise ApiError(422, "unknown_action", "this thread has no action '%s' (it may have been withdrawn)" % p.get("ui_action"))
    if _ua_expired(ua, _time.time()):
        raise ApiError(410, "expired", "this action has expired")
    steps = ua.get("steps") or []
    sel = p.get("selection") or {}
    if not isinstance(sel, dict) or set(sel) != {s["id"] for s in steps}:
        raise ApiError(422, "validation_error", "selection must answer exactly the declared steps: [%s]" % ", ".join(s["id"] for s in steps))
    labels = {}
    for st in steps:
        ch = {c["value"]: c["label"] for c in st["choices"]}
        if sel[st["id"]] not in ch:
            raise ApiError(422, "validation_error", "'%s' is not a valid choice for step '%s'" % (sel[st["id"]], st["id"]))
        labels[st["id"]] = ch[sel[st["id"]]]
    sel = dict(sorted(sel.items()))
    out = {"ui_action": ua["id"], "intent": ua["intent"], "label": ua["label"], "selection": sel, "selection_labels": labels, "sensitive": bool(ua.get("sensitive"))}
    dup = conn.execute("""SELECT a.id FROM actions a LEFT JOIN action_results r ON r.action_id=a.id
                          WHERE a.type='ui_action' AND a.target=? AND r.action_id IS NULL
                            AND json_extract(a.params,'$.ui_action')=? AND json_extract(a.params,'$.selection')=json(?) ORDER BY a.id DESC LIMIT 1""",
                       (req.id, ua["id"], json.dumps(sel, sort_keys=True))).fetchone()
    if dup:
        out["_dup"], out["_dup_id"] = True, dup["id"]
    return out, bool(ua.get("sensitive"))


def apply_action(req):
    """A human click: change one record's state and log it. One-off by definition (never a stored preference)."""
    with _write_lock:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if req.request_id:                               # exact retry of the same click: same action, no new effect
                ex = conn.execute("SELECT id,ts FROM actions WHERE request_id=?", (req.request_id,)).fetchone()
                if ex:
                    conn.execute("ROLLBACK")
                    return {"ok": True, "action": ex["id"], "at": ex["ts"], "changed": False, "duplicate": True}
            row = conn.execute("SELECT data FROM records WHERE section=? AND id=?", (req.section, req.id)).fetchone()
            if not row:
                raise ApiError(404, "not_found", "no such %s record '%s'" % (req.section, req.id))
            cur = json.loads(row["data"]); new = dict(cur); params = req.params or {}
            if req.type == "ui_action":
                params, sens_ua = _validate_ui_action(conn, req, cur)
                if params.pop("_dup", None):
                    conn.execute("ROLLBACK")
                    return {"ok": True, "action": params["_dup_id"], "at": now_iso(), "changed": False, "duplicate": True, "already_pending": True}
                if sens_ua and not req.sensitive_ack:
                    raise ApiError(403, "confirmation_required", "sensitive action: explicit confirmation from the UI dialog is required")
            elif req.type == "set_status" and req.section == "queue":
                if params.get("status") not in _QSTAT:
                    raise ApiError(422, "validation_error", "params.status must be one of %s" % ", ".join(_QSTAT))
                new["status"] = params["status"]
            elif req.type == "set_stage" and req.section == "threads":
                if params.get("stage") not in (cur.get("stages") or []):
                    raise ApiError(422, "validation_error", "params.stage must be one of the thread's stages")
                new["stage"] = params["stage"]
            elif req.type == "choose" and req.section == "inbox":
                if params.get("option") not in (cur.get("options") or []):
                    raise ApiError(422, "validation_error", "params.option must be one of the item's options")
                new["choice"], new["status"] = params["option"], "resolved"
            elif req.type in ("not_attending", "attend") and req.section == "schedule":
                # an internal statement of intent; never an external cancellation (no registration/booking is touched)
                if req.type == "not_attending":
                    if cur.get("status") == "cancelled":
                        raise ApiError(422, "validation_error", "the event itself is already cancelled")
                    if to_ts(cur.get("end") or cur["start"], end_of_day=True) < _time.time():
                        raise ApiError(422, "validation_error", "the event is already over")
                    new["attendance"] = "not_going"
                else:
                    if cur.get("attendance") != "not_going":
                        raise ApiError(422, "validation_error", "you are not marked as 'not attending'")
                    new.pop("attendance", None)
                params = {"internal_only": True}
            elif req.type in ("stop_reminding", "resume_reminding") and req.section == "automations":
                # A REQUEST to Muse. The dashboard record mirrors Muse's native scheduled task, so it changes only when Muse
                # confirms (it updates the mirror and reports via action_results). Nothing here claims the task is stopped.
                if cur.get("kind") != "once":
                    raise ApiError(422, "validation_error", "only one-time reminders can be stopped from here")
                pend = conn.execute("""SELECT a.id, a.type FROM actions a LEFT JOIN action_results r ON r.action_id=a.id
                                       WHERE a.section='automations' AND a.target=? AND a.type IN ('stop_reminding','resume_reminding') AND r.action_id IS NULL
                                       ORDER BY a.id DESC LIMIT 1""", (req.id,)).fetchone()
                if pend and pend["type"] == req.type:
                    conn.execute("ROLLBACK")
                    return {"ok": True, "action": pend["id"], "at": now_iso(), "changed": False, "duplicate": True, "already_pending": True}
                if req.type == "stop_reminding" and cur.get("status") == "paused":
                    raise ApiError(422, "validation_error", "this reminder is already stopped")
                if req.type == "resume_reminding":
                    last = conn.execute("""SELECT a.type, r.status AS res FROM actions a LEFT JOIN action_results r ON r.action_id=a.id
                                           WHERE a.section='automations' AND a.target=? AND a.type IN ('stop_reminding','resume_reminding')
                                           ORDER BY a.id DESC LIMIT 1""", (req.id,)).fetchone()
                    stop_done = bool(last and last["type"] == "stop_reminding" and last["res"] == "completed")        # Muse reports it already stopped
                    if cur.get("status") != "paused" and not (pend and pend["type"] == "stop_reminding") and not stop_done:
                        raise ApiError(422, "validation_error", "there is nothing to resume or withdraw")
                params = {"internal_only": True, "mirror_changes_when_muse_confirms": True}
            elif req.type in _HUMAN.get(req.section, {}):
                if req.section == "threads" and req.type == "done":
                    if cur.get("status") in ("done", "dropped"):
                        raise ApiError(422, "validation_error", "thread is already archived")
                    imp = _impact(conn, req.id)
                    if imp["tasks"] and params.get("acknowledge_open") is not True:      # enforced here, not just in the UI
                        raise ApiError(409, "open_work", "this thread still has open tasks; archiving needs explicit acknowledgment", [{"impact": imp}])
                    params = {"impact": imp, "acknowledged_open_tasks": bool(imp["tasks"])}
                new["status"] = _HUMAN[req.section][req.type]
            else:
                raise ApiError(422, "validation_error", "action '%s' is not allowed on %s" % (req.type, req.section))
            if req.type not in ("ui_action", "stop_reminding", "resume_reminding") and new == cur:
                conn.execute("ROLLBACK")                      # nothing would change: no second action is logged (double clicks, stale buttons)
                last = conn.execute("SELECT id FROM actions WHERE section=? AND target=? ORDER BY id DESC LIMIT 1", (req.section, req.id)).fetchone()
                return {"ok": True, "action": last["id"] if last else None, "at": now_iso(), "changed": False, "noop": True, "status": new.get("status")}
            sens = bool(cur.get("sensitive"))
            if sens and req.type != "ui_action" and not req.sensitive_ack:
                raise ApiError(403, "confirmation_required", "sensitive item: explicit confirmation from the UI dialog is required")
            now = now_iso()
            cur_ = conn.execute("INSERT INTO actions(ts,actor,type,section,target,params,before,after,sensitive,request_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (now, "human", req.type, req.section, req.id, json.dumps(params, ensure_ascii=False),
                                 json.dumps(cur, ensure_ascii=False), json.dumps(new, ensure_ascii=False), int(sens or (req.type == "ui_action" and bool(params.get("sensitive")))), req.request_id))
            aid = cur_.lastrowid
            if new != cur:
                new["human"] = {"action": aid, "type": req.type, "at": now}
                conn.execute("UPDATE records SET data=?, updated_at=? WHERE section=? AND id=?",
                             (json.dumps(new, ensure_ascii=False), now, req.section, req.id))
            conn.execute("COMMIT")
            return {"ok": True, "action": aid, "at": now, "changed": new != cur, "status": new.get("status")}
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


_PRIO = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
_PSTAT = {"active": 0, "blocked": 1, "planned": 2, "paused": 3, "unknown": 4, "done": 5}


CONTEXT_TTL_MIN = int(os.environ.get("CONTEXT_TTL_MINUTES", "30"))
MAX_FUTURE_S, MAX_PAST_S, DEDUPE_S, KEEP_DAYS = 120, 48 * 3600, 600, 30


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def apply_context_event(norm):
    """Store one validated phone observation. norm = dict(type, place, observed_ts, event_id|None).
    Observation only: this function never touches threads, tasks, deadlines, reminders, focus or ui_actions."""
    now = _time.time()
    ots = norm["observed_ts"]
    if ots > now + MAX_FUTURE_S:
        raise ApiError(422, "validation_error", "observed time is in the future")
    if ots < now - MAX_PAST_S:
        raise ApiError(422, "validation_error", "observed time is too old (> 48h)")
    key = ("id:" + norm["event_id"]) if norm.get("event_id") else "k:%s|%s|%s" % (norm["type"], norm["place"], _utc(ots))
    with _write_lock:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM context_events WHERE event_key=?", (key,)).fetchone() or \
               conn.execute("SELECT 1 FROM context_events WHERE type=? AND place=? AND ABS(observed_ts-?)<?", (norm["type"], norm["place"], ots, DEDUPE_S)).fetchone():
                conn.execute("ROLLBACK")
                return {"ok": True, "stored": False, "duplicate": True}
            latest = conn.execute("SELECT MAX(observed_ts) m FROM context_events").fetchone()["m"]
            conn.execute("INSERT INTO context_events(event_key,source,type,place,observed_ts,observed_at,received_at) VALUES(?,?,?,?,?,?,?)",
                         (key, "chronicle", norm["type"], norm["place"], ots, _utc(ots), now_iso()))
            conn.execute("DELETE FROM context_events WHERE observed_ts < ?", (now - KEEP_DAYS * 86400,))
            conn.execute("COMMIT")
            return {"ok": True, "stored": True, "duplicate": False, "superseded": latest is not None and ots < latest}
        except Exception:
            if conn.in_transaction: conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def _context_state(conn, now):
    rows = conn.execute("SELECT type,place,observed_ts,observed_at,received_at FROM context_events ORDER BY observed_ts DESC LIMIT 10").fetchall()
    def one(r):
        age = max(0, int(now - r["observed_ts"]))
        return {"source": "chronicle", "type": r["type"], "place": r["place"], "observed_at": r["observed_at"], "received_at": r["received_at"],
                "age_seconds": age, "fresh": age <= CONTEXT_TTL_MIN * 60}
    recent = [one(r) for r in rows if now - r["observed_ts"] <= 24 * 3600]
    return {"ttl_minutes": CONTEXT_TTL_MIN, "latest": one(rows[0]) if rows else None, "recent": recent}


def get_state(journal_limit=100):
    conn = connect()
    try:
        conn.execute("BEGIN")
        now = _time.time()
        last = conn.execute("SELECT value FROM meta WHERE key='last_update'").fetchone()
        last = last["value"] if last else None
        age = None
        level = "never"
        if last:
            age = max(0, int(now - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()))
            level = "fresh" if age <= STALE_FRESH_MIN * 60 else "aging" if age <= STALE_AGING_MIN * 60 else "stale"
        state = {
            "server_time": now_iso(),
            "muse": {"last_update": last, "age_seconds": age, "freshness": level,
                     "fresh_minutes": STALE_FRESH_MIN, "stale_minutes": STALE_AGING_MIN},
        }
        f = conn.execute("SELECT data,updated_at FROM focus").fetchone()
        state["focus"] = {**json.loads(f["data"]), "updated_at": f["updated_at"]} if f else None
        for sec in SECTIONS:
            state[sec] = [{"id": r["id"], **json.loads(r["data"]), "created_at": r["created_at"], "updated_at": r["updated_at"]}
                          for r in conn.execute("SELECT * FROM records WHERE section=? ORDER BY created_at, id", (sec,))]
        state["context"] = _context_state(conn, now)
        state["areas"] = [{"id": a, "label": l} for a, l in AREAS]
        state["human_actions_enabled"] = len(os.environ.get("MUSE_HUMAN_TOKEN", "")) >= 16
        seen = conn.execute("SELECT value FROM meta WHERE key='actions_seen'").fetchone()
        seen = int(seen["value"]) if seen else 0
        state["actions_seen"] = seen
        results = {r["action_id"]: r for r in conn.execute("SELECT * FROM action_results")}
        def _act(r):
            res = results.get(r["id"])
            d = {"id": r["id"], "ts": r["ts"], "type": r["type"], "section": r["section"], "target": r["target"],
                 "params": json.loads(r["params"] or "{}"), "sensitive": bool(r["sensitive"]), "seen": r["id"] <= seen,
                 "status": res["status"] if res else ("seen" if r["id"] <= seen else "accepted")}
            if res:
                d["result_at"] = res["ts"]
                if res["note"]: d["note"] = res["note"]
            return d
        state["actions"] = [_act(r) for r in conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 60")]
        state["unseen_actions"] = conn.execute("SELECT COUNT(*) c FROM actions WHERE id>?", (seen,)).fetchone()["c"]
        _istat = {"open": 0, "acknowledged": 1}
        state["inbox"].sort(key=lambda x: (_istat.get(x.get("status", "open"), 2), x.get("due") or "9999", x["created_at"]))
        for t in state["threads"]:
            if t.get("ui_actions"):
                for u in t["ui_actions"]:
                    u["expired"] = _ua_expired(u, now)
        state["threads"].sort(key=lambda x: (x.get("status") in ("done", "dropped"), x.get("area", ""), x.get("title", "").lower()))
        state["tz"] = getattr(LOCAL, "key", str(LOCAL))
        today = local_today(); state["today"] = today.isoformat()
        tomorrow = (today + timedelta(days=1)).isoformat()
        for qi in state["queue"]:
            pf = qi.get("planned_for")
            if pf and qi.get("status") not in ("done", "dropped"):
                qi["plan_state"] = "overdue" if pf < state["today"] else "today" if pf == state["today"] else "tomorrow" if pf == tomorrow else "future"
        q = state["queue"]
        q.sort(key=lambda x: (x.get("order") is None, x.get("order") or 0, _PRIO.get(x.get("priority"), 9)))
        for d in state["deadlines"]:
            ts = to_ts(d["due"], end_of_day=True)
            d["due_in_seconds"] = int(ts - now)
            d["when_ts"] = int(ts)
            d["overdue"] = ts < now and d.get("status", "open") not in ("done", "dropped")
        state["deadlines"].sort(key=lambda d: (d.get("status") in ("done", "dropped"), d["due_in_seconds"]))
        for s in state["schedule"]:
            s["when_ts"] = int(to_ts(s["start"]))
            s["start_in_seconds"] = int(to_ts(s["start"]) - now)
            end = to_ts(s["end"], True) if s.get("end") else to_ts(s["start"], True)
            s["past"] = end < now
        state["schedule"].sort(key=lambda s: (s["past"], to_ts(s["start"])))
        state["projects"].sort(key=lambda p: (_PSTAT.get(p.get("status"), 4), p["name"].lower()))
        for a in state["automations"]:
            a["when_ts"] = int(to_ts(a["next_run"])) if a.get("next_run") else None
            a["next_run_overdue"] = bool(a.get("next_run") and to_ts(a["next_run"]) < now and a.get("status") != "paused")
        state["automations"].sort(key=lambda a: (not a.get("next_run"), to_ts(a.get("next_run")) or 0, a["name"].lower()))
        state["journal"] = [dict(r) for r in conn.execute(
            "SELECT id,ts,kind,message,title,area,thread FROM journal ORDER BY id DESC LIMIT ?", (journal_limit,))]
        for j in state["journal"]:
            for k in ("title", "area", "thread"):
                if j[k] is None: del j[k]
        state["journal_total"] = conn.execute("SELECT COUNT(*) c FROM journal").fetchone()["c"]
        conn.execute("COMMIT")
        return state
    finally:
        conn.close()


def check_db():
    conn = connect()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
