"""Tiny Muse Control client (stdlib only).

    from museboard import update
    update(focus={"title": "X", "reason": "Y"}, journal="did a thing")

Config: env MUSE_URL (default http://127.0.0.1:5050), MUSE_TOKEN; or JSON file
~/.museboard {"url": "...", "token": "..."}.
CLI: python museboard.py payload.json [-]   ('-' reads stdin)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

__all__ = ["update", "MuseError"]
SECTIONS = ("focus", "threads", "inbox", "queue", "deadlines", "projects", "schedule", "automations", "journal", "heartbeat", "actions_seen", "action_results")


class MuseError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status, self.body = status, body


def _config():
    url, token = os.environ.get("MUSE_URL"), os.environ.get("MUSE_TOKEN")
    try:
        with open(os.path.expanduser("~/.museboard")) as f:
            cfg = json.load(f)
        url, token = url or cfg.get("url"), token or cfg.get("token")
    except (OSError, ValueError):
        pass
    if not token:
        raise MuseError("no token: set MUSE_TOKEN or ~/.museboard")
    return (url or "http://127.0.0.1:5050").rstrip("/"), token


def update(*, idempotency_key=None, timeout=10, retries=4, **sections):
    """POST an update. Retries network/5xx/429 errors with the SAME idempotency key (safe)."""
    unknown = set(sections) - set(SECTIONS)
    if unknown:
        raise MuseError("unknown section(s): %s (valid: %s)" % (", ".join(sorted(unknown)), ", ".join(SECTIONS)))
    url, token = _config()
    key = idempotency_key or uuid.uuid4().hex
    data = json.dumps(sections, ensure_ascii=False).encode()
    req = urllib.request.Request(url + "/api/update", data=data, method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json", "Idempotency-Key": key})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = body
            if e.code in (429, 500, 502, 503, 504) and e.code != 503 or (e.code == 503 and "write_disabled" not in body):
                last = MuseError("HTTP %s: %s" % (e.code, body[:500]), e.code, parsed)
            else:
                raise MuseError("HTTP %s: %s" % (e.code, body[:2000]), e.code, parsed) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = MuseError("network error: %s" % e)
        if attempt < retries:
            time.sleep(min(0.5 * 2 ** attempt, 8))
    raise last


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    try:
        raw = sys.stdin.read() if argv[0] == "-" else open(argv[0], encoding="utf-8").read()
        payload = json.loads(raw)
        key = payload.pop("idempotency_key", None)
        print(json.dumps(update(idempotency_key=key, **payload), ensure_ascii=False))
        return 0
    except (MuseError, OSError, ValueError, TypeError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
