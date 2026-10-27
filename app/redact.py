"""High-precision secret redaction for what the dashboard accepts and returns.

Two uses, one rule set:
* write path: `/api/update` text is redacted before it is stored (the caller gets a warning, never the value);
* read path: `/api/state` redacts every string it returns, so a secret that was stored earlier (the journal is append-only) is no longer served.
Pattern matching cannot find every secret; the patterns are deliberately narrow so ordinary notes are left alone.
"""
import re

RED = "[redacted]"
_VAL = r"(?P<v>(?=[A-Za-z0-9!@#$%^&*._-]*\d)[A-Za-z0-9!@#$%^&*._-]{4,})"          # a value that contains a digit
PATTERNS = [
    ("bearer token", re.compile(r"(?i)\bbearer\s+(?P<v>[A-Za-z0-9._~+/=-]{16,})")),
    ("api key", re.compile(r"\b(?P<v>sk-[A-Za-z0-9_-]{16,})")),
    ("access token", re.compile(r"\b(?P<v>gh[pousr]_[A-Za-z0-9]{20,})")),
    ("cloud key", re.compile(r"\b(?P<v>AKIA[0-9A-Z]{16})\b")),
    ("jwt", re.compile(r"\b(?P<v>eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*)")),
    ("private key", re.compile(r"(?P<v>-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$))")),
    ("password/secret assignment", re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|api[_ -]?key|secret|token)\b\s*(?:is|:|=)\s*(?P<v>(?=\S*[\d!@#$%^&*])\S{4,})")),
    ("password (zh)", re.compile(r"(?:\u5bc6\u7801|\u53e3\u4ee4|\u52a8\u6001\u7801)\s*(?:\u662f|\u4e3a|[:\uff1a=])?\s*" + _VAL)),
    ("verification code", re.compile(r"(?i)\b(?:verification|security|login|one[- ]time|auth(?:entication)?)\s+code\b\s*(?:is|:)?\s*(?P<v>\d{4,8})\b")),
    ("verification code (zh)", re.compile(r"\u9a8c\u8bc1\u7801\s*(?:\u662f|\u4e3a|[:\uff1a=])?\s*(?P<v>\d{4,8})")),
    ("otp", re.compile(r"(?i)\botp\b\D{0,6}(?P<v>\d{4,8})\b")),
    ("recovery code", re.compile(r"(?i)\b(?:recovery|backup)\s+codes?\b\s*(?:is|:|=)\s*(?P<v>[A-Za-z0-9-]{6,})")),
]
SKIP_KEYS = {"id", "idempotency_key", "event_id", "thread", "target", "project", "task", "merged_into", "request_id", "section", "type", "status", "kind", "intent", "ref",
             "abstract", "quote", "authors", "sources"}      # public paper metadata: "token = 4096" in an abstract is not a secret


def redact_text(s):
    """Return (redacted string, [pattern names that matched])."""
    hits = []
    for name, p in PATTERNS:
        def sub(m, _n=name):
            hits.append(_n)
            a, b = m.span("v"); base = m.start()
            return m.group(0)[:a - base] + RED + m.group(0)[b - base:]
        s = p.sub(sub, s)
    return s, hits


def redact_obj(o, path="", warnings=None, _key=""):
    """Deep copy with every string redacted. `warnings` collects {path, found} (never the value)."""
    if isinstance(o, dict):
        return {k: (v if k in SKIP_KEYS else redact_obj(v, "%s.%s" % (path, k) if path else str(k), warnings, k)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact_obj(v, "%s.%d" % (path, i), warnings, _key) for i, v in enumerate(o)]
    if isinstance(o, str):
        s, hits = redact_text(o)
        if hits and warnings is not None:
            warnings.append({"path": path, "found": sorted(set(hits))})
        return s
    return o
