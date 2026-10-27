"""Read-only alphaXiv MCP wrapper: one more *candidate source* for Papers, nothing else.

Design rules (enforced here in code, not in a prompt):
* DEFAULT DENY. Only the tools in ALLOWED can ever be called, with arguments validated to the exact documented shape. The server's own tool list is never
  consulted, so write/destructive tools (follow/save/move/delete/edit...) are unreachable even if a caller names them: the check happens before any network I/O.
* The API key stays on the server (env ALPHAXIV_API_KEY, or a file named by ALPHAXIV_API_KEY_FILE). It is sent only as an Authorization header to
  api.alphaxiv.org over https, never logged, never returned, never put in an error message. Redirects are refused. Responses are size-capped.
* Muse only ever receives paper ids (and whether they map to an arXiv id), never the MCP session, the key, or arbitrary tool access.
* Any failure becomes a short error code; the Papers workflow then simply uses arXiv / Hugging Face / Scholar.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE = "https://api.alphaxiv.org/mcp/v1"
MAX_BYTES = 1_000_000
TIMEOUT = 15
# tool -> {argument: (min, max, default)}; extending this table is a deliberate, reviewed code change
ALLOWED = {
    "get_for_you_feed": {"limit": (1, 200, 30)},      # free of assistant quota (per the docs)
    "get_hot_feed": {"limit": (1, 200, 30)},          # free of assistant quota (per the docs)
}
FEEDS = {"for_you": "get_for_you_feed", "hot": "get_hot_feed"}
_ARXIV_NEW = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_ARXIV_OLD = re.compile(r"^[a-z-]+(\.[A-Za-z]{2})?/\d{7}(v\d+)?$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._/-]{3,64}$")
_RATE_HEADERS = ("ratelimit", "x-ratelimit", "retry-after", "x-quota", "x-credit", "x-alphaxiv")


class AlphaXivError(Exception):
    """`code` is one of: not_configured, denied, bad_args, auth, quota, timeout, network, protocol, too_large, guard."""
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code, self.detail = code, detail


def api_key():
    k = os.environ.get("ALPHAXIV_API_KEY", "").strip()
    if not k and os.environ.get("ALPHAXIV_API_KEY_FILE"):
        try:
            k = open(os.environ["ALPHAXIV_API_KEY_FILE"]).read().strip()
        except OSError:
            k = ""
    return k


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def validate_call(name, args):
    """Default deny + exact argument validation. Pure function: raises before anything leaves the machine."""
    spec = ALLOWED.get(name)
    if spec is None:
        raise AlphaXivError("denied", "tool not allowed")
    args = dict(args or {})
    unknown = set(args) - set(spec)
    if unknown:
        raise AlphaXivError("bad_args", "unexpected argument")
    out = {}
    for k, (lo, hi, dflt) in spec.items():
        v = args.get(k, dflt)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise AlphaXivError("bad_args", "%s must be an integer in %d..%d" % (k, lo, hi))
        out[k] = v
    return out


class Client:
    def __init__(self, key=None, base=DEFAULT_BASE):
        self._key = key if key is not None else api_key()
        if base != DEFAULT_BASE and not re.match(r"^http://127\.0\.0\.1:\d+/", base):          # a different base is allowed only for local tests
            raise AlphaXivError("denied", "base url")
        self._base = base
        self._opener = urllib.request.build_opener(_NoRedirect)
        self.headers_seen = {}

    def _post(self, body, session=None):
        req = urllib.request.Request(self._base, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer " + self._key, "User-Agent": "muse-control-papers/1"})
        if session: req.add_header("Mcp-Session-Id", session)
        try:
            r = self._opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            self._note(e.headers)
            if e.code in (401, 403): raise AlphaXivError("auth")
            if e.code == 429: raise AlphaXivError("quota", e.headers.get("Retry-After", ""))
            raise AlphaXivError("network", "http %d" % e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AlphaXivError("timeout" if "timed out" in str(e) else "network")
        self._note(r.headers)
        data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES: raise AlphaXivError("too_large")
        return r.headers, data

    def _note(self, headers):
        for k, v in headers.items():
            if k.lower().startswith(_RATE_HEADERS): self.headers_seen[k.lower()] = v[:80]

    @staticmethod
    def _message(headers, data, rid):
        text = data.decode("utf-8", "replace")
        if "text/event-stream" in (headers.get("Content-Type") or ""):
            msgs = []
            for block in text.split("\n\n"):
                payload = "\n".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
                if payload:
                    try: msgs.append(json.loads(payload))
                    except ValueError: pass
        else:
            try: msgs = [json.loads(text)]
            except ValueError: raise AlphaXivError("protocol", "not json")
            if isinstance(msgs[0], list): msgs = msgs[0]
        for m in msgs:
            if isinstance(m, dict) and m.get("id") == rid: return m
        raise AlphaXivError("protocol", "no reply")

    def call(self, name, args=None):
        args = validate_call(name, args)                      # default deny happens BEFORE any network use
        if not self._key: raise AlphaXivError("not_configured")
        h, d = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "muse-control-papers", "version": "1"}}})
        init = self._message(h, d, 1)
        if "error" in init: raise AlphaXivError("protocol", "initialize rejected")
        sid = h.get("Mcp-Session-Id") or h.get("mcp-session-id")
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
        except AlphaXivError:
            pass                                              # a notification has no reply that matters
        h2, d2 = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}, sid)
        m = self._message(h2, d2, 2)
        if "error" in m: raise AlphaXivError("protocol", "tool error")
        res = m.get("result") or {}
        if res.get("isError"): raise AlphaXivError("quota" if re.search(r"(?i)quota|limit|credit", json.dumps(res)[:400]) else "protocol", "tool error")
        if isinstance(res.get("structuredContent"), dict): return res["structuredContent"]
        for part in res.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                try: return json.loads(part.get("text", ""))
                except ValueError: pass
        raise AlphaXivError("protocol", "unreadable result")


def classify_id(uid):
    """Never assume a universal id IS an arXiv id: only ids that look like arXiv ids are mapped; everything else stays unmapped (and out of de-duplication)."""
    uid = str(uid or "").strip()
    if not _SAFE_ID.match(uid): return None, None
    base = re.sub(r"v\d+$", "", uid)
    return uid, (base.lower() if (_ARXIV_NEW.match(uid) or _ARXIV_OLD.match(uid)) else None)


def normalise_feed(result, limit):
    items = result.get("papers") if isinstance(result, dict) else None
    if not isinstance(items, list): raise AlphaXivError("protocol", "no papers list")
    out, seen = [], set()
    for it in items[:limit]:
        uid, ax = classify_id(it.get("universal_paper_id") if isinstance(it, dict) else None)
        if uid and uid not in seen:
            seen.add(uid); out.append({"paper_id": uid, "arxiv_id": ax})
    return out


# ---- service layer used by the API route: cache + a hard daily cap on upstream calls (so a loop can never burn anything)
_lock = threading.Lock()
_cache, _calls = {}, {}
CACHE_TTL = 6 * 3600
DAILY_CAP = 4                                                # upstream calls per feed per UTC day


def feed(kind, limit=30, client=None, now=None):
    now = now if now is not None else time.time()
    tool = FEEDS.get(kind)
    if tool is None: raise AlphaXivError("bad_args", "feed must be hot or for_you")
    limit = validate_call(tool, {"limit": limit})["limit"]
    with _lock:
        hit = _cache.get((kind, limit))
        if hit and now - hit[0] < CACHE_TTL:
            return dict(hit[1], cached=True)
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        if _calls.get((kind, day), 0) >= DAILY_CAP:
            raise AlphaXivError("guard", "daily upstream call cap reached")
        _calls[(kind, day)] = _calls.get((kind, day), 0) + 1
    c = client or Client()
    ids = normalise_feed(c.call(tool, {"limit": limit}), limit)
    res = {"ok": True, "source": "alphaxiv", "feed": kind, "count": len(ids), "mapped": sum(1 for i in ids if i["arxiv_id"]), "ids": ids, "fetched_at": int(now)}
    with _lock:
        _cache[(kind, limit)] = (now, res)
    res = dict(res, cached=False)
    if getattr(c, "headers_seen", None): res["rate_headers"] = c.headers_seen
    return res


def safe_feed(kind, limit=30, client=None, now=None):
    """Never raises: a failure is just `{"ok": false, "error": code}` so the daily routine carries on with its other sources."""
    try:
        return feed(kind, limit, client, now)
    except AlphaXivError as e:
        return {"ok": False, "source": "alphaxiv", "feed": kind, "error": e.code}
    except Exception:
        return {"ok": False, "source": "alphaxiv", "feed": kind, "error": "internal"}


def reset_for_tests():
    with _lock:
        _cache.clear(); _calls.clear()
