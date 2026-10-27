"""Paper collection helpers for Muse's daily routine (stdlib only; public metadata only).

    from papers import collect, prefilter, to_update
    cands = collect()                                  # arXiv + Hugging Face daily papers, de-duplicated, sources merged
    short = prefilter(cands, keywords, limit=40)       # cheap keyword/popularity shortlist; Muse (with its Memory) picks <= 5
    update(papers=to_update(chosen, rec_date))         # chosen items carry reason, quote, projects, rank

Scholar alert e-mails: Muse extracts only title / authors / link of each paper from the alert (never the e-mail text) and appends
{"title":..., "sources":[{"name":"scholar","url":...}]} items to the candidate list before choosing; the server de-duplicates by arXiv id, DOI and title.
Be polite: one arXiv API request and one Hugging Face request per day are enough.
"""
import hashlib
import json
import math
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UA = "muse-control-papers/1 (personal reading list)"
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_INVISIBLE = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_INSTRUCTION_LIKE = [re.compile(x, re.I) for x in (
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|messages?)", r"disregard\s+(the\s+)?(previous|above|system|prior)",
    r"\b(system|developer)\s+(prompt|message)\s*:", r"\byou\s+are\s+(now\s+)?(an?\s+)?(ai|assistant|language\s+model|chatgpt|claude)\b", r"<\s*/?\s*(system|instructions?)\s*>",
    r"do\s+not\s+(tell|inform|alert)\s+the\s+user", r"\b(send|email|upload|exfiltrate|post)\b.{0,50}\b(password|credentials?|api[- ]?key|tokens?|files?|secrets?)\b")]


def clean_text(t):
    """Fetched text is untrusted DATA: drop hidden characters, collapse whitespace."""
    return " ".join(_INVISIBLE.sub("", t or "").split())


def injection_flags(c):
    """Names of instruction-like patterns found in a candidate's text. Flag only: security papers may quote such phrases; Muse must treat all fetched text as data either way."""
    blob = " ".join([c.get("title") or "", c.get("abstract") or ""] + list(c.get("authors") or []))
    return ["instruction-like text"] if any(p.search(blob) for p in _INSTRUCTION_LIKE) else []


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", t or "").lower()).strip()


def norm_arxiv(a):
    a = re.sub(r"(?i)^(arxiv:|https?://arxiv\.org/(abs|pdf)/)", "", (a or "").strip())
    return re.sub(r"(\.pdf)?(v\d+)?$", "", a).lower()


def norm_doi(d):
    return re.sub(r"(?i)^(doi:|https?://(dx\.)?doi\.org/)", "", (d or "").strip()).lower()


def keys(c):
    k = []
    if c.get("arxiv_id"): k.append("arxiv:" + norm_arxiv(c["arxiv_id"]))
    if c.get("doi"): k.append("doi:" + norm_doi(c["doi"]))
    t = norm_title(c.get("title"))
    if len(t) >= 20: k.append("t:" + hashlib.sha1(t.encode()).hexdigest()[:16])
    return k


def _day(s):
    return (s or "")[:10] or None


def fetch_arxiv(categories=("cs.LG", "cs.CL", "cs.AI"), max_results=100, timeout=30):
    q = " OR ".join("cat:" + c for c in categories)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": max_results})
    return _parse_atom(_get(url, timeout))


def _parse_atom(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall("a:entry", ATOM):
        full = (e.findtext("a:id", "", ATOM) or "")
        aid = norm_arxiv(full.rsplit("/abs/", 1)[-1])
        if not aid: continue
        out.append({"arxiv_id": aid, "title": clean_text(e.findtext("a:title", "", ATOM)),
                    "authors": [clean_text(a.findtext("a:name", "", ATOM)) for a in e.findall("a:author", ATOM)][:40],
                    "abstract": clean_text(e.findtext("a:summary", "", ATOM))[:3000],
                    "published": _day(e.findtext("a:published", "", ATOM)), "updated": _day(e.findtext("a:updated", "", ATOM)),
                    "sources": [{"name": "arxiv", "url": "https://arxiv.org/abs/" + aid}], "signals": {}})
    return out


def fetch_arxiv_ids(ids, timeout=30):
    """arXiv metadata for specific ids (e.g. papers that only appeared on Hugging Face): one request for up to ~100 ids."""
    ids = [norm_arxiv(i) for i in ids if i]
    if not ids: return []
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    return _parse_atom(_get(url, timeout))


def fetch_hf_daily(date=None, timeout=30):
    """Hugging Face daily papers (public JSON). Only names, title, abstract and the arXiv id are kept; avatars/user data are dropped."""
    url = "https://huggingface.co/api/daily_papers" + ("?date=" + date if date else "")
    out = []
    for it in json.loads(_get(url, timeout)):
        p = it.get("paper") or {}
        aid = norm_arxiv(p.get("id"))
        if not aid: continue
        out.append({"arxiv_id": aid, "title": clean_text(p.get("title") or it.get("title")),
                    "authors": [clean_text(a.get("name")) for a in (p.get("authors") or []) if a.get("name")][:40],
                    "abstract": clean_text(p.get("summary") or it.get("summary"))[:3000],
                    "published": _day(p.get("publishedAt")), "updated": None,
                    "sources": [{"name": "huggingface", "url": "https://huggingface.co/papers/" + aid}],
                    "signals": {"hf_upvotes": p.get("upvotes") or 0}})
    return out


def _get_json(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": UA}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2_000_000))


def fetch_alphaxiv(base_url, muse_token, feeds=("hot", "for_you"), limit=30, timeout=30, stats=None):
    """Candidate ids from alphaXiv via the dashboard's read-only wrapper (the alphaXiv key never leaves the server), then public arXiv metadata for the ids that
    really are arXiv ids. Hot = new-paper candidates, For You = supplementary discovery (no age filter: Muse decides). Ids that do not look like arXiv ids are NOT
    guessed at: they are skipped and counted in `stats` (a dict the caller may pass). Returns (candidates, error codes)."""
    stats = stats if stats is not None else {}
    ids, errs = {}, []
    for f in feeds:
        try:
            d = _get_json("%s/api/papers/source/alphaxiv?feed=%s&limit=%d" % (base_url.rstrip("/"), f, limit), {"Authorization": "Bearer " + muse_token}, timeout)
        except Exception as e:
            errs.append("alphaxiv(%s): %s" % (f, type(e).__name__)); continue
        if not d.get("ok"):
            errs.append("alphaxiv(%s): %s" % (f, d.get("error"))); continue
        for i in d.get("ids") or []:
            st = stats.setdefault(f, {"returned": 0, "mapped": 0, "unmapped_skipped": 0})
            st["returned"] += 1
            if i.get("arxiv_id"): ids.setdefault(i["arxiv_id"], f); st["mapped"] += 1
            else: st["unmapped_skipped"] += 1
    out = []
    if ids:
        try:
            for c in fetch_arxiv_ids(list(ids), timeout):
                c["sources"] = c["sources"] + [{"name": "alphaxiv", "url": "https://www.alphaxiv.org/abs/" + c["arxiv_id"]}]
                c["signals"] = dict(c.get("signals") or {}, alphaxiv_feed=ids.get(c["arxiv_id"]))
                out.append(c)
        except Exception as e:
            errs.append("arxiv-metadata: %s" % type(e).__name__)
    return out, errs


def merge(*lists):
    """Union-find over arXiv id / DOI / normalised title; one candidate per paper, every source kept."""
    items = [dict(c) for L in lists for c in L]
    parent = list(range(len(items)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    seen = {}
    for i, c in enumerate(items):
        for k in keys(c):
            if k in seen: parent[find(i)] = find(seen[k])
            else: seen[k] = i
    groups = {}
    for i in range(len(items)): groups.setdefault(find(i), []).append(items[i])
    out = []
    for g in groups.values():
        arx = next((x for x in g if x.get("abstract") and any(s.get("name") == "arxiv" for s in x.get("sources") or [])), None)      # arXiv's own record is authoritative for dates/authors/abstract
        base = dict(arx or next((x for x in g if x.get("abstract")), g[0]))
        srcs, sig = [], {}
        for x in g:
            for s in x.get("sources") or []:
                if s["url"] not in {y["url"] for y in srcs}: srcs.append(s)
            sig.update(x.get("signals") or {})
            for f in ("arxiv_id", "doi", "published", "updated", "title", "abstract"):
                if not base.get(f) and x.get(f): base[f] = x[f]
            if len(x.get("authors") or []) > len(base.get("authors") or []): base["authors"] = x["authors"]
        base["sources"], base["signals"] = srcs[:8], sig
        base["flags"] = injection_flags(base)
        out.append(base)
    return out


def known_papers(base_url, max_days=120, timeout=30):
    """Identity keys of every paper ever recommended (any rating, including 'not relevant'), read from the dashboard's public /api/papers, so a paper is never
    recommended twice on different days."""
    known, before, got = set(), None, 0
    while got < max_days:
        d = _get_json("%s/api/papers?days=30%s" % (base_url.rstrip("/"), "&before=" + before if before else ""), {}, timeout)
        for day in d.get("days") or []:
            got += 1
            for p in day.get("papers") or []: known.update(keys(p))
        before = d.get("next_before")
        if not before: break
    return known


def drop_known(cands, known):
    """Remove candidates that match an already recommended paper (arXiv id, DOI or normalised title). Returns (fresh, dropped_count)."""
    fresh = [c for c in cands if not (set(keys(c)) & known)]
    return fresh, len(cands) - len(fresh)


_ARXIV_IN_TEXT = re.compile(r"(?i)(?:arxiv(?:\.org)?[/:]\s*(?:abs|pdf)?/?\s*|\b)((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?")


def extract_arxiv_id(*texts):
    """An arXiv id hidden in a link or title (e.g. a Scholar redirect URL that embeds the arXiv address). None when there is none."""
    for t in texts:
        m = _ARXIV_IN_TEXT.search(urllib.parse.unquote(t or ""))
        if m: return m.group(1).lower()
    return None


def scholar_candidates(items):
    """Scholar alert items {title, authors, urls[]} -> candidates (title/authors/link only, never e-mail text). Those that carry an arXiv id get a real abstract in enrich()."""
    out = []
    for it in items or []:
        urls = [u for u in (it.get("urls") or []) if isinstance(u, str) and u.startswith("https://")]
        aid = extract_arxiv_id(*urls, it.get("title"))
        out.append({"arxiv_id": aid, "title": clean_text(it.get("title")), "authors": [clean_text(a) for a in it.get("authors") or []][:40], "abstract": "", "published": None, "updated": None,
                    "sources": [{"name": "scholar", "url": u} for u in urls[:1]] + ([{"name": "arxiv", "url": "https://arxiv.org/abs/" + aid}] if aid else []), "signals": {}})
    return out


def enrich(cands, timeout=30):
    """Candidates that have an arXiv id but did not come from arXiv itself (Hugging Face only, Scholar only) get arXiv's metadata: the real first-submission date, abstract and an arXiv link."""
    need = [c["arxiv_id"] for c in cands if c.get("arxiv_id") and not (c.get("abstract") and any(s.get("name") == "arxiv" for s in c.get("sources") or []) and c.get("published"))]
    if not need: return cands, []
    try:
        meta = fetch_arxiv_ids(need[:100], timeout)
    except Exception as e:
        return cands, ["arxiv-enrich: %s" % type(e).__name__]
    return merge(cands, meta), []


def collect(categories=("cs.LG", "cs.CL", "cs.AI"), max_results=100, hf_date=None, alphaxiv=None, stats=None, scholar=None):
    """One call, both public sources. A failing source is skipped (and named in the second return value), never fatal."""
    lists, errs = [], []
    for name, fn in (("arxiv", lambda: fetch_arxiv(categories, max_results)), ("huggingface", lambda: fetch_hf_daily(hf_date))):
        try: lists.append(fn())
        except Exception as e: errs.append("%s: %s" % (name, type(e).__name__))
    if alphaxiv:                       # (dashboard_base_url, muse_token): optional extra candidate source; any failure just means fewer candidates
        try:
            c, e = fetch_alphaxiv(*alphaxiv, stats=stats); lists.append(c); errs += e
        except Exception as ex: errs.append("alphaxiv: %s" % type(ex).__name__)
    if scholar: lists.append(scholar_candidates(scholar))
    merged, e2 = enrich(merge(*lists))
    return merged, errs + e2


def prefilter(cands, keywords, limit=40):
    """Cheap shortlist so Muse reads 40 abstracts instead of 150: keyword hits (title x3, abstract x1) + log(upvotes), multi-source bonus.
    `keywords` come from Muse's own research profile; nothing here is stored on the server."""
    kw = [k.lower() for k in keywords if k]
    scored = []
    for c in cands:
        t, a = (c.get("title") or "").lower(), (c.get("abstract") or "").lower()
        hit = [k for k in kw if k in t or k in a]
        score = sum(3 for k in kw if k in t) + sum(1 for k in kw if k in a) + math.log1p((c.get("signals") or {}).get("hf_upvotes", 0)) + 0.5 * (len(c.get("sources") or []) - 1)
        if hit: scored.append((score, dict(c, matched_keywords=hit)))
    return [c for _, c in sorted(scored, key=lambda x: -x[0])[:limit]]


def to_update(chosen, rec_date):
    """Build the `papers` list for `update(...)`. `chosen` items need reason (and ideally quote, projects, rank); at most 5 are accepted by the server."""
    out = []
    for i, c in enumerate(chosen[:5], 1):
        d = {"id": "p%d" % i, "kind": "paper", "rec_date": rec_date, "rank": c.get("rank", i)}
        for f in ("title", "authors", "abstract", "arxiv_id", "doi", "published", "updated", "sources", "projects", "reason", "quote"):       # signals / flags / matched_keywords never leave the machine
            if c.get(f): d[f] = c[f]
        out.append(d)
    return out


def day_marker(rec_date, candidates, note):
    """When nothing is worth reading, say so (the page shows it) instead of filling the day."""
    return {"id": "day-" + rec_date, "kind": "day", "rec_date": rec_date, "title": "No recommendation", "candidates": candidates, "note": note}


if __name__ == "__main__":      # python papers.py  ->  candidates as JSON on stdout, problems on stderr
    c, errs = collect()
    for e in errs: print("source failed:", e, file=sys.stderr)
    json.dump(c, sys.stdout, ensure_ascii=False)
