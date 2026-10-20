import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from . import db
from .schema import (ActionRequest, CHRONICLE_KNOWN_IGNORED, CHRONICLE_NATIVE, ContextCanonical, ContextNative, UpdateRequest)

log = logging.getLogger("muse")
STATIC = Path(__file__).parent / "static"
MAX_BODY = 512 * 1024
VERSION = "2.4.0"

app = FastAPI(title="Muse Control", docs_url=None, redoc_url=None, openapi_url=None)
PROTECTED = Path(__file__).parent / "protected"   # baked into the image: Muse's frontend sandbox cannot edit it
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/_ui", StaticFiles(directory=PROTECTED), name="protected_ui")


@app.on_event("startup")
def _startup():
    db.init_db()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith(("/static/", "/_ui/")):
        resp.headers["Cache-Control"] = "no-cache"     # always revalidate (ETag) so frontend deploys show up immediately
    return resp


def err(status, code, message, details=None, headers=None):
    body = {"ok": False, "error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


@app.exception_handler(db.ApiError)
async def _api_error(request, exc):
    return err(exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(404)
async def _nf(request, exc):
    return err(404, "not_found", "no such route")


@app.exception_handler(405)
async def _mna(request, exc):
    return err(405, "method_not_allowed", "method not allowed (dashboard is read-only)")


@app.exception_handler(Exception)
async def _boom(request, exc):
    log.exception("unhandled error")
    return err(500, "internal_error", "internal server error")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    try:
        db.check_db()
    except Exception:
        return err(503, "db_unavailable", "database check failed")
    return {"status": "ok", "db": "ok", "version": VERSION, "time": db.now_iso()}


@app.get("/api/state")
def state(journal: int = 100):
    return db.get_state(max(1, min(journal, 1000)))


def _check_auth(request: Request):
    token = os.environ.get("MUSE_TOKEN", "")
    if len(token) < 16:
        return err(503, "write_disabled", "write API disabled: MUSE_TOKEN not configured (min 16 chars)")
    h = request.headers.get("authorization", "")
    given = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(given.encode(), token.encode()):
        return err(401, "unauthorized", "missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})
    return None


@app.post("/api/update")
async def update(request: Request):
    bad = _check_auth(request)
    if bad:
        return bad
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return err(413, "payload_too_large", f"max {MAX_BODY} bytes")
    try:
        payload = json.loads(raw)
    except ValueError:
        return err(400, "invalid_json", "body is not valid JSON")
    if not isinstance(payload, dict):
        return err(422, "validation_error", "body must be a JSON object")
    hdr_key = request.headers.get("idempotency-key")
    if hdr_key:
        payload.setdefault("idempotency_key", hdr_key)
    try:
        req = UpdateRequest.model_validate(payload)
    except ValidationError as e:
        details = [{"path": ".".join(str(p) for p in x["loc"]), "msg": x["msg"]} for x in e.errors()]
        return err(422, "validation_error", "update rejected; nothing was applied", details[:50])
    if not (req.model_fields_set - {"idempotency_key"}):
        return err(422, "validation_error",
                   "empty update: send at least one of focus, queue, deadlines, projects, schedule, automations, journal, heartbeat")
    res = await run_in_threadpool(db.apply_update, req, payload, req.idempotency_key)
    return res


def _check_human(request: Request):
    token = os.environ.get("MUSE_HUMAN_TOKEN", "")
    if len(token) < 16:
        return err(503, "actions_disabled", "human actions disabled: MUSE_HUMAN_TOKEN not configured (min 16 chars)")
    h = request.headers.get("authorization", "")
    given = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(given.encode(), token.encode()):
        return err(401, "unauthorized", "missing or invalid human token", headers={"WWW-Authenticate": "Bearer"})
    return None


@app.post("/api/action")
async def action(request: Request):
    """Human click endpoint (separate token from Muse's /api/update). Narrow, whitelisted state transitions only."""
    bad = _check_human(request)
    if bad:
        return bad
    raw = await request.body()
    if len(raw) > 8 * 1024:
        return err(413, "payload_too_large", "max 8192 bytes")
    try:
        payload = json.loads(raw)
        req = ActionRequest.model_validate(payload)
    except ValidationError as e:
        return err(422, "validation_error", "action rejected", [{"path": ".".join(str(p) for p in x["loc"]), "msg": x["msg"]} for x in e.errors()][:20])
    except ValueError:
        return err(400, "invalid_json", "body is not valid JSON")
    return await run_in_threadpool(db.apply_action, req)


def _check_chronicle(request: Request):
    token = os.environ.get("CHRONICLE_TOKEN", "")
    if len(token) < 16:
        return err(503, "context_disabled", "context events disabled: CHRONICLE_TOKEN not configured (min 16 chars)")
    h = request.headers.get("authorization", "")
    given = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(given.encode(), token.encode()):
        return err(401, "unauthorized", "missing or invalid chronicle token", headers={"WWW-Authenticate": "Bearer"})
    return None


def _parse_when(s):
    from datetime import datetime
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp must be ISO 8601 with a UTC offset")
    if d.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return d.timestamp()


@app.post("/api/context/events")
async def context_event(request: Request):
    """Phone geofence events (observations only). Own token; whitelisted vocabulary; deduped; ordered by observed time."""
    bad = _check_chronicle(request)
    if bad:
        return bad
    raw = await request.body()
    if len(raw) > 2048:
        return err(413, "payload_too_large", "max 2048 bytes")
    try:
        payload = json.loads(raw)
    except ValueError:
        return err(400, "invalid_json", "body is not valid JSON")
    if not isinstance(payload, dict):
        return err(422, "validation_error", "body must be a JSON object")
    try:
        if "event" in payload:
            m = ContextNative.model_validate(payload)
            if m.event in CHRONICLE_KNOWN_IGNORED:
                return {"ok": True, "stored": False, "ignored": True, "reason": "event not used in v1"}
            if m.event not in CHRONICLE_NATIVE:
                return err(422, "validation_error", "unknown event '%s'" % m.event)
            typ, place = CHRONICLE_NATIVE[m.event]
            when, eid = m.ts, m.event_id
        else:
            m = ContextCanonical.model_validate(payload)
            typ, place, when, eid = m.type, m.place, m.observed_at, m.event_id
        ots = _parse_when(when)
    except ValidationError as e:
        return err(422, "validation_error", "event rejected", [{"path": ".".join(str(p) for p in x["loc"]), "msg": x["msg"]} for x in e.errors()][:10])
    except ValueError as e:
        return err(422, "validation_error", str(e))
    return await run_in_threadpool(db.apply_context_event, {"type": typ, "place": place, "observed_ts": ots, "event_id": eid})
