"""Frontend validation (runs inside the sandboxed validator container).
validate: seeds a throwaway server and tests rendering, interaction and safety; smoke: GET-only check of a live server."""
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

VIEWS = {"home": ["focus", "needs-you", "upcoming", "in-progress", "matters"], "week": ["week-grid"], "timeline": ["timeline-full"], "log": ["log"], "system": ["routines", "system-status"]}
OPTIONAL = {"needs-you", "upcoming", "in-progress"}          # hidden (not empty placeholders) when they have nothing to show
ALL_SECTIONS = [s for v in VIEWS.values() for s in v]
VIEWPORTS = {"desktop": (1280, 800), "mobile": (390, 844)}
RAW_ENUM = re.compile(r"\b(TODO|UNKNOWN|OBSERVED|PLANNED|UNCONFIRMED|IN_PROGRESS)\b")
LIMITS = {"needs-you": ("inbox", 5), "in-progress": ("queue", 6)}
checks = []


def rec(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})


def call(base, path, method="GET", body=None, token=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, None


def wait_up(base):
    for _ in range(60):
        try:
            if call(base, "/api/health")[0] == 200: return True
        except Exception: pass
        time.sleep(0.5)
    return False


def iso(days=0, hours=0, minutes=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours, minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def dst_days(year):
    """Real US DST transitions for America/New_York, read from the tz database (never hard-coded)."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York"); out = []; d = datetime(year, 1, 1, 12, tzinfo=ny); prev = d.utcoffset()
    while d.year == year:
        d += timedelta(days=1)
        if d.utcoffset() != prev: out.append(d.date()); prev = d.utcoffset()
    return out


def next_dst(kind):
    """Next transition (kind 'start' = March, 'end' = November) that is at least 40 days away."""
    horizon = (datetime.now(timezone.utc) + timedelta(days=40)).date()
    for y in range(datetime.now().year, datetime.now().year + 4):
        for d in dst_days(y):
            if d > horizon and ((kind == "start" and d.month == 3) or (kind == "end" and d.month == 11)): return d.isoformat()
    raise RuntimeError("no DST transition found")


def fixture(today):
    th = [{"id": "fxid-th-%d" % i, "title": "Fixture thread %d" % i, "area": a, "template": t, "status": s}
          for i, (a, t, s) in enumerate([("research", "paper", "active"), ("course", "course", "active"), ("purchase", "purchase", "active"),
                                         ("health", "cycle", "active"), ("life", "event", "paused"), ("system", "generic", "active"), ("career", "application", "done"), ("career", "application", "done")], 1)]
    th[0].update(stages=["Write", "Submit", "Review"], stage="Write", summary="Thread one summary")
    th[2].update(stages=["Look", "Buy"], stage="Look", ui_actions=[
        {"id": "fx_book", "label": "Fixture book it", "intent": "schedule_fixture",
         "steps": [{"id": "slot", "prompt": "Fixture when?", "choices": [{"value": "am", "label": "Fixture morning"}, {"value": "pm", "label": "Fixture afternoon"}]}]},
        {"id": "fx_nudge", "label": "Fixture nudge me", "intent": "nudge_fixture"},
        {"id": "fx_danger", "label": "Fixture risky ask", "intent": "risky_fixture", "sensitive": True}])
    th[2]["next_step"] = "Fixture next step"
    th[0]["ui_actions"] = [{"id": "fx_old", "label": "Fixture expired action", "intent": "old_fixture", "expires_at": "2001-01-01"}]
    f = {"focus": {"title": '<img src=x onerror="window.__xss=1"> Fixture focus', "task": "fxid-q-rec", "project": "fxid-th-3", "reason": "Fixture reason <script>window.__xss=2</script>", "priority": "p1", "basis": "recommended"},
         "threads": th,
         "inbox": [{"id": "fxid-in-1", "title": "Fixture sensitive confirm", "kind": "confirm", "sensitive": True, "status": "open", "detail": "sensitive detail"},
                   {"id": "fxid-in-2", "title": "Fixture decision pick", "kind": "decision", "options": ["Option A", "Option B"], "status": "open"},
                   {"id": "fxid-in-3", "title": "Fixture alert ack", "kind": "alert", "status": "open"},
                   {"id": "fxid-in-4", "title": "Fixture todo ask", "kind": "todo", "status": "open"}],
         "queue": [{"id": "fxid-q-%d" % i, "title": "Fixture queue item %d" % i, "status": ["active", "active", "blocked", "waiting", "todo", "active", "done", "active", "active", "active"][(i - 1) % 10],
                    "order": i, "priority": "p1" if i < 3 else None, "thread": "fxid-th-%d" % (1 + i % 3),
                    "note": "Supporting note %d. Second sentence that is rather long to test wrapping on narrow screens." % i} for i in range(1, 13)],
         "deadlines": [{"id": "fxid-d-%d" % i, "title": "Fixture deadline %d" % i, "due": iso(days=i - 2), "status": "open", "thread": "fxid-th-2", "note": "d note"} for i in range(1, 7)]
                      + [{"id": "fxid-d-shared", "title": "Fixture shared deadline", "due": iso(days=3, hours=1), "thread": "fxid-th-3"}],
         "schedule": [{"id": "fxid-s-%d" % i, "title": "Fixture event %d" % i, "start": iso(days=i), "location": "Room %d" % i, "status": ["confirmed", "unconfirmed"][i % 2]} for i in range(1, 6)]
                     + [{"id": "fxid-s-shared", "title": "Fixture shared event", "start": iso(days=3, hours=1), "status": "unconfirmed", "thread": "fxid-th-3"}],
         "automations": [{"id": "fxid-a-%d" % i, "name": "Fixture routine %d" % i, "frequency": "daily", "kind": "daily", "next_run": iso(hours=i * 3), "status": "ok"} for i in range(1, 5)]
                        + [{"id": "fxid-a-w", "name": "Fixture weekly job", "kind": "weekly", "next_run": iso(days=2)}, {"id": "fxid-a-always", "name": "Fixture always job", "kind": "always"},
                           {"id": "fxid-a-once", "name": "Fixture once reminder", "kind": "once", "next_run": iso(days=3, hours=-4), "thread": "fxid-th-3"}],
         "journal": [{"message": "Fixture digest %d. Details follow here for the day." % i, "kind": "digest"} for i in range(1, 15)]
                    + [{"message": "Fixture decision text. Because reasons.", "kind": "decision"}, {"message": "Fixture alert text.", "kind": "alert"}, {"message": "Fixture plain note.", "kind": "note"}]}
    ty = (datetime.fromisoformat(today) - timedelta(days=1)).strftime("%Y-%m-%d")
    tm = (datetime.fromisoformat(today) + timedelta(days=1)).strftime("%Y-%m-%d")
    f["queue"] += [
        {"id": "fxid-q-p1", "title": "Fixture plan tonight todo", "status": "todo", "priority": "p3", "planned_for": today, "time_hint": "tonight"},
        {"id": "fxid-q-p2", "title": "Fixture plan morning active", "status": "active", "planned_for": today, "time_hint": "morning", "order": 1, "thread": "fxid-th-3"},
        {"id": "fxid-q-p3", "title": "Fixture plan tomorrow", "status": "todo", "planned_for": tm, "time_hint": "evening"},
        {"id": "fxid-q-p4", "title": "Fixture plan missed", "status": "todo", "planned_for": ty},
        {"id": "fxid-q-p5", "title": "Fixture plan done", "status": "done", "planned_for": today},
        {"id": "fxid-q-p6", "title": "Fixture high priority unplanned", "status": "todo", "priority": "p0"},
        {"id": "fxid-q-arch", "title": "Fixture archived open task", "status": "waiting", "thread": "fxid-th-7"},
        {"id": "fxid-q-arch2", "title": "Fixture archived active task", "status": "active", "thread": "fxid-th-8"},
        {"id": "fxid-q-rec", "title": "Fixture recommended work", "status": "active", "thread": "fxid-th-3", "order": 0, "priority": "p0"},
        {"id": "fxid-q-recdup", "title": "Fixture recommended work copy", "status": "active", "thread": "fxid-th-1", "order": 0, "priority": "p0"}]
    f["schedule"] += [
        {"id": "fxid-s-neg", "title": "Fixture neg event", "start": iso(minutes=20), "status": "confirmed", "thread": "fxid-th-3"},
        {"id": "fxid-s-tz1", "title": "Fixture tz offset EDT", "start": "2030-10-19T08:30:00-04:00", "status": "confirmed"},
        {"id": "fxid-s-tz2", "title": "Fixture tz naive EDT", "start": "2030-10-19T14:30", "status": "confirmed"},
        {"id": "fxid-s-tz3", "title": "Fixture tz offset EST", "start": "2030-11-04T10:00:00-05:00", "status": "confirmed"},
        {"id": "fxid-s-tz4", "title": "Fixture tz naive EST", "start": "2030-11-04T10:00", "status": "confirmed"},
        {"id": "fxid-s-dst1", "title": "Fixture dst end naive", "start": next_dst("end") + "T09:00", "status": "confirmed"},
        {"id": "fxid-s-dst2", "title": "Fixture dst end offset ok", "start": next_dst("end") + "T09:00:00-05:00", "status": "confirmed"},
        {"id": "fxid-s-dst3", "title": "Fixture dst end slip", "start": next_dst("end") + "T09:00:00-04:00", "status": "confirmed"},
        {"id": "fxid-s-dstlate", "title": "Fixture dst end late", "start": next_dst("end") + "T23:30", "status": "confirmed"},
        {"id": "fxid-s-dstutc", "title": "Fixture dst end utc", "start": next_dst("end") + "T03:30:00Z", "status": "confirmed"},
        {"id": "fxid-s-dst4", "title": "Fixture dst start naive", "start": next_dst("start") + "T10:00", "status": "confirmed"},
        {"id": "fxid-s-dst5", "title": "Fixture dst start offset ok", "start": next_dst("start") + "T10:00:00-04:00", "status": "confirmed"},
        {"id": "fxid-s-dst6", "title": "Fixture dst start slip", "start": next_dst("start") + "T10:00:00-05:00", "status": "confirmed"}]
    f["automations"] += [{"id": "fxid-a-solo", "name": "Fixture solo reminder", "kind": "once", "next_run": iso(minutes=40)}]
    f["deadlines"] += [{"id": "fxid-d-aoe", "title": "Fixture AoE deadline", "due": "2030-10-22T07:59:00-04:00"}]
    return f


def open_page(browser, vp, base, token=None, posts=None):
    w, h = VIEWPORTS[vp]
    ctx = browser.new_context(viewport={"width": w, "height": h}, service_workers="block")
    if token: ctx.add_init_script("try{localStorage.setItem('muse-human-token','%s')}catch(e){}" % token)
    pg = ctx.new_page()
    st = {"errors": [], "dialogs": [], "bad_requests": []}
    origin = re.match(r"https?://[^/]+", base).group(0)
    def route(r):
        req = r.request
        ok = req.url.startswith(origin) and (req.method == "GET" or (req.method == "POST" and req.url == origin + "/api/action"))
        if req.method == "POST" and req.url == origin + "/api/action" and posts is not None: posts.append(req.post_data)
        if not ok: st["bad_requests"].append("%s %s" % (req.method, req.url)); r.abort()
        else: r.continue_()
    pg.route("**/*", route)
    pg.on("console", lambda m: st["errors"].append("console.%s: %s" % (m.type, m.text)) if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: st["errors"].append("pageerror: %s" % e))
    pg.on("requestfailed", lambda r: st["errors"].append("requestfailed: %s" % r.url) if r.url.startswith(origin) else None)
    pg.on("dialog", lambda d: (st["dialogs"].append(d.message), d.dismiss()))
    pg.goto(base + "/", wait_until="load")
    pg.wait_for_timeout(1200)
    return ctx, pg, st


def go(pg, view):
    pg.evaluate("location.hash='#%s'" % view); pg.wait_for_timeout(350)


JS_STATS = """(secs) => {
  const out = {sections: {}};
  for (const id of secs) {
    const e = document.getElementById(id);
    out.sections[id] = e ? {text: e.innerText.trim(), visible: !!(e.offsetWidth && e.offsetHeight), top: e.getBoundingClientRect().top,
       heading: (e.querySelector('h1,h2,h3') || {}).innerText || ''} : null;
  }
  out.overflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
  const root = '#muse-actions-root';
  out.forbidden = [...document.querySelectorAll('form,input,textarea,select,iframe,object,embed,[contenteditable]')].filter(e => !e.closest(root)).length;
  out.badButtons = [...document.querySelectorAll('button')].filter(b => !b.closest(root) && !b.hasAttribute('data-act') && !b.hasAttribute('data-ui') && b.id !== 'theme-toggle').length;
  out.extlinks = [...document.querySelectorAll('a[href]')].filter(a => !a.href.startsWith(location.origin)).length;
  out.body = document.body.innerText;
  let min = 99; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const n = w.currentNode; if (!n.textContent.trim()) continue; const el = n.parentElement; if (el.closest('[hidden]')) continue;
    const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') continue; const fs = parseFloat(cs.fontSize); if (fs < min) min = fs; }
  out.minFont = min;
  out.xss = window.__xss || null; out.injected = document.querySelectorAll('img[src="x"], script:not([src])').length;
  out.hasActions = typeof window.MuseActions === 'object';
  out.tlRows = document.querySelectorAll('#upcoming .tl').length;
  return out; }"""


def assess(vp, pg, st, label, view, fx=None, outdir=None):
    secs = VIEWS[view]
    go(pg, view)
    s = pg.evaluate(JS_STATS, ALL_SECTIONS)
    tag = "%s/%s/%s" % (label, vp, view)
    missing = [i for i in secs if s["sections"][i] is None]
    rec("%s: sections present (#%s)" % (tag, ",#".join(secs)), not missing, "missing %s" % missing)
    blank = [i for i in secs if s["sections"][i] and not (i in OPTIONAL and not fx) and (not s["sections"][i]["visible"] or len(s["sections"][i]["text"]) < 2)]
    rec("%s: sections visible and non-blank" % tag, not blank, "blank/hidden %s" % blank)
    nohead = [i for i in secs if s["sections"][i] and not s["sections"][i]["heading"].strip()]
    rec("%s: sections have headings" % tag, not nohead, nohead)
    rec("%s: no JS/console errors, failed requests or dialogs" % tag, not (st["errors"] or st["dialogs"]), "; ".join(st["errors"] + st["dialogs"]))
    rec("%s: only same-origin GET (and /api/action POST) requests" % tag, not st["bad_requests"], "; ".join(st["bad_requests"]))
    rec("%s: no horizontal scrolling" % tag, not s["overflow"])
    rec("%s: no typing controls; every button is data-act/data-ui/theme toggle; no off-site links" % tag, s["forbidden"] == 0 and s["badButtons"] == 0 and s["extlinks"] == 0,
        "controls=%s buttons=%s extlinks=%s" % (s["forbidden"], s["badButtons"], s["extlinks"]))
    rec("%s: text >= 11px" % tag, s["minFont"] >= 11, "min %.1fpx" % s["minFont"])
    rec("%s: nothing injected/executed from data (XSS)" % tag, s["xss"] is None and s["injected"] == 0, "xss=%s injected=%s" % (s["xss"], s["injected"]))
    rec("%s: protected interaction layer loaded" % tag, s["hasActions"])
    pg.screenshot(path=os.path.join(outdir, "%s-%s-%s-fold.png" % (label, vp, view)))
    if view == "home": pg.screenshot(path=os.path.join(outdir, "%s-%s-%s-full.png" % (label, vp, view)), full_page=True)
    if view == "home" and not fx and label == "empty":
        rec("%s: sections with nothing to show are hidden, not filled with placeholders" % tag, all(not s["sections"][i]["visible"] for i in OPTIONAL) and "Nothing planned" not in s["body"], [i for i in OPTIONAL if s["sections"][i]["visible"]])
    if fx:
        body = s["body"]
        rec("%s: internal IDs not shown" % tag, "fxid-" not in body, "found fxid-")
        raw = RAW_ENUM.findall(body)
        rec("%s: no raw ENUM labels" % tag, not raw, raw[:5])
        if view == "home":
            sec = s["sections"]
            rec("%s: headings use the new names (MUSE RECOMMENDS, UPCOMING, IN PROGRESS, ALL MATTERS)" % tag,
                all(sec[k]["heading"].strip() == v for k, v in (("focus", "MUSE RECOMMENDS"), ("upcoming", "UPCOMING"), ("in-progress", "IN PROGRESS"), ("matters", "ALL MATTERS"))), {k: sec[k]["heading"] for k in sec})
            rec("%s: misleading titles are gone (MUSE IS ON IT / CURRENT FOCUS / TODAY'S PLAN / COMING UP)" % tag, not any(x in body for x in ("MUSE IS ON IT", "CURRENT FOCUS", "TODAY'S PLAN", "COMING UP")))
            rec("%s: recommendation shows its linked thread's next step and an Open button" % tag, "Fixture next step" in sec["focus"]["text"] and pg.is_visible("#focus button[data-ui=open-thread]"), sec["focus"]["text"][:160])
            rec("%s: focus visible above the fold" % tag, sec["focus"]["top"] < VIEWPORTS[vp][1] * 0.6)
            rec("%s: hostile text rendered literally (escaped)" % tag, "<img src=x" in body)
            for k, (key, lim) in LIMITS.items():
                n_shown = pg.evaluate("document.querySelectorAll('#%s .body > .ask, #%s .body > .card-row, #%s .body > .item').length" % (k, k, k))
                rec("%s: %s first-screen shows <= %d items" % (tag, k, lim), n_shown <= lim, "shows %d" % n_shown)
            rec("%s: sensitive ask is flagged" % tag, "Sensitive" in sec["needs-you"]["text"])
            up = sec["upcoming"]["text"]
            rec("%s: upcoming shows today's plan (any status) together with the coming days, <= 8 rows" % tag, "Fixture plan tonight todo" in up and "Fixture plan morning active" in up and s["tlRows"] <= 8, s["tlRows"])
            rec("%s: upcoming excludes tomorrow-only noise of done/missed/unplanned items" % tag, not any(x in up for x in ("Fixture plan done", "Fixture plan missed", "Fixture high priority unplanned")))
            rec("%s: missed plans are kept in a collapsed list, not lost" % tag, "Missed plans" in up)
            lists = up + sec["in-progress"]["text"] + sec["needs-you"]["text"]
            rec("%s: a task planned today and recommended appears once across the lists" % tag, lists.count("Fixture plan morning active") == 1, lists.count("Fixture plan morning active"))
            rec("%s: the recommended thread's rows are marked Recommended instead of looking unrelated" % tag, "Recommended" in up)
            rec("%s: priority never makes an unplanned task appear in Upcoming" % tag, "Fixture high priority unplanned" not in up)
            opened = pg.evaluate("[...document.querySelectorAll('#matters .body > details.area[open]')].length")
            rec("%s: All Matters areas are collapsed by default" % tag, opened == 0 and "Research" in sec["matters"]["text"], opened)
            rec("%s: archived threads with real open work are flagged, not hidden" % tag, "1 with open tasks" in sec["matters"]["text"], sec["matters"]["text"][-200:])
            wtxt = pg.evaluate("document.getElementById('thread-fxid-th-7').textContent")
            rec("%s: an archived thread that only has a background Waiting task is plain tracking, not a warning" % tag, "tracking" in wtxt and "Archived, but still attached" not in wtxt and "open" not in wtxt.split("Archived")[0], wtxt[:160])
            ip = pg.eval_on_selector_all("#in-progress .item .t", "e=>e.map(x=>x.innerText.trim())")
            rec("%s: the recommended task is not repeated in IN PROGRESS (link-based)" % tag, "Fixture recommended work" not in ip and "In progress: Fixture recommended work" in sec["focus"]["text"], ip)
            rec("%s: a similarly-titled but different task is NOT merged away" % tag, "Fixture recommended work copy" in ip, ip)
            icons = pg.evaluate("Promise.all([...document.querySelectorAll('link[rel~=icon],link[rel=apple-touch-icon]')].map(l => fetch(l.href).then(r => [l.getAttribute('href'), r.status, r.headers.get('content-type') || ''])))")
            rec("%s: the page declares an SVG icon, a 32px PNG and an apple-touch icon, all served as images" % tag, len(icons) == 3 and all(i[1] == 200 and i[2].startswith("image/") for i in icons), icons)
            rec("%s: theme-color is set for light and dark" % tag, pg.evaluate("document.querySelectorAll('meta[name=theme-color]').length") == 2)
            rec("%s: thread summaries carry no action buttons" % tag, pg.evaluate("document.querySelectorAll('details.thread > summary button[data-act]').length") == 0)
            rec("%s: no thread card shows a prominent 'Mark done' (archive is a secondary entry)" % tag, "Mark done" not in pg.evaluate("document.getElementById('matters').textContent"))
        if view == "timeline":
            tzs = {"Fixture tz offset EDT": "8:30 AM", "Fixture tz naive EDT": "2:30 PM", "Fixture tz offset EST": "10:00 AM", "Fixture tz naive EST": "10:00 AM",
                   "Fixture dst end naive": "9:00 AM", "Fixture dst end offset ok": "9:00 AM", "Fixture dst end slip": "8:00 AM",       # -04:00 after the fall-back is one hour early
                   "Fixture dst start naive": "10:00 AM", "Fixture dst start offset ok": "10:00 AM", "Fixture dst start slip": "11:00 AM",
                   "Fixture AoE deadline": "7:59 AM"}
            rec("%s: the Timeline states that times are Eastern Time (America/New_York)" % tag, "All times are Eastern Time (America/New_York)" in body, body[:120])
            rec("%s: the header states Eastern Time" % tag, "Eastern Time" in pg.inner_text("#today"), pg.inner_text("#today"))
            tzres = pg.evaluate("t => Object.fromEntries(Object.keys(t).map(k => [k, [...document.querySelectorAll('#timeline-full .tl')].filter(r => r.innerText.includes(k)).map(r => r.innerText)]))", tzs)
            badtz = {k: v for k, v in tzres.items() if not (len(v) == 1 and tzs[k] in v[0])}
            rec("%s: times show in America/New_York (EDT and EST, DST edges) even though this browser runs in UTC" % tag, not badtz, badtz)
            plan_rows = pg.evaluate("[...document.querySelectorAll('#timeline-full .tl')].filter(r=>r.innerText.includes('Fixture plan tomorrow')).map(r=>r.innerText)")
            rec("%s: planned task shown on its day, labelled Plan, without any invented clock time" % tag,
                len(plan_rows) == 1 and "Plan" in plan_rows[0] and not re.search(r"\d{1,2}:\d{2}", plan_rows[0].replace(plan_rows[0].split("\n")[0], "")), plan_rows)
            today_rows = pg.evaluate("[...document.querySelectorAll('#timeline-full .tl')].filter(r=>r.innerText.includes('Fixture plan tonight todo')).length")
            rec("%s: today's plan also visible in Timeline" % tag, today_rows == 1, today_rows)
            rows = pg.evaluate("[...document.querySelectorAll('#timeline-full .tl')].filter(r=>r.innerText.includes('Fixture shared')).length")
            rec("%s: same-thread/same-instant deadline+event shown once (de-duplicated)" % tag, rows == 1, "rows=%s" % rows)
            folded = pg.evaluate("[...document.querySelectorAll('#timeline-full .tl')].some(r=>r.innerText.includes('Fixture shared')&&r.innerText.includes('Reminder'))")
            rec("%s: reminder folded into its event; conservative 'Not confirmed' kept" % tag, folded and "Not confirmed" in body)
        if view == "week":
            wk = pg.evaluate("[...document.querySelectorAll('#week-days .wk-day')].map(d => ({date: d.getAttribute('data-date'), today: d.classList.contains('today'), text: d.innerText}))")
            tdy = call(base_url(), "/api/state")[1]["today"]
            dates = [d["date"] for d in wk]
            rec("%s: seven day columns starting today, consecutive, today highlighted" % tag, len(wk) == 7 and dates[0] == tdy and wk[0]["today"] and all((datetime.fromisoformat(dates[i + 1]) - datetime.fromisoformat(dates[i])).days == 1 for i in range(6)), dates)
            rec("%s: no clock times are shown in the 7-day view" % tag, not re.search(r"\d{1,2}:\d{2}", s["sections"]["week-grid"]["text"]), s["sections"]["week-grid"]["text"][:160])
            allst = call(base_url(), "/api/state")[1]
            wrong = []
            for e in allst["schedule"]:
                if e["id"] in ("fxid-s-neg", "fxid-s-1", "fxid-s-2", "fxid-s-3", "fxid-s-4", "fxid-s-5"):
                    want = datetime.fromtimestamp(e["when_ts"], NYZ).date().isoformat()
                    col = [d for d in wk if d["date"] == want]
                    if want in dates and not (col and e["title"] in col[0]["text"]): wrong.append((e["title"], want))
            rec("%s: events sit in the column of their Eastern-Time day" % tag, not wrong, wrong)
            plan = [d for d in wk if d["today"]][0]["text"]
            rec("%s: planned tasks (date only) appear under their day labelled Plan" % tag, "Fixture plan tonight todo" in plan and "Plan" in plan, plan[:160])
            rec("%s: items link to their thread and the view is read-only" % tag, pg.evaluate("document.querySelectorAll('#week-grid a.tlink').length") > 0 and pg.evaluate("document.querySelectorAll('#week-grid button[data-act]').length") == 0)
            rec("%s: deadlines and reminders are labelled Due / Reminder" % tag, "Due" in s["sections"]["week-grid"]["text"] and "Reminder" in s["sections"]["week-grid"]["text"])
            if vp == "mobile":
                cols = pg.evaluate("getComputedStyle(document.getElementById('week-days')).gridTemplateColumns.split(' ').length")
                rec("%s: on a phone the days stack in one column" % tag, cols == 1, cols)
        if view == "log":
            n = pg.evaluate("document.querySelectorAll('#log .entry').length")
            rec("%s: log default page <= 10 entries" % tag, n <= 10, n)
        if view == "system":
            rec("%s: routines grouped by cadence/time of day" % tag, all(x in body.lower() for x in ("always on", "weekly")) and "fixture once reminder" not in body.lower())
    return s


def theme_checks(pg, vp, out):
    LUM = """() => { const parse=c=>c.match(/[\\d.]+/g).map(Number), lin=v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4)};
      const L=c=>{const [r,g,b]=parse(c);return 0.2126*lin(r)+0.7152*lin(g)+0.0722*lin(b)};
      const bg=getComputedStyle(document.body).backgroundColor, fg=getComputedStyle(document.body).color;
      const x=L(fg),y=L(bg); return {bg, ratio:(Math.max(x,y)+0.05)/(Math.min(x,y)+0.05)}; }"""
    go(pg, "home")
    pg.evaluate("localStorage.removeItem('muse-theme'); document.documentElement.removeAttribute('data-theme')"); pg.reload(wait_until="load"); pg.wait_for_timeout(600)
    light = pg.evaluate(LUM)
    if pg.get_attribute("#theme-toggle", "aria-pressed") == "false": pg.click("#theme-toggle")
    pg.wait_for_timeout(300); dark = pg.evaluate(LUM)
    rec("theme/%s: toggle switches theme" % vp, light["bg"] != dark["bg"])
    rec("theme/%s: contrast >= 4.5:1 in both themes" % vp, light["ratio"] >= 4.5 and dark["ratio"] >= 4.5, "%.1f / %.1f" % (light["ratio"], dark["ratio"]))
    pg.reload(wait_until="load"); pg.wait_for_timeout(700)
    rec("theme/%s: choice persists across reload" % vp, pg.evaluate(LUM)["bg"] == dark["bg"])
    for v in VIEWS:
        go(pg, v); ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
        if ov: rec("theme/%s/%s: dark theme no horizontal scroll" % (vp, v), False)
    pg.screenshot(path=os.path.join(out, "dark-%s-home-fold.png" % vp)); go(pg, "home"); pg.screenshot(path=os.path.join(out, "dark-%s-home-full.png" % vp), full_page=True)
    pg.evaluate("localStorage.removeItem('muse-theme')")


NYZ = None
_BASE = [""]
def base_url(): return _BASE[0]


def week_checks(browser, base):
    posts = []
    ctx, pg, st = open_page(browser, "desktop", base)
    today = call(base, "/api/state")[1]["today"]
    pg.evaluate("location.hash='#week'"); pg.wait_for_timeout(800)
    dates = lambda: pg.evaluate("[...document.querySelectorAll('#week-days .wk-day')].map(d => d.getAttribute('data-date'))")
    rec("week: Previous is disabled on the current week and Today is disabled", pg.is_disabled("#week-grid button[data-ui=week-prev]") and pg.is_disabled("#week-grid button[data-ui=week-today]"))
    pg.click("#week-grid button[data-ui=week-next]"); pg.wait_for_timeout(500)
    d1 = dates(); nxt = (datetime.fromisoformat(today) + timedelta(days=7)).date().isoformat()
    rec("week: Next moves seven days ahead and updates the link", d1[0] == nxt and pg.evaluate("location.hash") == "#week=" + nxt and not pg.is_disabled("#week-grid button[data-ui=week-prev]"), (d1[0], pg.evaluate("location.hash")))
    pg.click("#week-grid button[data-ui=week-prev]"); pg.wait_for_timeout(400)
    rec("week: Previous returns to the current week", dates()[0] == today)
    pg.click("#week-grid button[data-ui=week-next]"); pg.click("#week-grid button[data-ui=week-today]"); pg.wait_for_timeout(400)
    rec("week: Today resets the view", dates()[0] == today and pg.evaluate("location.hash") == "#week")
    # weeks around the real DST changes: days follow Eastern Time, whatever this browser's zone is
    for kind, titles in (("end", {"Fixture dst end naive": 0, "Fixture dst end offset ok": 0, "Fixture dst end slip": 0, "Fixture dst end late": 0, "Fixture dst end utc": -1}),
                         ("start", {"Fixture dst start naive": 0, "Fixture dst start offset ok": 0, "Fixture dst start slip": 0})):
        d = next_dst(kind); start = (datetime.fromisoformat(d) - timedelta(days=2)).date().isoformat()
        pg.goto(base + "/#week=" + start); pg.wait_for_timeout(1500)
        cols = pg.evaluate("[...document.querySelectorAll('#week-days .wk-day')].map(c => ({date: c.getAttribute('data-date'), text: c.innerText}))")
        ds = [c["date"] for c in cols]
        rec("week: the week containing the %s DST change has seven consecutive dates including %s" % (kind, d), len(cols) == 7 and d in ds and all((datetime.fromisoformat(ds[i + 1]) - datetime.fromisoformat(ds[i])).days == 1 for i in range(6)), ds)
        bad = []
        for title, shift in titles.items():
            want = (datetime.fromisoformat(d) + timedelta(days=shift)).date().isoformat()
            col = [c for c in cols if c["date"] == want]
            if not (col and title in col[0]["text"]): bad.append((title, want))
        rec("week: events around the %s DST change land on the correct Eastern day (incl. late evening and UTC-stored times)" % kind, not bad, bad)
    rec("week: no JS errors", not st["errors"], "; ".join(st["errors"]))
    ctx.close()
    ctx, pg, st = open_page(browser, "mobile", base)
    pg.evaluate("location.hash='#week'"); pg.wait_for_timeout(900)
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    pg.screenshot(path=os.path.join(OUT_DIR[0], "week-mobile-full.png"), full_page=True)
    rec("week: phone layout has no horizontal overflow", not ov)
    ctx.close()


def thread_card(pg, title):
    """Open a thread through the unified locate mechanism (hash) and return its selector."""
    tid = {"Fixture thread %d" % i: "fxid-th-%d" % i for i in range(1, 9)}[title]
    go(pg, "home"); pg.wait_for_selector("#matters details.area", timeout=8000)
    pg.evaluate("location.hash='#thread=%s'" % tid); pg.wait_for_timeout(600)
    sel = "#thread-%s" % tid
    pg.wait_for_selector(sel + "[open]", timeout=4000)
    return sel


def actions_for(base, thread):
    return [a for a in call(base, "/api/state")[1]["actions"] if a["type"] == "ui_action" and a["target"] == thread]


def ui_action_checks(browser, base, mtoken, htoken):
    posts = []
    ctx, pg, st = open_page(browser, "desktop", base, htoken, posts)
    card = thread_card(pg, "Fixture thread 3")
    txt = pg.inner_text(card)
    rec("ui_actions: Muse-declared buttons are rendered on the thread", all(x in txt for x in ("Fixture book it", "Fixture nudge me", "Fixture risky ask")))
    rec("ui_actions: expired action is not shown", "Fixture expired action" not in pg.inner_text("#matters"))
    n0 = len(posts)
    pg.click(card + " button[data-ui=ua-start]"); pg.wait_for_selector(card + " button[data-ui=ua-pick]")
    rec("ui_actions: multi-step choice stays local (nothing sent after start)", len(posts) == n0)
    pg.click(card + " button[data-ui=ua-pick]:has-text('Fixture morning')"); pg.wait_for_selector(card + " button[data-act=ui_action]:has-text('Confirm')")
    rec("ui_actions: reaches confirm step locally, still nothing sent", len(posts) == n0 and "Fixture morning" in pg.inner_text(card))
    pg.click(card + " button[data-ui=ua-back]"); pg.wait_for_selector(card + " button[data-ui=ua-pick]")
    pg.click(card + " button[data-ui=ua-pick]:has-text('Fixture afternoon')"); pg.wait_for_selector(card + " button[data-act=ui_action]:has-text('Confirm')")
    with pg.expect_response(lambda r: r.url.endswith("/api/action")) as ri:
        pg.dblclick(card + " button[data-act=ui_action]:has-text('Confirm')")
    pg.wait_for_timeout(1500)
    acts = actions_for(base, "fxid-th-3")
    rec("ui_actions: single structured submit accepted by backend", ri.value.status == 200 and len(acts) == 1 and acts[0]["params"]["intent"] == "schedule_fixture"
        and acts[0]["params"]["selection"] == {"slot": "pm"}, acts)
    rec("ui_actions: double click creates one action only", len(acts) == 1)
    card = thread_card(pg, "Fixture thread 3")
    rec("ui_actions: immediate 'accepted' status shown (waiting for Muse)", "Submitted · waiting for Muse" in pg.inner_text(card))
    rec("ui_actions: action does not leak to other threads", not actions_for(base, "fxid-th-1") and "Submitted" not in pg.inner_text("#matters details#thread-fxid-th-1"))
    aid = acts[0]["id"]
    call(base, "/api/update", "POST", {"actions_seen": aid}, mtoken); pg.wait_for_timeout(6500)
    rec("ui_actions: 'Muse read it' status shown", "Muse has read it" in pg.inner_text(thread_card(pg, "Fixture thread 3")))
    call(base, "/api/update", "POST", {"action_results": [{"id": aid, "status": "completed", "note": "Fixture booked"}]}, mtoken); pg.wait_for_timeout(6500)
    rec("ui_actions: completion shown only after Muse reports it", "✓ Done: Fixture booked" in pg.inner_text(thread_card(pg, "Fixture thread 3")))
    # sensitive declared action -> protected dialog, nothing sent before confirmation
    card = thread_card(pg, "Fixture thread 3"); n1 = len(posts)
    pg.click(card + " button[data-act=ui_action]:has-text('Fixture risky ask')"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    rec("ui_actions: sensitive action requires dialog, nothing sent yet", len(posts) == n1)
    pg.click("#muse-actions-root [role=dialog] button:has-text('Cancel')"); pg.wait_for_timeout(500)
    rec("ui_actions: cancelling the sensitive dialog sends nothing", len(posts) == n1 and not [a for a in actions_for(base, "fxid-th-3") if a["params"].get("intent") == "risky_fixture"])
    # network failure must be explicit, never fake success
    pg.route("**/api/action", lambda r: r.abort())
    pg.click(card + " button[data-act=ui_action]:has-text('Fixture nudge me')"); pg.wait_for_timeout(1500)
    rec("ui_actions: network failure is reported and no pending state is faked", pg.is_visible("#muse-actions-root .ma-toast.bad") and "Network error" in pg.inner_text("#muse-actions-root")
        and not [a for a in actions_for(base, "fxid-th-3") if a["params"].get("intent") == "nudge_fixture"])
    pg.unroute("**/api/action")
    # Muse withdraws the declarations: they disappear and cannot be submitted
    call(base, "/api/update", "POST", {"threads": [{"id": "fxid-th-3", "ui_actions": []}]}, mtoken); pg.wait_for_timeout(6500)
    rec("ui_actions: withdrawn actions disappear from the UI", "Fixture nudge me" not in pg.inner_text("#matters"))
    rec("ui_actions: withdrawn/forged submit rejected by backend", call(base, "/api/action", "POST", {"type": "ui_action", "section": "threads", "id": "fxid-th-3",
        "params": {"ui_action": "fx_nudge", "selection": {}}}, htoken)[0] == 422)
    unexpected = [e for e in st["errors"] if "/api/action" not in e and "ERR_FAILED" not in e]     # the aborted request above is intentional
    rec("ui_actions: no errors during ui_action flow", not unexpected, "; ".join(unexpected))
    ctx.close()
    # mobile: same local flow fits the screen
    call(base, "/api/update", "POST", {"threads": [{"id": "fxid-th-3", "ui_actions": [{"id": "fx_m", "label": "Fixture mobile pick", "intent": "mobile_fixture",
        "steps": [{"id": "when", "prompt": "Fixture when?", "choices": [{"value": "a", "label": "Choice A"}, {"value": "b", "label": "Choice B"}]}]}]}]}, mtoken)
    ctx, pg, st = open_page(browser, "mobile", base, htoken)
    card = thread_card(pg, "Fixture thread 3")
    pg.click(card + " button[data-ui=ua-start]"); pg.wait_for_selector(card + " button[data-ui=ua-pick]")
    ov1 = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    pg.click(card + " button[data-ui=ua-pick]:has-text('Choice A')"); pg.wait_for_selector(card + " button[data-act=ui_action]")
    ov2 = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    rec("ui_actions: mobile multi-step flow works without horizontal scroll", not ov1 and not ov2 and not st["errors"], "; ".join(st["errors"]))
    ctx.close()


def ctx_post(base, token, event, minutes_ago):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).astimezone(timezone(timedelta(hours=-4))).isoformat(timespec="seconds")
    return call(base, "/api/context/events", "POST", {"event": event, "device": "iphone", "ts": ts}, token)


def context_checks(browser, base, mtoken, ctoken, htoken):
    rec("context: unauthenticated event rejected (401)", call(base, "/api/context/events", "POST", {"event": "arrived_home", "ts": iso()})[0] == 401)
    rec("context: Muse/human tokens cannot post events", call(base, "/api/context/events", "POST", {"event": "arrived_home", "ts": iso()}, mtoken)[0] == 401
        and call(base, "/api/context/events", "POST", {"event": "arrived_home", "ts": iso()}, htoken)[0] == 401)
    rec("context: chronicle token cannot publish or click", call(base, "/api/update", "POST", {"heartbeat": True}, ctoken)[0] == 401
        and call(base, "/api/action", "POST", {"type": "done", "section": "queue", "id": "x"}, ctoken)[0] == 401)
    rec("context: free-form instruction fields rejected", call(base, "/api/context/events", "POST", {"event": "arrived_home", "ts": iso(), "cmd": "x"}, ctoken)[0] == 422)
    sel = "#context-line"
    ctx, pg, st = open_page(browser, "desktop", base)
    go(pg, "home")
    rec("context: no line shown when there is no event", not pg.is_visible(sel))
    ctx_post(base, ctoken, "left_office", 45)                                     # older than the TTL
    pg.wait_for_timeout(6500)
    rec("context: stale event (beyond TTL) is not shown as live", not pg.is_visible(sel))
    r = ctx_post(base, ctoken, "arrived_home", 2)
    rec("context: valid fresh event accepted", r[0] == 200 and r[1].get("stored"), r)
    pg.wait_for_timeout(6500)
    txt = pg.inner_text(sel) if pg.is_visible(sel) else ""
    rec("context: fresh event shown with source, action and time", "Phone detected" in txt and "entered Home geofence" in txt and re.search(r"\d{1,2}:\d{2}", txt), txt)
    rec("context: wording never claims the user is currently there", not re.search(r"(you are|you're|currently|now at|is home|at home)", txt, re.I), txt)
    ctx_post(base, ctoken, "left_home", 30)                                       # arrives late, happened earlier
    pg.wait_for_timeout(6500)
    rec("context: out-of-order older event does not replace the newer state", "entered Home geofence" in (pg.inner_text(sel) if pg.is_visible(sel) else ""))
    rec("context: duplicate event is not stored twice", ctx_post(base, ctoken, "arrived_home", 1)[1].get("duplicate") is True)
    rec("context: no JS errors", not st["errors"], "; ".join(st["errors"]))
    ctx.close()
    ctx, pg, st = open_page(browser, "mobile", base)
    go(pg, "home"); pg.wait_for_timeout(500)
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    box = pg.eval_on_selector(sel, "e=>{const r=e.getBoundingClientRect();return [r.left,r.right,innerWidth]}") if pg.is_visible(sel) else None
    rec("context: mobile layout fits (visible, no overflow)", box is not None and not ov and box[0] >= 0 and box[1] <= box[2], box)
    pg.screenshot(path=os.path.join(OUT_DIR[0], "context-mobile-fold.png"))
    ctx.close()


OUT_DIR = [""]


def st_of(base, sec, rid):
    return next(x for x in call(base, "/api/state")[1][sec] if x["id"] == rid)


def open_sec(pg, card):
    if pg.get_attribute(card + " details.sec", "open") is None:
        pg.click(card + " details.sec > summary")
    pg.wait_for_selector(card + " details.sec[open] button", timeout=3000)


def home_lite_checks(browser, base, mtoken, htoken):
    posts = []
    # --- old links and unknown hashes land on Home
    for old in ("#areas", "#now", "#home", "#bogus", "#matters"):
        ctx, pg, st = open_page(browser, "desktop", base, htoken, posts)
        pg.evaluate("location.hash='%s'" % old); pg.wait_for_timeout(700)
        ok = pg.is_visible("#view-home") and not pg.is_visible("#view-timeline") and pg.get_attribute("#nav a[data-view=home]", "aria-current") == "page"
        rec("compat: old link %s opens Home (no Areas page, no error)" % old, ok and not st["errors"], "; ".join(st["errors"]))
        ctx.close()
    ctx, pg, st = open_page(browser, "desktop", base, htoken, posts)
    rec("compat: navigation has no separate Areas entry", "Areas" not in pg.inner_text("#nav") and pg.inner_text("#nav").split() == ["Home", "Week", "Timeline", "Log", "System"], pg.inner_text("#nav"))
    # --- every non-system thread is reachable from Home
    api_threads = [t for t in call(base, "/api/state")[1]["threads"] if t.get("area") != "system"]
    dom = pg.evaluate("[...document.querySelectorAll('#matters details.thread')].map(e => e.id)")
    rec("home: All Matters gives access to every thread", all(("thread-" + t["id"]) in dom for t in api_threads), (len(dom), len(api_threads)))
    # --- locate from the recommendation
    go(pg, "home"); pg.evaluate("history.replaceState(null,'','#home')")
    pg.click("#focus button[data-ui=open-thread]"); pg.wait_for_timeout(1200)
    box = pg.evaluate("(() => { const e = document.getElementById('thread-fxid-th-3'); const r = e.getBoundingClientRect(); return [e.open, r.top, r.bottom, innerHeight]; })()")
    rec("locate: Open on the recommendation expands and scrolls to the linked thread", box[0] and box[1] < box[3] and box[2] > 0, box)
    pg.reload(wait_until="load"); pg.wait_for_timeout(1500)
    rec("persistence: expanded state is remembered across reload", pg.evaluate("document.getElementById('thread-fxid-th-3').open") is True)
    # --- archive safety (UI + backend)
    card = thread_card(pg, "Fixture thread 2")
    rec("archive: thread card has no prominent Mark done; the entry is secondary", "Mark done" not in pg.inner_text(card) and pg.is_visible(card + " details.sec > summary"))
    imp0 = {k: [{f: v for f, v in x.items() if f in ("id", "status", "due", "start", "next_run")} for x in call(base, "/api/state")[1][k] if x.get("thread") == "fxid-th-2"] for k in ("queue", "deadlines", "schedule", "automations")}
    r = call(base, "/api/action", "POST", {"type": "done", "section": "threads", "id": "fxid-th-2"}, htoken)
    rec("archive: the BACKEND refuses to archive a thread with open tasks without acknowledgment (409 open_work + impact)",
        r[0] == 409 and r[1]["error"]["code"] == "open_work" and r[1]["error"]["details"][0]["impact"]["tasks"] >= 1, r)
    rec("archive: refused request changed nothing", st_of(base, "threads", "fxid-th-2").get("status", "active") == "active")
    open_sec(pg, card); n0 = len(posts)
    pg.click(card + " details.sec button[data-act=done]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    dt = pg.inner_text("#muse-actions-root [role=dialog]")
    rec("archive: UI shows an explicit impact summary before archiving", "Still attached" in dt and "open task" in dt and "does not delete or cancel anything" in dt, dt)
    pg.click("#muse-actions-root [role=dialog] button:has-text('Cancel')"); pg.wait_for_timeout(500)
    rec("archive: cancelling sends nothing and changes nothing", len(posts) == n0 and st_of(base, "threads", "fxid-th-2").get("status", "active") == "active")
    open_sec(pg, card); pg.click(card + " details.sec button[data-act=done]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    pg.click("#muse-actions-root [role=dialog] button.primary"); pg.wait_for_timeout(1800)
    imp1 = {k: [{f: v for f, v in x.items() if f in ("id", "status", "due", "start", "next_run")} for x in call(base, "/api/state")[1][k] if x.get("thread") == "fxid-th-2"] for k in ("queue", "deadlines", "schedule", "automations")}
    rec("archive: acknowledged archive succeeds", st_of(base, "threads", "fxid-th-2")["status"] == "done")
    rec("archive: tasks, deadlines, events and reminders are NOT deleted or changed by archiving", imp0 == imp1)
    a = [x for x in call(base, "/api/state")[1]["actions"] if x["target"] == "fxid-th-2" and x["type"] == "done"][0]
    rec("archive: the action log records what was still open", a["params"]["impact"]["tasks"] >= 1 and a["params"]["acknowledged_open_tasks"] is True)
    go(pg, "home"); pg.evaluate("location.hash='#thread=fxid-th-2'"); pg.wait_for_timeout(800)
    at = pg.inner_text("#thread-fxid-th-2")
    rec("archive: an archived thread with open work explains itself", "still attached" in at and "keep running" in at, at[:200])
    rec("archive: waiting tasks remain visible under the archived thread", "Fixture queue item" in at or "open task" in at)
    open_sec(pg, "#thread-fxid-th-2"); pg.click("#thread-fxid-th-2 details.sec button[data-act=reopen]"); pg.wait_for_timeout(1500)
    rec("archive: Reopen restores the thread", st_of(base, "threads", "fxid-th-2").get("status") == "active")
    # --- task Done does not archive the thread
    card3 = thread_card(pg, "Fixture thread 3")
    pg.click(card3 + " .rel button[data-act=done] >> nth=0"); pg.wait_for_timeout(1500)
    rec("archive: marking a task Done never archives its thread", st_of(base, "threads", "fxid-th-3").get("status", "active") == "active")
    # --- an unlinked recommendation is shown without a guessed link
    call(base, "/api/update", "POST", {"focus": {"title": "Fixture unlinked focus", "reason": "no link"}}, mtoken)
    go(pg, "home"); pg.wait_for_timeout(6500)
    rec("focus: an unlinked recommendation is still shown, with no guessed Open link", "Fixture unlinked focus" in pg.inner_text("#focus") and not pg.is_visible("#focus button[data-ui=open-thread]"))
    rec("home: no errors", not st["errors"], "; ".join(st["errors"]))
    ctx.close()
    # --- mobile: everything expanded still fits
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
    ctx.add_init_script("try{localStorage.setItem('muse-open', JSON.stringify({'area:research':true,'area:course':true,'area:purchase':true,'area:health':true,'area:life':true,'archived':true,'thread:fxid-th-1':true,'thread:fxid-th-3':true,'thread:fxid-th-7':true}))}catch(e){}")
    pg = ctx.new_page(); pg.goto(base + "/#home", wait_until="load"); pg.wait_for_timeout(1500)
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    pg.screenshot(path=os.path.join(OUT_DIR[0], "home-expanded-mobile.png"), full_page=True)
    rec("mobile: Home with all matters expanded has no horizontal overflow", not ov)
    ctx.close()


def row_sel(scope, text):
    return "%s .card-row:has-text('%s')" % (scope, text)


def snap_others(base, skip):
    s = call(base, "/api/state")[1]
    return {sec: sorted(({k: v for k, v in x.items() if k in ("id", "status", "due", "start", "next_run", "attendance", "planned_for")} for x in s[sec] if x["id"] not in skip), key=lambda r: r["id"])
            for sec in ("queue", "deadlines", "schedule", "automations", "threads")}


def negation_checks(browser, base, mtoken, htoken):
    posts = []
    ctx, pg, st = open_page(browser, "desktop", base, htoken, posts)
    go(pg, "home"); pg.wait_for_selector("#upcoming .card-row", timeout=8000)
    # ---- Event: Not attending (internal intent; never an external cancellation)
    before = snap_others(base, {"fxid-s-neg"})
    r = row_sel("#upcoming", "Fixture neg event")
    pg.click(r + " button[data-ui=menu]"); pg.wait_for_selector("#upcoming .menu-pop", timeout=3000)
    mt = pg.inner_text("#upcoming .menu-pop")
    rec("negation: an Event row offers Not attending in a ··· menu (and says what it does)", "Not attending" in mt and "does not cancel any registration or booking" in mt, mt)
    n0 = len(posts)
    pg.click("#upcoming .menu-pop button[data-act=not_attending]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    dt = pg.inner_text("#muse-actions-root [role=dialog]")
    rec("negation: the dialog separates the internal update from external commitments", "does not cancel any registration, booking or message" in dt and "Muse will ask you before doing anything outside" in dt, dt)
    pg.click("#muse-actions-root [role=dialog] button:has-text('Cancel')"); pg.wait_for_timeout(400)
    rec("negation: cancelling the dialog sends nothing", len(posts) == n0 and st_of(base, "schedule", "fxid-s-neg").get("attendance") is None)
    pg.click("#upcoming .menu-pop button[data-act=not_attending]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    with pg.expect_response(lambda x: x.url.endswith("/api/action")) as ri:
        pg.click("#muse-actions-root [role=dialog] button.primary")
    pg.wait_for_timeout(1500)
    e = st_of(base, "schedule", "fxid-s-neg")
    rec("negation: Not attending is stored as the human's intent, not as Done or cancelled", ri.value.status == 200 and e.get("attendance") == "not_going" and e["status"] == "confirmed", e)
    rec("negation: related deadlines, tasks, threads and other reminders are untouched", snap_others(base, {"fxid-s-neg"}) == before)
    rec("negation: the event leaves Upcoming", "Fixture neg event" not in pg.inner_text("#upcoming"))
    pg.evaluate("location.hash='#timeline'"); pg.wait_for_timeout(700)
    tr = pg.inner_text("#timeline-full")
    rec("negation: the Timeline keeps it, marked Not attending, with an Attending again path", "Fixture neg event" in tr and "Not attending" in tr, tr[:200])
    trow = row_sel("#timeline-full", "Fixture neg event")
    pg.click(trow + " button[data-ui=menu]"); pg.click("#timeline-full .menu-pop button[data-act=attend]"); pg.wait_for_timeout(1500)
    rec("negation: Attending again restores it (recoverable mis-click)", st_of(base, "schedule", "fxid-s-neg").get("attendance") is None)
    # Muse's feedback shows up only once it reports
    call(base, "/api/update", "POST", {"actions_seen": 9999}, mtoken)
    # ---- Reminder: Stop reminding is a REQUEST; only Muse's confirmation completes it
    def wait_poll(): pg.wait_for_timeout(6500)
    go(pg, "home"); pg.wait_for_selector("#upcoming .card-row", timeout=8000)
    r = row_sel("#upcoming", "Fixture solo reminder")
    pg.click(r + " button[data-ui=menu]"); pg.click("#upcoming .menu-pop button[data-act=stop_reminding]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    dt = pg.inner_text("#muse-actions-root [role=dialog]")
    rec("reminder: the dialog says it asks Muse and is not final until Muse confirms", "asks Muse to stop its own scheduled reminder" in dt and "until Muse confirms" in dt and "does not cancel anything outside" in dt, dt)
    pg.dblclick("#muse-actions-root [role=dialog] button.primary"); pg.wait_for_timeout(1800)
    toast = pg.inner_text("#muse-actions-root") if pg.query_selector("#muse-actions-root .ma-toast") else ""
    rec("reminder: the confirmation says 'waiting for Muse', not 'stopped'", "waiting for Muse" in toast and "Reminder stopped" not in toast, toast)
    acts = [a for a in call(base, "/api/state")[1]["actions"] if a["target"] == "fxid-a-solo" and a["type"] == "stop_reminding"]
    rec("reminder: a double click is one request", len(acts) == 1, len(acts))
    rec("reminder: the mirror is NOT changed by the click (the native task is Muse's)", st_of(base, "automations", "fxid-a-solo").get("status") in (None, "ok"))
    rt = pg.inner_text("#upcoming")
    rec("reminder: it stays listed as 'stop requested', not as stopped", "Fixture solo reminder" in rt and "Stop requested · waiting for Muse to confirm" in rt, rt[:300])
    pg.click(r + " button[data-ui=menu]"); mt = pg.inner_text("#upcoming .menu-pop")
    rec("reminder: while pending the menu offers to withdraw the request", "Cancel stop request" in mt, mt)
    aid = acts[0]["id"]
    # Muse cannot stop its native task -> the user sees a failure, not success
    call(base, "/api/update", "POST", {"actions_seen": aid, "action_results": [{"id": aid, "status": "unable", "note": "fixture scheduler refused"}]}, mtoken); wait_poll()
    rt = pg.inner_text("#upcoming")
    rec("reminder: when Muse cannot stop it, the failure is shown (not faked)", "Muse couldn't stop this reminder: fixture scheduler refused" in rt and "Stopped" not in rt, rt[:300])
    rec("reminder: after a failure it is still active and can be tried again", st_of(base, "automations", "fxid-a-solo").get("status") in (None, "ok"))
    if not pg.is_visible("#upcoming .menu-pop"): pg.click(r + " button[data-ui=menu]")
    pg.click("#upcoming .menu-pop button[data-act=stop_reminding]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    pg.click("#muse-actions-root [role=dialog] button.primary"); pg.wait_for_timeout(1800)
    aid2 = [a for a in call(base, "/api/state")[1]["actions"] if a["target"] == "fxid-a-solo" and a["type"] == "stop_reminding"][0]["id"]
    rec("reminder: retrying creates a new request", aid2 != aid)
    # Muse really stops it: updates the mirror and confirms
    call(base, "/api/update", "POST", {"automations": [{"id": "fxid-a-solo", "status": "paused"}], "actions_seen": aid2, "action_results": [{"id": aid2, "status": "completed", "note": "native task stopped"}]}, mtoken); wait_poll()
    rec("reminder: only after Muse confirms does it leave Upcoming", "Fixture solo reminder" not in pg.inner_text("#upcoming"))
    pg.evaluate("location.hash='#timeline'"); pg.wait_for_timeout(700)
    trow = row_sel("#timeline-full", "Fixture solo reminder")
    rec("reminder: a confirmed-stopped reminder stays in Timeline as Stopped (resumable)", pg.is_visible(trow) and "Stopped" in pg.inner_text(trow))
    pg.click(trow + " button[data-ui=menu]"); pg.click("#timeline-full .menu-pop button[data-act=resume_reminding]"); pg.wait_for_timeout(1800)
    rec("reminder: Resume is also a request until Muse confirms", st_of(base, "automations", "fxid-a-solo").get("status") == "paused" and "Resume requested · waiting for Muse to confirm" in pg.inner_text(trow))
    aid3 = [a for a in call(base, "/api/state")[1]["actions"] if a["target"] == "fxid-a-solo" and a["type"] == "resume_reminding"][0]["id"]
    call(base, "/api/update", "POST", {"automations": [{"id": "fxid-a-solo", "status": "ok"}], "actions_seen": aid3, "action_results": [{"id": aid3, "status": "completed"}]}, mtoken); wait_poll()
    go(pg, "home"); pg.wait_for_selector("#upcoming .card-row", timeout=8000)
    rec("reminder: once Muse re-enables it, it is back in Upcoming without a pending tag", "Fixture solo reminder" in pg.inner_text("#upcoming") and "waiting for Muse" not in pg.inner_text(row_sel("#upcoming", "Fixture solo reminder")))
    # ---- Task: Not doing + Restore + double click
    go(pg, "home"); pg.wait_for_selector("#upcoming .card-row", timeout=8000)
    r = row_sel("#upcoming", "Fixture plan tonight todo")
    pg.click(r + " button[data-ui=menu]"); pg.wait_for_selector("#upcoming .menu-pop", timeout=3000)
    pg.dblclick("#upcoming .menu-pop button[data-act=set_status]"); pg.wait_for_timeout(1800)
    acts = [a for a in call(base, "/api/state")[1]["actions"] if a["target"] == "fxid-q-p1" and a["type"] == "set_status"]
    rec("negation: Not doing marks the task dropped (not Done) and a double click is a single action", st_of(base, "queue", "fxid-q-p1")["status"] == "dropped" and len(acts) == 1, len(acts))
    rec("negation: the task leaves Upcoming", "Fixture plan tonight todo" not in pg.inner_text("#upcoming"))
    pg.evaluate("document.querySelector('#matters details[data-key=archived]').open = true; document.querySelector('#matters details[data-key=arch-recs]').open = true"); pg.wait_for_timeout(400)
    rrow = "#matters details[data-key=arch-recs] .kvl:has-text('Fixture plan tonight todo')"
    rec("negation: dropped tasks are kept under Archived with a Restore button", pg.is_visible(rrow) and "not doing" in pg.inner_text(rrow) and pg.is_visible(rrow + " button[data-act=reopen]"))
    pg.click(rrow + " button[data-act=reopen]"); pg.wait_for_timeout(1500)
    rec("negation: Restore brings the task back", st_of(base, "queue", "fxid-q-p1")["status"] == "todo")
    # ---- authority, sensitive path, no token
    rec("negation: Muse's token cannot click Not attending / Stop reminding (401)", call(base, "/api/action", "POST", {"type": "not_attending", "section": "schedule", "id": "fxid-s-neg"}, mtoken)[0] == 401
        and call(base, "/api/action", "POST", {"type": "stop_reminding", "section": "automations", "id": "fxid-a-solo"}, mtoken)[0] == 401)
    rec("negation: a past event, a routine and a non-event are rejected by the backend", call(base, "/api/action", "POST", {"type": "stop_reminding", "section": "automations", "id": "fxid-a-1"}, htoken)[0] == 422
        and call(base, "/api/action", "POST", {"type": "not_attending", "section": "queue", "id": "fxid-q-1"}, htoken)[0] == 422)
    rec("negation: no JS errors", not st["errors"], "; ".join(st["errors"]))
    ctx.close()
    ctx, pg, st = open_page(browser, "mobile", base, htoken)
    go(pg, "home"); pg.wait_for_selector("#upcoming .card-row", timeout=8000)
    pg.click("#upcoming button[data-ui=menu] >> nth=0"); pg.wait_for_selector("#upcoming .menu-pop", timeout=3000)
    ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    pg.screenshot(path=os.path.join(OUT_DIR[0], "menu-mobile.png"))
    rec("negation: mobile menu fits without horizontal scroll", not ov)
    ctx.close()


def interaction_checks(browser, base, mtoken, htoken, out):
    posts = []
    ctx, pg, st = open_page(browser, "desktop", base, htoken, posts)
    go(pg, "home"); pg.wait_for_selector("#in-progress button[data-act=done]", timeout=8000)
    first = pg.inner_text("#in-progress .item .t")
    with pg.expect_response(lambda r: r.url.endswith("/api/action")) as ri:
        pg.click("#in-progress .item button[data-act=done]")
    rec("interaction: Done click -> authorised POST /api/action 200", ri.value.status == 200, ri.value.status)
    pg.wait_for_timeout(1500)
    q = {x["id"]: x for x in call(base, "/api/state")[1]["queue"]}
    done_ids = [i for i, x in q.items() if x.get("status") == "done" and x["title"] == first]
    rec("interaction: state really changed in the backend (not just visually)", bool(done_ids) and q[done_ids[0]].get("human", {}).get("type") == "done")
    rec("interaction: UI reflects the change", first not in pg.eval_on_selector_all("#in-progress .item .t", "e=>e.map(x=>x.innerText)"))
    # sensitive confirmation goes through the protected dialog
    n0 = len(posts)
    pg.click("#needs-you .ask.sens button[data-act=confirm]"); pg.wait_for_selector("#muse-actions-root [role=dialog]", timeout=4000)
    rec("interaction: sensitive confirm shows protected dialog and sends nothing yet", len(posts) == n0)
    with pg.expect_response(lambda r: r.url.endswith("/api/action")) as ri:
        pg.click("#muse-actions-root [role=dialog] button.primary")
    rec("interaction: sensitive confirm applied only after dialog confirmation", ri.value.status == 200 and "sensitive_ack" in posts[-1])
    inbox = {x["id"]: x for x in call(base, "/api/state")[1]["inbox"]}
    rec("interaction: sensitive item confirmed by human", inbox["fxid-in-1"]["status"] == "confirmed")
    # decision with options -> choice recorded
    pg.wait_for_timeout(800)
    pg.click("#needs-you .ask button[data-option='Option B']"); pg.wait_for_timeout(1200)
    inbox = {x["id"]: x for x in call(base, "/api/state")[1]["inbox"]}
    rec("interaction: decision option recorded as choice", inbox["fxid-in-2"].get("choice") == "Option B" and inbox["fxid-in-2"]["status"] == "resolved")
    # stage change on a thread card
    card1 = thread_card(pg, "Fixture thread 1")
    pg.click(card1 + " button[data-act=set_stage][aria-pressed=false] >> nth=0"); pg.wait_for_timeout(1500)
    th = {x["id"]: x for x in call(base, "/api/state")[1]["threads"]}
    rec("interaction: thread stage change persisted", th["fxid-th-1"]["stage"] != "Write" or th["fxid-th-3"]["stage"] != "Look")
    # scripted clicks must be ignored
    go(pg, "home"); n1 = len(posts)
    pg.evaluate("document.querySelector('#in-progress button[data-act=done]').click()"); pg.wait_for_timeout(900)
    rec("interaction: script-generated (untrusted) clicks do nothing", len(posts) == n1)
    rec("interaction: actions are logged and visible to Muse (unseen > 0)", call(base, "/api/state")[1]["unseen_actions"] >= 4)
    rec("interaction: no errors during interactions", not st["errors"], "; ".join(st["errors"]))
    ctx.close()
    ui_action_checks(browser, base, mtoken, htoken)
    # no token -> unlock dialog, nothing sent
    posts2 = []
    ctx, pg, st = open_page(browser, "desktop", base, None, posts2)
    go(pg, "home"); pg.wait_for_selector("#in-progress button[data-act=done]", timeout=8000)
    pg.click("#in-progress .item button[data-act=done]"); pg.wait_for_selector("#muse-actions-root input[type=password]", timeout=4000)
    rec("interaction: without token a click asks to unlock and sends nothing", not posts2)
    ctx.close()
    # token/role separation
    rec("auth: Muse token cannot click (401)", call(base, "/api/action", "POST", {"type": "done", "section": "queue", "id": "fxid-q-1"}, mtoken)[0] == 401)
    rec("auth: human token cannot publish (401)", call(base, "/api/update", "POST", {"heartbeat": True}, htoken)[0] == 401)
    rec("auth: Muse cannot answer a sensitive item (422)", call(base, "/api/update", "POST", {"inbox": [{"id": "fxid-in-3", "sensitive": True}]}, mtoken)[0] == 200
        and call(base, "/api/update", "POST", {"inbox": [{"id": "fxid-in-3", "status": "confirmed"}]}, mtoken)[0] == 422)
    code, res = call(base, "/api/update", "POST", {"actions_seen": 9999}, mtoken)
    rec("auth: Muse can mark human actions as seen", code == 200 and call(base, "/api/state")[1]["unseen_actions"] == 0)


def run(a, base):
    rec("server reachable", wait_up(base))
    if not checks[-1]["ok"]: return
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        if a.mode == "smoke-lite":      # verifying a ROLLBACK: the older release may predate today's page contract
            for vp in VIEWPORTS:
                ctx, pg, st = open_page(browser, vp, base)
                txt = pg.evaluate("document.body.innerText.length"); ov = pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
                rec("rollback/%s: page renders (non-blank)" % vp, txt > 100, txt)
                rec("rollback/%s: no JS/console errors" % vp, not st["errors"], "; ".join(st["errors"]))
                rec("rollback/%s: no horizontal scroll" % vp, not ov)
                ctx.close()
            return
        if a.mode == "smoke":
            for vp in VIEWPORTS:
                ctx, pg, st = open_page(browser, vp, base)
                for v in VIEWS: assess(vp, pg, st, "prod", v, None, a.out)
                ctx.close()
            return
        rec("API: unauthenticated write rejected (401)", call(base, "/api/update", "POST", {"heartbeat": True})[0] == 401)
        rec("API: unauthenticated action rejected (401)", call(base, "/api/action", "POST", {"type": "done", "section": "queue", "id": "x"})[0] == 401)
        rec("API: dashboard endpoints are read-only (405 on POST /api/state)", call(base, "/api/state", "POST", {})[0] == 405)
        rec("API: invalid update rejected with 422", call(base, "/api/update", "POST", {"queue": [{"id": "a", "status": "bogus"}]}, a.token)[0] == 422)
        rec("API: /api/state leaks no tokens", a.token not in json.dumps(call(base, "/api/state")[1]) and a.human_token not in json.dumps(call(base, "/api/state")[1]))
        for vp in VIEWPORTS:       # empty state
            ctx, pg, st = open_page(browser, vp, base)
            for v in VIEWS: assess(vp, pg, st, "empty", v, None, a.out)
            ctx.close()
        fx = fixture(call(base, "/api/state")[1]["today"]); payload = dict(fx)
        code, body = call(base, "/api/update", "POST", payload, a.token)
        rec("API: authenticated fixture update accepted", code == 200, body)
        if code != 200: return
        for vp in VIEWPORTS:
            ctx, pg, st = open_page(browser, vp, base)
            pg.evaluate("window.__marker = 1")
            for v in VIEWS: assess(vp, pg, st, "seeded", v, fx, a.out)
            call(base, "/api/update", "POST", {"journal": [{"message": "Fixture live-update marker 12345", "kind": "note"}]}, a.token)
            go(pg, "log"); pg.click("#log button[data-tab=activity]")
            ok = False
            for _ in range(14):
                pg.wait_for_timeout(1000)
                if pg.evaluate("document.body.innerText.includes('Fixture live-update marker 12345')"): ok = True; break
            rec("seeded/%s: polls /api/state and updates without reload" % vp, ok and pg.evaluate("window.__marker === 1"))
            theme_checks(pg, vp, a.out)
            ctx.close()
        interaction_checks(browser, base, a.token, a.human_token, a.out)
        home_lite_checks(browser, base, a.token, a.human_token)
        week_checks(browser, base)
        negation_checks(browser, base, a.token, a.human_token)
        context_checks(browser, base, a.token, a.chronicle_token, a.human_token)
        rec("API: unauthenticated write still rejected after tests", call(base, "/api/update", "POST", {"heartbeat": True})[0] == 401)
        browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True); ap.add_argument("--base", required=True)
    ap.add_argument("--token"); ap.add_argument("--human-token", dest="human_token", default=""); ap.add_argument("--chronicle-token", dest="chronicle_token", default=""); ap.add_argument("--out", required=True)
    a = ap.parse_args(); base = a.base.rstrip("/"); os.makedirs(a.out, exist_ok=True); OUT_DIR[0] = a.out; _BASE[0] = base
    global NYZ
    from zoneinfo import ZoneInfo
    NYZ = ZoneInfo("America/New_York")
    try: run(a, base)
    except Exception as e: rec("checks runner crashed", False, repr(e))
    json.dump({"ok": all(c["ok"] for c in checks) and bool(checks), "checks": checks}, open(os.path.join(a.out, "checks.json"), "w"), indent=1)


main()
