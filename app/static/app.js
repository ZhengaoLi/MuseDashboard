(function () {
  "use strict";
  var POLL_MS = 5000, lost = false, S = null, last = "", view = "home", logTab = "digest", logLimit = 10;
  function loadOpen() { try { return JSON.parse(localStorage.getItem("muse-open") || "{}") || {}; } catch (e) { return {}; } }
  function saveOpen() { try { localStorage.setItem("muse-open", JSON.stringify(openKeys)); } catch (e) {} }
  var openKeys = loadOpen(), TEST_RE = /\b(test|probe|smoke|verification)\b/i, DAY = 86400;

  // ---------- tiny DOM helpers (text nodes only) ----------
  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null && text !== "") e.textContent = String(text);
    return e;
  }
  function add(p) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) p.appendChild(arguments[i]); return p; }
  function body(id) { var b = document.querySelector("#" + id + " .body"); b.textContent = ""; return b; }
  function empty(b, msg) { b.appendChild(h("div", "empty", msg)); }
  function nowS() { return Date.now() / 1000; }

  // ---------- formatting ----------
  function dur(s) {
    s = Math.abs(Math.round(s)); var m = Math.floor(s / 60);
    if (m < 1) return "moments"; if (m < 60) return m + (m === 1 ? " minute" : " minutes");
    var hr = Math.floor(m / 60); if (hr < 24) return hr + (hr === 1 ? " hour" : " hours");
    var d = Math.floor(hr / 24); return d + (d === 1 ? " day" : " days");
  }
  function short(s) {
    s = Math.abs(Math.round(s)); var m = Math.floor(s / 60); if (m < 1) return "<1m"; if (m < 60) return m + "m";
    var hr = Math.floor(m / 60); if (hr < 24) return hr + "h"; return Math.floor(hr / 24) + "d";
  }
  function tz() { return (S && S.tz) || undefined; }
  function tzLabel() { var z = (S && S.tz) || ""; return z === "America/New_York" ? "Eastern Time" : z.replace(/_/g, " "); }
  function tzParts(ts) {
    var o = {}; new Intl.DateTimeFormat("en-US", { timeZone: tz(), year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(ts * 1000)).forEach(function (p) { o[p.type] = p.value; }); return o;
  }
  function tzOffset(ts) { var p = tzParts(ts); return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) / 1000 - Math.floor(ts / 60) * 60; }
  function wallToEpoch(y, m, d, hh, mm) {      // wall-clock time in the display zone -> real instant (DST-aware)
    var guess = Date.UTC(y, m - 1, d, hh || 0, mm || 0) / 1000, t = guess - tzOffset(guess); return guess - tzOffset(t);
  }
  function dayNum(ts) { var p = tzParts(ts); return Date.UTC(+p.year, +p.month - 1, +p.day) / 86400000; }
  function parse(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    if (m) return { date: new Date(+m[1], +m[2] - 1, +m[3]), dateOnly: true };
    var n = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s || "");      // no offset: server-zone wall time
    if (n) return { date: new Date(wallToEpoch(+n[1], +n[2], +n[3], +n[4], +n[5]) * 1000), dateOnly: false };
    return { date: new Date(s), dateOnly: false };
  }
  var DOPT = { weekday: "short", month: "short", day: "numeric" }, TOPT = { hour: "numeric", minute: "2-digit" };
  function withTz(o) { var x = {}; for (var k in o) x[k] = o[k]; x.timeZone = tz(); return x; }
  function fmtDate(s) { var p = parse(s); if (isNaN(p.date)) return s || ""; return p.date.toLocaleDateString([], p.dateOnly ? DOPT : withTz(DOPT)); }
  function fmtTime(s) { var p = parse(s); return (p.dateOnly || isNaN(p.date)) ? "" : p.date.toLocaleTimeString([], withTz(TOPT)); }
  function fmtTs(ts, dateOnly) { var d = new Date(ts * 1000), a = d.toLocaleDateString([], withTz(DOPT)); return dateOnly ? a : a + " · " + d.toLocaleTimeString([], withTz(TOPT)); }
  function ago(ts) { return dur((Date.parse(S.server_time) - Date.parse(ts)) / 1000) + " ago"; }
  function line(note) {
    if (!note) return "";
    var seg = note.split(/[；;。\n]/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (!seg.length) return ""; var out = seg[0]; if (out.length < 14 && seg[1]) out += "；" + seg[1]; return out;
  }

  // ---------- data helpers ----------
  function actionsOn() { return !!(S && S.human_actions_enabled); }
  function thr(id) { for (var i = 0; i < S.threads.length; i++) if (S.threads[i].id === id) return S.threads[i]; return null; }
  function areaLabel(id) { for (var i = 0; i < S.areas.length; i++) if (S.areas[i].id === id) return S.areas[i].label; return "Unsorted"; }
  function areaOf(r) { var t = r.thread && thr(r.thread); return r.area || (t && t.area) || ""; }
  function vis(r) { return !r.merged_into; }
  function closed(r) { return r.status === "done" || r.status === "dropped"; }
  function threadTitle(r) { var t = r.thread && thr(r.thread); return t ? t.title : ""; }
  function pr(p) { return { p0: 0, p1: 1, p2: 2, p3: 3 }[p] === undefined ? 9 : { p0: 0, p1: 1, p2: 2, p3: 3 }[p]; }
  var menuOpen = null;
  function menuBtn(key) {
    var b = h("button", "btn", "···"); b.type = "button"; b.setAttribute("data-ui", "menu"); b.setAttribute("data-key", key);
    b.setAttribute("aria-label", "More"); b.setAttribute("aria-expanded", menuOpen === key ? "true" : "false"); return b;
  }
  function menuPanel(items, note) {
    var p = h("div", "menu-pop");
    items.forEach(function (it) { add(p, btn(it.label, it.act, it.section, it.id, it.extra)); });
    if (note) p.appendChild(h("span", "menu-note", note));
    return p;
  }
  // what each negation means: an internal update only; nothing outside Muse's own records is cancelled
  var NOTE_ATT = "Records that you won't go and lets Muse stop its own reminders for it. It does not cancel any registration or booking.";
  function negItems(kind, rec, sec) {
    if (kind === "task" && !closed(rec)) return [{ label: "Not doing", act: "set_status", section: "queue", id: rec.id, extra: { status: "dropped" } }];
    if (kind === "event") return rec.attendance === "not_going" ? [{ label: "Attending again", act: "attend", section: "schedule", id: rec.id }] : [{ label: "Not attending", act: "not_attending", section: "schedule", id: rec.id }];
    if (kind === "reminder") {
      var ri = remInfo(rec);
      if (ri.pendingStop) return [{ label: "Cancel stop request", act: "resume_reminding", section: "automations", id: rec.id }];
      if (ri.stopped) return [{ label: "Resume reminder", act: "resume_reminding", section: "automations", id: rec.id }];
      return [{ label: "Stop reminding", act: "stop_reminding", section: "automations", id: rec.id }];
    }
    return [];
  }
  function remInfo(rec) {         // what is really true about a reminder mirror: only Muse's confirmation counts
    var a = latestAction("automations", rec.id), t = a && a.type, pend = a && (a.status === "accepted" || a.status === "seen"), fail = a && (a.status === "unable" || a.status === "declined");
    var o = { stopped: rec.status === "paused", tag: "", bad: false, pendingStop: false };
    if (t === "stop_reminding") {
      if (pend) { o.tag = "Stop requested · waiting for Muse to confirm"; o.pendingStop = true; }
      else if (fail) { o.tag = "Muse couldn't stop this reminder" + (a.note ? ": " + a.note : ""); o.bad = true; }
      else if (a.status === "completed") o.stopped = true;
    } else if (t === "resume_reminding") {
      if (pend) o.tag = rec.status === "paused" ? "Resume requested · waiting for Muse to confirm" : "Withdraw requested · waiting for Muse to confirm";
      else if (fail) { o.tag = "Muse couldn't resume this reminder" + (a.note ? ": " + a.note : ""); o.bad = true; }
      else if (a.status === "completed") o.stopped = false;
    }
    return o;
  }
  function latestAction(section, id) { for (var i = 0; i < S.actions.length; i++) if (S.actions[i].section === section && S.actions[i].target === id) return S.actions[i]; return null; }
  function pendingNote(section, id) {
    var a = latestAction(section, id);
    if (!a || a.type !== "not_attending") return "";
    if (a.status === "accepted" || a.status === "seen") return "Muse is reviewing related reminders";
    return a.status === "completed" && a.note ? "Muse: " + a.note : "";
  }
  function itRow(text, extra) { var r = h("div", "it"); add(r, h("span", null, text), extra); return r; }
  function chip(txt, cls) { return h("span", "tag " + (cls || ""), txt); }

  // ---------- action buttons (clicks are handled by the protected /_ui/actions.js) ----------
  function btn(label, act, section, id, extra, cls) {
    if (!actionsOn()) return null;
    var b = h("button", "btn" + (cls ? " " + cls : ""), label); b.type = "button";
    b.setAttribute("data-act", act); b.setAttribute("data-section", section); b.setAttribute("data-id", id);
    if (extra) for (var k in extra) b.setAttribute("data-" + k, extra[k]);
    return b;
  }
  function doneBtn(section, r) {
    return closed(r) || r.status === "confirmed" ? btn("Reopen", "reopen", section, r.id) : btn("Done", "done", section, r.id);
  }
  function details(key, cls, summary) {
    var d = h("details", cls); d.setAttribute("data-key", key); if (openKeys[key]) d.open = true;
    var s = h("summary"); if (typeof summary === "string") s.textContent = summary; else if (summary) s.appendChild(summary);
    d.appendChild(s); return d;
  }

  // ---------- header ----------
  function renderHeader() {
    document.getElementById("today").textContent = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", timeZone: tz() }) + (tzLabel() ? " · " + tzLabel() : "");
    var el = document.getElementById("pulse"), t = document.getElementById("pulse-text"), m = S.muse;
    el.className = "pulse " + (lost ? "lost" : m.freshness);
    el.title = m.last_update ? "Last successful Muse update: " + m.last_update : "";
    if (lost) t.textContent = "Connection lost · showing last data";
    else if (m.freshness === "never") t.textContent = "Muse hasn't reported yet";
    else if (m.freshness === "stale") t.textContent = "No update from Muse in " + dur(m.age_seconds);
    else t.textContent = "Muse updated " + ago(m.last_update);
  }

  // ---------- context line: latest phone observation (never claims where the user is now) ----------
  function renderContext() {
    var el = document.getElementById("context-line"), c = S.context, l = c && c.latest;
    if (lost || !l || !l.fresh) { el.hidden = true; el.textContent = ""; return; }      // past the TTL it is no longer a live signal
    var place = { home: "Home", office: "Office" }[l.place] || l.place, verb = l.type === "geofence_exit" ? "left" : "entered";
    var clock = new Date(l.observed_at).toLocaleTimeString([], withTz(TOPT));
    el.textContent = "";
    add(el, h("span", "dot"), h("span", null, "Phone detected: " + verb + " " + place + " geofence · " + clock + " · " + dur(l.age_seconds) + " ago"));
    el.title = "Geofence event reported by the phone at " + clock + ". It is an observation, not confirmation of where you are now.";
    el.hidden = false;
  }

  // ---------- NOW ----------
  function qById(id) { for (var i = 0; i < S.queue.length; i++) if (S.queue[i].id === id) return S.queue[i]; return null; }
  function focusLink() {      // only exact ids are followed; a missing link is never guessed
    var f = S.focus; if (!f) return { task: null, thread: null };
    var task = f.task ? qById(f.task) : null, th = f.project ? thr(f.project) : null;
    if (!th && f.project) S.projects.forEach(function (p) { if (p.id === f.project && p.thread) th = thr(p.thread); });
    if (!th && task && task.thread) th = thr(task.thread);
    return { task: task, thread: th };
  }
  function renderFocus() {
    var b = body("focus"), f = S.focus;
    if (!f) return empty(b, "No recommendation yet. Muse hasn't chosen one.");
    var l = focusLink(), th = l.thread;
    add(b, h("div", "focus-title", f.title), f.reason ? h("div", "focus-reason", f.reason) : null);
    if (th && th.next_step && !closed(th)) add(b, add(h("div", "s next"), h("b", null, "Next: "), document.createTextNode(th.next_step)));
    var meta = h("div", "focus-meta"), n = 0;
    if (th) { add(meta, add(h("span"), document.createTextNode("Project "), h("b", null, th.title))); n++; }
    else { var name = ""; S.projects.forEach(function (p) { if (p.id === f.project) name = p.name; }); if (name) { add(meta, add(h("span"), document.createTextNode("Project "), h("b", null, name))); n++; } }
    if (f.deadline) { add(meta, add(h("span"), document.createTextNode("Due "), h("b", null, fmtDate(f.deadline) + (fmtTime(f.deadline) ? " · " + fmtTime(f.deadline) : "")))); n++; }
    if (f.basis === "recommended") { add(meta, h("span", null, "Muse recommendation")); n++; }
    if (n) b.appendChild(meta);
    var mine = S.queue.filter(function (q) { return vis(q) && isRecommended(q) && (q.status === "active" || q.status === "blocked") && q.plan_state !== "today"; });
    mine.slice(0, 2).forEach(function (q) { add(b, add(h("div", "s next"), h("b", null, "In progress: "), document.createTextNode(q.title))); });
    if (th) { var bs = h("div", "btns"); add(bs, openBtn(th.id)); b.appendChild(bs); }
  }
  function tlink(text, tid) {
    if (!tid || !thr(tid)) return h("span", null, text);
    var a = h("a", "tlink", text); a.href = "#thread=" + tid; a.setAttribute("data-ui", "open-thread"); a.setAttribute("data-thread", tid); return a;
  }
  function openBtn(tid, label) { var x = h("button", "btn primary", label || "Open"); x.type = "button"; x.setAttribute("data-ui", "open-thread"); x.setAttribute("data-thread", tid); return x; }

  function askCard(i) {
    var c = h("div", "ask" + (i.sensitive ? " sens" : ""));
    var top = h("div", "row2"); add(top, h("div", "t", i.title), i.sensitive ? chip("Sensitive · confirm here only", "red") : null);
    add(c, top, i.detail ? h("div", "s", i.detail) : null);
    var bs = h("div", "btns");
    if (i.status === "open" || i.status === "acknowledged") {
      if (i.options && i.options.length) i.options.forEach(function (o) { add(bs, btn(o, "choose", "inbox", i.id, { option: o })); });
      else if (i.kind === "confirm") add(bs, btn("Confirm", "confirm", "inbox", i.id, null, "primary"), btn("Not me / decline", "decline", "inbox", i.id, null, i.sensitive ? "warn" : ""));
      else if (i.kind === "alert") add(bs, btn("Acknowledge", "ack", "inbox", i.id, null, "primary"));
      else if (i.kind === "todo") add(bs, btn("Done", "resolve", "inbox", i.id, null, "primary"));
      else add(bs, btn("Resolved", "resolve", "inbox", i.id, null, "primary"));
    }
    if (!actionsOn()) add(c, h("div", "more", "Read-only: actions are not enabled on this server."));
    if (bs.childNodes.length) c.appendChild(bs);
    return c;
  }
  function renderNeeds() {
    var b = body("needs-you");
    var open = S.inbox.filter(function (i) { return vis(i) && (i.status === "open" || i.status === undefined); });
    document.getElementById("needs-you").hidden = !open.length;
    if (!open.length) return;
    open = open.slice().sort(function (a, c) { return (c.sensitive ? 1 : 0) - (a.sensitive ? 1 : 0); });
    open.slice(0, 5).forEach(function (i) { b.appendChild(askCard(i)); });
    if (open.length > 5) {
      var d = details("needs-more", "more", (open.length - 5) + " more");
      open.slice(5).forEach(function (i) { d.appendChild(askCard(i)); }); b.appendChild(d);
    }
  }

  var HINT = { morning: "Morning", afternoon: "Afternoon", evening: "Evening", tonight: "Tonight" }, HORD = { morning: 0, afternoon: 1, evening: 2, tonight: 3 };
  function plannedList(state) {
    return S.queue.filter(function (q) { return vis(q) && !closed(q) && q.plan_state === state; })
      .sort(function (a, c) { return (HORD[a.time_hint] === undefined ? 4 : HORD[a.time_hint]) - (HORD[c.time_hint] === undefined ? 4 : HORD[c.time_hint]) || pr(a.priority) - pr(c.priority) || (a.order || 99) - (c.order || 99); });
  }
  function prow(q, missed) {
    var r = h("div", "item"), m = h("div", "main"), th = threadTitle(q);
    var sub = [th && th !== q.title ? th : "", missed ? "Planned " + fmtDate(q.planned_for) : "", line(q.note)].filter(Boolean).join(" · ");
    add(m, h("div", "t", q.title), sub ? h("div", "s", sub) : null);
    var side = h("div", "side");
    if (q.time_hint && !missed) add(side, h("div", "tag", HINT[q.time_hint]));
    if (q.status === "blocked") add(side, h("div", "state red", "Blocked")); else if (q.status === "waiting") add(side, h("div", "state amber", "Waiting"));
    var db = doneBtn("queue", q); if (db) add(side, db);
    add(r, m, side); return r;
  }
  function qrow(q) {
    var wrap = h("div", "card-row"), r = h("div", "item"), m = h("div", "main"), th = threadTitle(q), key = "q:" + q.id;
    var sub = [th && th !== q.title ? th : "", line(q.note)].filter(Boolean).join(" · ");
    add(m, h("div", "t", q.title), sub ? h("div", "s", sub) : null);
    var side = h("div", "side");
    if (q.status === "blocked") add(side, h("div", "state red", "Blocked"));
    var db = doneBtn("queue", q); if (db) add(side, db);
    var items = negItems("task", q); if (items.length && actionsOn()) add(side, menuBtn(key));
    add(r, m, side); wrap.appendChild(r);
    if (menuOpen === key && items.length) wrap.appendChild(menuPanel(items, "Marks it as something you are not doing. Nothing is deleted or cancelled outside."));
    return wrap;
  }
  function isRecommended(q) {     // links only (focus.task / focus.project): never title similarity
    if (!S.focus) return false; var l = focusLink();
    return !!((l.task && q.id === l.task.id) || (!l.task && l.thread && q.thread === l.thread.id));
  }
  function renderMotion() {
    var b = body("in-progress");
    var live = S.queue.filter(function (q) { return vis(q) && q.plan_state !== "today" && !isRecommended(q) && (q.status === "active" || q.status === "blocked") && !(q.thread && thr(q.thread) && closed(thr(q.thread))); });
    live.sort(function (a, c) { return pr(a.priority) - pr(c.priority) || (a.order || 99) - (c.order || 99); });
    var wait = S.queue.filter(function (q) { return vis(q) && q.plan_state !== "today" && q.status === "waiting"; });
    document.getElementById("in-progress").hidden = !live.length && !wait.length;
    if (!live.length && !wait.length) return;
    live.slice(0, 6).forEach(function (q) { b.appendChild(qrow(q)); });
    if (live.length > 6) {
      var d = details("motion-more", "more", (live.length - 6) + " more in motion");
      live.slice(6).forEach(function (q) { d.appendChild(qrow(q)); }); b.appendChild(d);
    }
    if (wait.length) {
      var w = details("motion-wait", "more", "Waiting (" + wait.length + ")");
      wait.forEach(function (q) { w.appendChild(qrow(q)); }); b.appendChild(w);
    }
  }

  // ---------- unified timeline (deadlines + events + reminders, de-duplicated by thread + instant) ----------
  var CERT = { unconfirmed: 0, tentative: 1, unknown: 1, confirmed: 2 };
  function timeline() {
    var n = nowS(), raw = [];
    S.deadlines.forEach(function (d) { if (vis(d) && d.status !== "done" && d.status !== "dropped") raw.push({ kind: "Deadline", rec: d, sec: "deadlines", ts: d.when_ts, dateOnly: d.due.length === 10, rank: 1 }); });
    S.schedule.forEach(function (e) { if (vis(e) && !e.past && e.status !== "cancelled") raw.push({ kind: "Event", rec: e, sec: "schedule", ts: e.when_ts, dateOnly: e.start.length === 10, rank: 0 }); });
    S.automations.forEach(function (a) { if (vis(a) && a.kind === "once" && a.when_ts && a.when_ts > n - 3600) raw.push({ kind: "Reminder", rec: a, sec: "automations", ts: a.when_ts, rank: 2 }); });
    S.queue.forEach(function (q) {            // planned days (no clock time is ever invented)
      if (vis(q) && !closed(q) && q.plan_state) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(q.planned_for); raw.push({ kind: "Plan", rec: q, sec: "queue", ts: wallToEpoch(+m[1], +m[2], +m[3], 0, 0), dateOnly: true, rank: 3 }); }
    });
    raw.sort(function (a, c) { return a.ts - c.ts || a.rank - c.rank; });
    var out = [], byKey = {};
    raw.forEach(function (x) {
      var k = (x.rec.thread || x.rec.id) + "@" + Math.floor(x.ts / 60);
      if (x.kind === "Plan") { out.push({ kind: "Plan", rec: x.rec, sec: x.sec, ts: x.ts, dateOnly: true, also: [], reminders: [] }); return; }
      if (x.kind !== "Reminder" && byKey[k]) { var p = byKey[k]; p.also.push(x); return; }
      var item = { kind: x.kind, rec: x.rec, sec: x.sec, ts: x.ts, dateOnly: x.dateOnly, also: [], reminders: [] };
      if (x.kind !== "Reminder") byKey[k] = item; out.push(item);
    });
    // fold reminders into the event/deadline of the same thread that follows within 36h
    var res = [];
    out.forEach(function (x) {
      if (x.kind === "Reminder" && x.rec.thread && !remInfo(x.rec).stopped && !remInfo(x.rec).tag) {
        var host = null;
        out.forEach(function (y) { if (y.kind !== "Reminder" && y.kind !== "Plan" && y.rec.thread === x.rec.thread && y.ts >= x.ts && y.ts - x.ts <= 36 * 3600 && (!host || y.ts < host.ts)) host = y; });
        if (host) { host.reminders.push(x); return; }
      }
      res.push(x);
    });
    res.forEach(function (x) {   // conservative certainty across merged sources
      var all = [x].concat(x.also); var c = 9;
      all.forEach(function (y) { if (y.sec === "schedule") c = Math.min(c, CERT[y.rec.status] === undefined ? 1 : CERT[y.rec.status]); });
      x.cert = c === 9 ? null : c;
    });
    return res;
  }
  function bucket(x, n) {
    var d = dayNum(x.ts) - dayNum(n);
    if (x.ts < n && (x.kind === "Deadline" || (x.kind === "Plan" && x.rec.plan_state === "overdue"))) return "Overdue";
    if (d <= 0) return "Today"; if (d === 1) return "Tomorrow"; if (d <= 7) return "This week"; if (d <= 30) return "Next 30 days"; if (d <= 90) return "Later"; return "Horizon";
  }
  function tlRow(x) {
    var wrap = h("div", "card-row"), r = h("div", "tl"), n = nowS(), key = "tl:" + x.sec + ":" + x.rec.id;
    add(r, h("div", "when", fmtTs(x.ts, x.dateOnly)));
    var m = h("div"), t = h("div", "t", x.rec.title || x.rec.name);
    t.appendChild(h("span", "chip2", x.kind));
    var fl = S.focus ? focusLink() : null;
    if (fl && ((fl.task && x.rec.id === fl.task.id && x.sec === "queue") || (fl.thread && x.rec.thread === fl.thread.id))) t.appendChild(h("span", "chip2 rec", "Recommended"));
    var th = threadTitle(x.rec), subs = [];
    if (x.kind === "Plan" && x.rec.time_hint) subs.push(HINT[x.rec.time_hint]);
    if (th) subs.push(th);
    if (x.kind === "Reminder") { var ri = remInfo(x.rec); if (ri.stopped) subs.push("Stopped"); if (ri.tag) subs.push(ri.tag); }
    if (x.kind === "Event" && x.rec.attendance === "not_going") subs.push("Not attending");
    else if (x.rec.status === "unconfirmed" || x.cert === 0) subs.push("Not confirmed"); else if (x.rec.status === "tentative") subs.push("Tentative");
    x.reminders.forEach(function (y) { if (y.ts !== x.ts) subs.push("Reminder " + fmtTs(y.ts, false).split(" · ").pop()); });
    if (x.kind === "Deadline" && x.rec.basis === "unknown" && x.rec.due.length === 10) subs.push("Date not confirmed");
    var pn = pendingNote(x.sec, x.rec.id); if (pn) subs.push(pn);
    add(m, t, subs.length ? h("div", "sub" + (x.kind === "Reminder" && remInfo(x.rec).bad ? " red" : ""), subs.join(" · ")) : null); r.appendChild(m);
    var side = h("div", "side"), items = [], note = "";
    if (x.kind === "Plan") { add(side, h("div", x.rec.plan_state === "overdue" ? "red" : "tag", x.rec.plan_state === "overdue" ? "Missed plan" : "Planned")); items = negItems("task", x.rec); }
    else if (x.ts < n && x.kind === "Deadline") add(side, h("div", "red", "Overdue " + short(n - x.ts)));
    else add(side, h("div", null, "in " + short(x.ts - n)));
    if (x.kind === "Event") { items = negItems("event", x.rec); note = x.rec.attendance === "not_going" ? "" : NOTE_ATT; }
    if (x.kind === "Reminder") { items = negItems("reminder", x.rec); note = "A request to Muse: it stops its own scheduled reminder and confirms here. Nothing else changes."; }
    if (x.kind === "Deadline") { var db = doneBtn("deadlines", x.rec); if (db) add(side, db); }
    if (x.kind === "Plan") { var pb = doneBtn("queue", x.rec); if (pb) add(side, pb); }
    if (items.length && actionsOn()) add(side, menuBtn(key));
    r.appendChild(side); wrap.appendChild(r);
    if (menuOpen === key && items.length) wrap.appendChild(menuPanel(items, note));
    return wrap;
  }
  function renderUpcoming() {
    var sec = document.getElementById("upcoming"), b = body("upcoming"), n = nowS();
    var items = timeline().filter(function (x) { return x.ts < n + 7 * DAY && !(x.kind === "Plan" && x.rec.plan_state === "overdue") && !(x.kind === "Event" && x.rec.attendance === "not_going") && !(x.kind === "Reminder" && remInfo(x.rec).stopped); });
    var missed = plannedList("overdue");
    sec.hidden = !items.length && !missed.length;
    if (sec.hidden) return;
    var lastB = "";
    items.slice(0, 8).forEach(function (x) { var k = bucket(x, n); if (k !== lastB) { b.appendChild(h("div", "day", k)); lastB = k; } b.appendChild(tlRow(x)); });
    if (items.length > 8) b.appendChild(h("div", "more", (items.length - 8) + " more — see Timeline"));
    if (missed.length) { var mm = details("today-missed", "more", "Missed plans (" + missed.length + ")"); missed.forEach(function (q) { mm.appendChild(prow(q, true)); }); b.appendChild(mm); }
  }
  function renderTimeline() {
    var b = body("timeline-full"), n = nowS(), items = timeline();
    if (tzLabel()) b.appendChild(h("div", "tznote", "All times are " + tzLabel() + (S.tz ? " (" + S.tz + ")" : "") + "."));
    if (!items.length) return empty(b, "Nothing scheduled.");
    var lastB = "";
    items.forEach(function (x) { var k = bucket(x, n); if (k !== lastB) { b.appendChild(h("div", "day", k)); lastB = k; } b.appendChild(tlRow(x)); });
  }

  // ---------- AREAS: threads grouped by life area ----------
  function related(t) {
    var id = t.id, f = function (r) { return r.thread === id; };
    return { queue: S.queue.filter(f), deadlines: S.deadlines.filter(f), schedule: S.schedule.filter(f), automations: S.automations.filter(f),
             projects: S.projects.filter(f), inbox: S.inbox.filter(f) };
  }
  function nextDate(rel) {
    var n = nowS();
    function pick(list) {
      var best = null;
      list.forEach(function (r) {
        if (!vis(r) || closed(r) || r.past || r.status === "cancelled" || !r.when_ts || r.when_ts < n) return;
        if (r.basis === "unknown" && (r.due || "").length === 10) return;     // placeholder date: never shown as real
        if (!best || r.when_ts < best.ts) best = { ts: r.when_ts, dateOnly: (r.due || r.start || "").length === 10 };
      });
      return best;
    }
    return pick(rel.deadlines.concat(rel.schedule)) || pick(rel.automations.filter(function (a) { return a.kind === "once"; }));
  }
  // ---------- Muse-declared actions (ui_actions): local multi-step selection, one submit ----------
  var UA = {};                                   // "thread|action" -> { step, sel } (browser-local; nothing is sent until Confirm)
  function latestAct(tid, uid) {
    for (var i = 0; i < S.actions.length; i++) { var a = S.actions[i]; if (a.type === "ui_action" && a.target === tid && a.params && a.params.ui_action === uid) return a; }
    return null;
  }
  function uaPicked(a) { var l = a.params && a.params.selection_labels; return l ? Object.keys(l).map(function (k) { return l[k]; }).join(" · ") : ""; }
  function uaButton(label, attrs, cls) {
    var b = h("button", "btn" + (cls ? " " + cls : ""), label); b.type = "button";
    for (var k in attrs) b.setAttribute(k, attrs[k]); return b;
  }
  function uaBlock(t) {
    var list = (t.ui_actions || []).filter(function (u) { return !u.expired; });
    if (!list.length || !actionsOn() || closed(t)) return null;
    var box = h("div", "ua");
    list.forEach(function (u) {
      var key = t.id + "|" + u.id, a = latestAct(t.id, u.id), row = h("div", "ua-row"), st = UA[key], steps = u.steps || [];
      if (a && (a.status === "accepted" || a.status === "seen")) {          // Accepted: stored by the backend, waiting for Muse
        delete UA[key];
        add(row, h("div", "t", u.label), h("div", "tag amber", "已提交 · " + (a.status === "seen" ? "Muse 已读取，处理中" : "等待 Muse 处理") + (uaPicked(a) ? " · " + uaPicked(a) : "")));
      } else if (!steps.length) {
        add(row, uaButton(u.label, { "data-act": "ui_action", "data-section": "threads", "data-id": t.id, "data-ua": u.id, "data-selection": "{}" }, u.sensitive ? "warn" : ""));
      } else if (!st) {
        add(row, uaButton(u.label, { "data-ui": "ua-start", "data-thread": t.id, "data-ua": u.id }, u.sensitive ? "warn" : ""));
      } else if (st.step < steps.length) {                                    // Pending: choosing locally
        var s0 = steps[st.step];
        add(row, h("div", "t", u.label + "  " + (st.step + 1) + "/" + (steps.length + 1)), h("div", "s", s0.prompt));
        var bs = h("div", "btns");
        s0.choices.forEach(function (c) { add(bs, uaButton(c.label, { "data-ui": "ua-pick", "data-thread": t.id, "data-ua": u.id, "data-value": c.value })); });
        add(bs, uaButton("取消", { "data-ui": "ua-cancel", "data-thread": t.id, "data-ua": u.id })); row.appendChild(bs);
      } else {                                                                // final local step: confirm
        var picked = steps.map(function (sp) { var c = sp.choices.filter(function (x) { return x.value === st.sel[sp.id]; })[0]; return c ? c.label : ""; }).join(" · ");
        add(row, h("div", "t", u.label + "  " + (steps.length + 1) + "/" + (steps.length + 1)), h("div", "s", "确认提交：" + picked));
        var bs2 = h("div", "btns");
        add(bs2, uaButton("确认", { "data-act": "ui_action", "data-section": "threads", "data-id": t.id, "data-ua": u.id, "data-selection": JSON.stringify(st.sel) }, "primary"),
            uaButton("返回", { "data-ui": "ua-back", "data-thread": t.id, "data-ua": u.id }), uaButton("取消", { "data-ui": "ua-cancel", "data-thread": t.id, "data-ua": u.id })); row.appendChild(bs2);
      }
      if (a && a.status !== "accepted" && a.status !== "seen" && a.result_at && (Date.parse(S.server_time) - Date.parse(a.result_at)) < DAY * 1000) {   // Completed
        add(row, h("div", "tag " + (a.status === "completed" ? "green" : "amber"), (a.status === "completed" ? "✓ 已完成" : a.status === "declined" ? "Muse 已拒绝" : "Muse 无法完成") + (a.note ? "：" + a.note : "")));
      }
      box.appendChild(row);
    });
    return box;
  }
  function impactOfThread(tid) {
    var o = { tasks: 0, waiting: 0, deadlines: 0, events: 0, reminders: 0, routines: 0 }, n = nowS();
    function mine(r) { return r.thread === tid && vis(r); }
    S.queue.forEach(function (q) { if (mine(q) && !closed(q)) { o.tasks++; if (q.status === "waiting") o.waiting++; } });
    S.deadlines.forEach(function (d) { if (mine(d) && (d.status === "open" || d.status === "unknown" || !d.status)) o.deadlines++; });
    S.schedule.forEach(function (e) { if (mine(e) && !e.past && e.status !== "cancelled") o.events++; });
    S.automations.forEach(function (a) { if (mine(a) && a.status !== "paused") { if (a.kind === "once") { if (a.when_ts && a.when_ts > n) o.reminders++; } else o.routines++; } });
    return o;
  }
  function impactText(o) {
    var p = [];
    if (o.tasks) p.push(o.tasks + (o.tasks === 1 ? " open task" : " open tasks") + (o.waiting ? " (" + o.waiting + " waiting)" : ""));
    if (o.deadlines) p.push(o.deadlines + (o.deadlines === 1 ? " formal deadline" : " formal deadlines"));
    if (o.events) p.push(o.events + (o.events === 1 ? " booked event" : " booked events"));
    if (o.reminders) p.push(o.reminders + (o.reminders === 1 ? " reminder" : " reminders"));
    if (o.routines) p.push(o.routines + (o.routines === 1 ? " routine" : " routines"));
    return p.join(" · ");
  }
  function threadCard(t, noId) {
    var rel = related(t), nx = nextDate(rel), imp = impactOfThread(t.id);
    var d = details("thread:" + t.id, "thread"); if (!noId) d.id = "thread-" + t.id;
    var sm = d.querySelector("summary"), top = h("div", "thr-top");
    var left = h("div", "t", t.title);
    var tags = h("div", "chips");
    if (t.stage) add(tags, chip(t.stage, "green")); else if (t.status !== "active") add(tags, chip({ waiting: "Waiting", paused: "Paused", done: "Archived", dropped: "Dropped" }[t.status] || t.status));
    if (closed(t) && imp.tasks - imp.waiting > 0) add(tags, chip((imp.tasks - imp.waiting) + " open", "amber"));
    else if (closed(t) && imp.waiting) add(tags, chip("tracking " + imp.waiting));
    var nUa = actionsOn() ? (t.ui_actions || []).filter(function (u) { return !u.expired; }).length : 0;
    if (nUa) add(tags, chip("● " + nUa + " 可操作", "amber"));
    add(top, left, nx ? chip(fmtTs(nx.ts, nx.dateOnly), "") : tags);
    add(sm, top, (t.summary || t.next_step) ? h("div", "thr-sum", line(t.summary || t.next_step)) : null);
    if (nx && (t.stage || t.status !== "active")) top.appendChild(tags);
    // body
    if (t.stages && t.stages.length) {
      var st = h("div", "stages");
      t.stages.forEach(function (s) {
        var on = s === t.stage, b2 = actionsOn() ? btn(s, "set_stage", "threads", t.id, { stage: s }) : h("span", "st" + (on ? " on" : ""), s);
        if (actionsOn()) b2.setAttribute("aria-pressed", on ? "true" : "false");
        st.appendChild(b2);
      });
      d.appendChild(st);
    }
    var uab = uaBlock(t); if (uab) d.appendChild(uab);
    if (t.summary) d.appendChild(h("div", "s", t.summary));
    if (t.next_step) d.appendChild(add(h("div", "s"), h("b", null, "Next: "), document.createTextNode(t.next_step)));
    if (closed(t) && impactText(imp)) d.appendChild(h("div", "note-box", (imp.tasks - imp.waiting > 0 ? "Archived, but still attached: " : "Archived. Muse keeps tracking: ") + impactText(imp) + ". Reminders, deadlines and routines keep running; waiting tasks stay tracked."));
    var openQ = rel.queue.filter(function (q) { return vis(q) && !closed(q); }), box = h("div", "rel");
    function relRow(text, primary, items, key, note) {
      var w = h("div", "card-row rel-row"), r2 = h("div", "it"), side = h("div", "side");
      add(r2, h("span", null, text)); add(side, primary); if (items.length && actionsOn()) add(side, menuBtn(key)); r2.appendChild(side); w.appendChild(r2);
      if (menuOpen === key && items.length) w.appendChild(menuPanel(items, note)); return w;
    }
    openQ.forEach(function (q) { var items = negItems("task", q); box.appendChild(relRow(q.title + (q.status === "waiting" ? " · waiting" : q.status === "blocked" ? " · blocked" : "") + (q.planned_for ? " · planned " + fmtDate(q.planned_for) + (q.time_hint ? " (" + HINT[q.time_hint] + ")" : "") : ""), doneBtn("queue", q), items, "q:" + q.id, "Marks it as something you are not doing. Nothing is deleted or cancelled outside.")); });
    rel.deadlines.filter(function (x) { return vis(x) && !closed(x); }).forEach(function (x) { box.appendChild(itRow(x.title + " — " + fmtDate(x.due), doneBtn("deadlines", x))); });
    rel.schedule.filter(function (x) { return vis(x) && !x.past && x.status !== "cancelled"; }).forEach(function (x) {
      var pn = pendingNote("schedule", x.id);
      box.appendChild(relRow(x.title + " — " + fmtTs(x.when_ts, x.start.length === 10) + (x.attendance === "not_going" ? " · not attending" : x.status === "unconfirmed" ? " · not confirmed" : "") + (pn ? " · " + pn : ""), null, negItems("event", x), "ev:" + x.id, x.attendance === "not_going" ? "" : NOTE_ATT)); });
    rel.automations.filter(function (x) { return vis(x) && x.kind === "once" && x.when_ts > nowS(); }).forEach(function (x) {
      var ri = remInfo(x);
      box.appendChild(relRow("Reminder — " + fmtTs(x.when_ts, false) + (ri.stopped ? " · stopped" : "") + (ri.tag ? " · " + ri.tag : ""), null, negItems("reminder", x), "rm:" + x.id, "A request to Muse: it stops its own scheduled reminder and confirms here. Nothing else changes.")); });
    if (box.childNodes.length) d.appendChild(box);
    var src = [];
    ["queue", "deadlines", "schedule", "automations", "projects", "inbox"].forEach(function (k) { rel[k].forEach(function (r) { src.push(k + ":" + r.id + (r.merged_into ? " (merged → " + r.merged_into + ")" : "")); }); });
    if (src.length) d.appendChild(h("div", "src", "Sources: " + src.join(" · ")));
    var acts = S.actions.filter(function (a) { return rel.queue.concat(rel.deadlines, rel.inbox).some(function (r) { return r.id === a.target; }) || a.target === t.id; }).slice(0, 2);
    if (acts.length) d.appendChild(h("div", "src", "You: " + acts.map(function (a) { return a.type.replace("_", " ") + " " + a.target + " (" + ago(a.ts) + ")"; }).join(" · ")));
    var more = btn(closed(t) ? "Reopen" : "Archive thread…", closed(t) ? "reopen" : "done", "threads", t.id);
    if (more) { var mn = h("details", "sec"), ms = h("summary", null, "···"); ms.setAttribute("aria-label", "More"); add(mn, ms, more); d.appendChild(mn); }
    return d;
  }
  function renderMatters() {
    var b = body("matters"), any = false;
    S.areas.filter(function (a) { return a.id !== "system"; }).concat([{ id: "", label: "Unsorted" }]).forEach(function (a) {
      var ths = S.threads.filter(function (t) { return (t.area || "") === a.id && !closed(t); });
      var orphans = ["queue", "deadlines", "schedule", "projects"].reduce(function (acc, k) {
        return acc.concat(S[k].filter(function (r) { return vis(r) && !r.thread && areaOf(r) === a.id && !closed(r) && !r.past; }).map(function (r) { return { k: k, r: r }; }));
      }, []);
      if (!ths.length && !orphans.length) return;
      any = true;
      var d = details("area:" + a.id, "area", null), s2 = d.querySelector("summary");
      add(s2, h("span", null, a.label), h("span", "n", ths.length + (ths.length === 1 ? " thread" : " threads")));
      ths.forEach(function (t) { d.appendChild(threadCard(t)); });
      if (orphans.length) {
        var box = h("div", "rel"); box.appendChild(h("div", "grp", "Not yet grouped"));
        orphans.forEach(function (o) { box.appendChild(itRow(o.r.title || o.r.name)); });
        d.appendChild(box);
      }
      b.appendChild(d);
    });
    var arch = S.threads.filter(function (t) { return closed(t) && t.area !== "system"; }), recs = [], merged = 0;
    ["queue", "deadlines", "projects", "inbox"].forEach(function (k) { S[k].forEach(function (r) { if (r.merged_into) merged++; else if ((closed(r) || ["confirmed", "declined", "resolved", "acknowledged"].indexOf(r.status) >= 0) && !(r.thread && thr(r.thread) && closed(thr(r.thread)))) recs.push({ k: k, r: r }); }); });
    ["schedule", "automations"].forEach(function (k) { S[k].forEach(function (r) { if (r.merged_into) merged++; }); });
    if (arch.length || recs.length || merged) {
      any = true;
      var withOpen = arch.filter(function (t) { var i = impactOfThread(t.id); return i.tasks - i.waiting > 0; }).length;
      var ad = details("archived", "area", null);
      add(ad.querySelector("summary"), h("span", null, "Archived" + (withOpen ? "  ⚠ " + withOpen + " with open tasks" : "")), h("span", "n", String(arch.length)));
      arch.sort(function (x, y) { var a1 = impactOfThread(x.id), b1 = impactOfThread(y.id); return (b1.tasks - b1.waiting) - (a1.tasks - a1.waiting); }).forEach(function (t) { ad.appendChild(threadCard(t)); });
      if (recs.length) {
        var rd = details("arch-recs", "more", "Other completed items (" + recs.length + ")");
        recs.forEach(function (o) {
          var back = (o.k === "queue" || o.k === "deadlines") ? btn(o.r.status === "dropped" ? "Restore" : "Reopen", "reopen", o.k, o.r.id) : null;
          add(rd, add(h("div", "kvl"), h("span", null, o.r.title || o.r.name), add(h("span", "side"), h("span", "tag", o.r.status === "dropped" ? "not doing" : o.r.status), back)));
        }); ad.appendChild(rd);
      }
      if (merged) ad.appendChild(h("div", "more", merged + " duplicate record(s) are merged into others and kept for history."));
      b.appendChild(ad);
    }
    if (!any) empty(b, "No threads yet. Muse organises everything into threads and areas.");
  }

  // ---------- locate: Open on the recommendation expands the same thread ----------
  function openThread(id) {
    var t = thr(id); if (!t) return;
    if (t.area === "system") { openKeys["thread:" + id] = true; saveOpen(); view = "system"; showView(); render(); return; }
    openKeys[closed(t) ? "archived" : "area:" + (t.area || "")] = true; openKeys["thread:" + id] = true; saveOpen();
    view = "home"; showView(); render();
    var el = document.getElementById("thread-" + id);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("flash"); setTimeout(function () { el.classList.remove("flash"); }, 1800); }
  }

  // ---------- 7 DAYS: one column per day (read-only; derived from the same items as the Timeline) ----------
  var weekStart = null;
  function addDays(d, n) { var p = d.split("-"); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] + n)).toISOString().slice(0, 10); }
  function tsDate(ts) { var p = tzParts(ts); return p.year + "-" + p.month + "-" + p.day; }
  function dayLabel(d) { var p = d.split("-"); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2], 12)).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }); }
  function weekItemDate(x) {
    if (x.kind === "Plan") return x.rec.planned_for;
    if (x.dateOnly) return (x.rec.due || x.rec.start || "").slice(0, 10);
    return tsDate(x.ts);
  }
  function weekItem(x) {        // no clock times here: order within a day is chronological, the day is what matters
    var muted = (x.kind === "Event" && x.rec.attendance === "not_going");
    var el = h("div", "wk-item " + x.kind.toLowerCase() + (muted ? " muted" : "")), t = h("div", "wk-time");
    t.textContent = x.kind === "Deadline" ? "Due" : x.kind === "Plan" ? "Plan" + (x.rec.time_hint ? " · " + HINT[x.rec.time_hint] : "") : x.kind;
    var title = h("div", "wk-title"); title.appendChild(tlink(x.rec.title || x.rec.name, x.rec.thread));
    add(el, t, title);
    var tags = [];
    if (muted) tags.push("Not attending");
    else if (x.rec.status === "unconfirmed" || x.cert === 0) tags.push("Not confirmed"); else if (x.rec.status === "tentative") tags.push("Tentative");
    if (x.reminders.length) tags.push(x.reminders.length === 1 ? "has a reminder" : x.reminders.length + " reminders");
    if (tags.length) el.appendChild(h("div", "wk-tags", tags.join(" · ")));
    return el;
  }
  function renderWeek() {
    var b = body("week-grid"), today = S.today, start = weekStart || today;
    if (start < today) start = today;                       // past items are not kept; the view starts today at the earliest
    var nav = h("div", "wk-nav"), range = dayLabel(start) + " – " + dayLabel(addDays(start, 6));
    function nb(label, ui, off) { var x = h("button", "btn", label); x.type = "button"; x.setAttribute("data-ui", ui); if (off) x.disabled = true; return x; }
    add(nav, nb("← Previous", "week-prev", start <= today), nb("Today", "week-today", start === today), nb("Next →", "week-next", false), h("span", "wk-range", range + (tzLabel() ? " · " + tzLabel() : "")));
    b.appendChild(nav);
    var by = {}, items = timeline().filter(function (x) { return !(x.kind === "Reminder" && remInfo(x.rec).stopped) && !(x.kind === "Plan" && x.rec.plan_state === "overdue"); });
    items.forEach(function (x) { var d = weekItemDate(x); (by[d] = by[d] || []).push(x); });
    var grid = h("div", "wk"); grid.id = "week-days";
    for (var i = 0; i < 7; i++) {
      var d = addDays(start, i), col = h("div", "wk-day" + (d === today ? " today" : "")); col.setAttribute("data-date", d);
      col.appendChild(h("div", "wk-h", dayLabel(d) + (d === today ? " · Today" : "")));
      var list = (by[d] || []).slice().sort(function (a, c) { var ad = (a.kind === "Plan" || a.dateOnly) ? 0 : 1, cd = (c.kind === "Plan" || c.dateOnly) ? 0 : 1; return ad - cd || a.ts - c.ts || a.rank - c.rank; });
      if (!list.length) col.appendChild(h("div", "wk-empty", "—"));
      list.forEach(function (x) { col.appendChild(weekItem(x)); });
      grid.appendChild(col);
    }
    b.appendChild(grid);
    b.appendChild(h("div", "more", "Read-only. No clock times here: items are in chronological order within each day. Routines are not shown. Exact times are on the Timeline."));
  }

  // ---------- LOG ----------
  function sentence(msg) { var m = /^(.{8,140}?[。！？.!?；;：:])\s*/.exec(msg); return m ? m[1] : (msg.length > 120 ? msg.slice(0, 117) + "…" : msg); }
  function renderLog() {
    var b = body("log");
    var tabs = h("div", "tabs");
    [["digest", "Digest"], ["decision", "Decisions"], ["alert", "Alerts"], ["activity", "Activity"]].forEach(function (t) {
      var x = h("button", "btn", t[1]); x.type = "button"; x.setAttribute("data-ui", "log-tab"); x.setAttribute("data-tab", t[0]); x.setAttribute("aria-pressed", logTab === t[0] ? "true" : "false"); tabs.appendChild(x);
    });
    b.appendChild(tabs);
    var list = S.journal.filter(function (j) { return !TEST_RE.test(j.message); });
    var rows;
    if (logTab === "activity") {
      rows = list.filter(function (j) { return j.kind !== "digest" && j.kind !== "alert" && j.kind !== "decision"; }).map(function (j) { return { ts: j.ts, title: j.title, msg: j.message, who: "Muse" }; });
      S.actions.forEach(function (a) { rows.push({ ts: a.ts, msg: "You: " + a.type.replace("_", " ") + " · " + a.section + ":" + a.target + (a.seen ? "" : " (Muse hasn't acted on this yet)"), who: "You" }); });
      rows.sort(function (a, c) { return Date.parse(c.ts) - Date.parse(a.ts); });
    } else rows = list.filter(function (j) { return j.kind === logTab; }).map(function (j) { return { ts: j.ts, title: j.title, msg: j.message, who: "Muse" }; });
    if (!rows.length) return empty(b, { digest: "No daily digests yet — Muse posts one after the morning email read.", decision: "No decisions recorded.", alert: "No alerts.", activity: "No activity." }[logTab]);
    rows.slice(0, logLimit).forEach(function (r) {
      var e = h("div", "entry"), head = r.title || sentence(r.msg);
      add(e, h("div", "h", head), h("div", "t2", ago(r.ts)));
      var rest = r.title ? r.msg : r.msg.slice(head.length === r.msg.length ? r.msg.length : r.msg.indexOf(head) + head.length).trim();
      if (rest && rest !== head) { var d = details("log:" + r.ts + ":" + head.slice(0, 20), "", "Details"); d.appendChild(h("div", null, rest)); e.appendChild(d); }
      b.appendChild(e);
    });
    if (rows.length > logLimit) { var m = h("button", "btn", "Show more (" + (rows.length - logLimit) + ")"); m.type = "button"; m.setAttribute("data-ui", "log-more"); b.appendChild(m); }
  }

  // ---------- SYSTEM ----------
  function renderRoutines() {
    var b = body("routines"), a = S.automations.filter(function (x) { return vis(x) && x.kind !== "once"; });
    if (!a.length) return empty(b, "No recurring routines recorded.");
    function tod(x) { if (!x.when_ts) return "Unscheduled"; var hr = new Date(x.when_ts * 1000).getHours(); return hr < 12 ? "Morning" : hr < 18 ? "Afternoon" : "Evening"; }
    var groups = [["Always on", function (x) { return x.kind === "always"; }]];
    ["Morning", "Afternoon", "Evening", "Unscheduled"].forEach(function (g) { groups.push([g + " (daily)", function (x) { return x.kind === "daily" && tod(x) === g; }]); });
    groups.push(["Weekly", function (x) { return x.kind === "weekly"; }], ["Monthly", function (x) { return x.kind === "monthly"; }], ["Other", function (x) { return !x.kind; }]);
    groups.forEach(function (g) {
      var items = a.filter(g[1]).sort(function (x, y) { return (x.when_ts ? new Date(x.when_ts * 1000).getHours() * 60 + new Date(x.when_ts * 1000).getMinutes() : 9999) - (y.when_ts ? new Date(y.when_ts * 1000).getHours() * 60 + new Date(y.when_ts * 1000).getMinutes() : 9999); });
      if (!items.length) return;
      b.appendChild(h("div", "grp", g[0]));
      items.forEach(function (x) {
        var r = h("div", "kvl"), l = h("span"); add(l, h("span", null, x.name), x.status === "failed" ? chip(" · failed", "red") : x.status === "paused" ? chip(" · paused") : null);
        add(r, l, h("span", "tag", (x.frequency || "") + (x.when_ts ? " · next " + fmtTs(x.when_ts, false) : ""))); b.appendChild(r);
      });
    });
    var once = S.automations.filter(function (x) { return vis(x) && x.kind === "once"; }).length;
    if (once) b.appendChild(h("div", "more", once + " one-time reminder(s) are shown with their events in the timeline."));
  }
  function renderSystem() {
    var b = body("system-status"), m = S.muse;
    function row(k, v) { return add(h("div", "kvl"), h("span", null, k), h("span", "tag", v)); }
    add(b, row("Last Muse update", m.last_update ? ago(m.last_update) + " · " + m.freshness : "never"),
      row("Click actions", S.human_actions_enabled ? (window.MuseActions && window.MuseActions.hasToken() ? "enabled on this device" : "enabled — first click asks for the token") : "disabled on server"),
      row("Actions Muse hasn't processed", String(S.unseen_actions)),
      row("Threads", String(S.threads.length)), row("Records", String(S.queue.length + S.deadlines.length + S.schedule.length + S.projects.length + S.automations.length + S.inbox.length)));
    var sysT = S.threads.filter(function (t) { return t.area === "system" && !closed(t); });
    if (sysT.length) { b.appendChild(h("div", "grp", "System threads")); sysT.forEach(function (t) { b.appendChild(threadCard(t, true)); }); }
  }

  // ---------- views, theme, polling ----------
  var VIEWS = ["home", "week", "timeline", "log", "system"];
  function showView() {
    var v = (location.hash || "#home").slice(1);
    if (v === "now" || v === "areas" || v === "matters" || v.indexOf("thread=") === 0) v = "home";      // old links keep working
    if (v === "week" || v.indexOf("week=") === 0) v = "week";
    if (VIEWS.indexOf(v) < 0) v = "home";
    view = v;
    document.querySelector(".page").classList.toggle("wide", v === "week");
    VIEWS.forEach(function (k) { document.getElementById("view-" + k).hidden = k !== v; });
    var links = document.querySelectorAll("#nav a");
    for (var i = 0; i < links.length; i++) { if (links[i].getAttribute("data-view") === v) links[i].setAttribute("aria-current", "page"); else links[i].removeAttribute("aria-current"); }
  }
  function applyHash() {
    var wk = /^#week=(\d{4}-\d{2}-\d{2})$/.exec(location.hash);
    if (wk) { weekStart = wk[1]; if (S) renderWeek(); return; }
    var m = /^#thread=(.+)$/.exec(location.hash);
    if (m) { openThread(decodeURIComponent(m[1])); return; }
    if (location.hash === "#areas" || location.hash === "#matters") { var el = document.getElementById("matters"); if (el) el.scrollIntoView({ block: "start" }); }
  }
  function render() {
    renderHeader(); renderContext(); renderFocus(); renderNeeds(); renderUpcoming(); renderMotion(); renderMatters();
    renderWeek(); renderTimeline(); renderLog(); renderRoutines(); renderSystem();
    ["week-grid", "focus", "needs-you", "upcoming", "in-progress", "matters", "timeline-full", "log", "routines", "system-status"].forEach(function (id) { document.getElementById(id).classList.toggle("dim", lost); });
  }
  function poll() {
    var ctl = new AbortController(), to = setTimeout(function () { ctl.abort(); }, 4000);
    fetch("/api/state?journal=200", { cache: "no-store", signal: ctl.signal })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (s) {
        clearTimeout(to); lost = false; var first = !S;
        var key = JSON.stringify(s, function (k, v) { return (k === "server_time" || k.slice(-8) === "_seconds") ? undefined : v; });
        if (key !== last || !S) { last = key; S = s; render(); if (first) applyHash(); } else { S.server_time = s.server_time; S.muse = s.muse; renderHeader(); }
      })
      .catch(function () { clearTimeout(to); lost = true; if (S) render(); else document.getElementById("pulse-text").textContent = "Connection lost"; });
  }
  document.addEventListener("toggle", function (e) { var d = e.target, k = d && d.getAttribute && d.getAttribute("data-key"); if (k && openKeys[k] !== d.open) { openKeys[k] = d.open; saveOpen(); } }, true);
  document.addEventListener("click", function (e) {      // UI-only controls (no data changes)
    var t = e.target.closest ? e.target.closest("[data-ui]") : null; if (!t) return;
    var ui = t.getAttribute("data-ui");
    if (ui === "week-prev" || ui === "week-next" || ui === "week-today") {
      var cur = weekStart || (S && S.today); if (!S) return;
      weekStart = ui === "week-today" ? S.today : addDays(cur < S.today ? S.today : cur, ui === "week-next" ? 7 : -7);
      history.replaceState(null, "", weekStart === S.today ? "#week" : "#week=" + weekStart); renderWeek(); return;
    }
    if (ui === "menu") { var mk = t.getAttribute("data-key"); menuOpen = menuOpen === mk ? null : mk; if (S) render(); return; }
    if (ui === "open-thread") { var tid = t.getAttribute("data-thread"); history.replaceState(null, "", "#thread=" + tid); if (S) openThread(tid); return; }
    if (ui === "log-tab") { logTab = t.getAttribute("data-tab"); logLimit = 10; if (S) renderLog(); }
    if (ui === "log-more") { logLimit += 15; if (S) renderLog(); }
    if (ui.indexOf("ua-") === 0 && S) {
      var key = t.getAttribute("data-thread") + "|" + t.getAttribute("data-ua"), st = UA[key];
      if (ui === "ua-start") UA[key] = { step: 0, sel: {} };
      else if (ui === "ua-cancel") delete UA[key];
      else if (ui === "ua-back" && st) st.step = Math.max(0, st.step - 1);
      else if (ui === "ua-pick" && st) {
        var th = thr(t.getAttribute("data-thread")), u = th && (th.ui_actions || []).filter(function (x) { return x.id === t.getAttribute("data-ua"); })[0];
        if (u && u.steps && u.steps[st.step]) { st.sel[u.steps[st.step].id] = t.getAttribute("data-value"); st.step += 1; }
      }
      render();
    }
  });
  window.addEventListener("hashchange", function () { showView(); if (S) applyHash(); });
  window.addEventListener("muse:refresh", function () { menuOpen = null; last = ""; poll(); });

  function currentTheme() {
    var t = document.documentElement.getAttribute("data-theme"); if (t === "dark" || t === "light") return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paintToggle() { var b = document.getElementById("theme-toggle"), dark = currentTheme() === "dark"; b.textContent = dark ? "Light mode" : "Dark mode"; b.setAttribute("aria-pressed", dark ? "true" : "false"); }
  document.getElementById("theme-toggle").addEventListener("click", function () {
    var next = currentTheme() === "dark" ? "light" : "dark"; document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("muse-theme", next); } catch (e) {} paintToggle();
  });
  if (window.matchMedia) { var mq = window.matchMedia("(prefers-color-scheme: dark)"); (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(paintToggle); }
  paintToggle(); showView(); poll(); setInterval(poll, POLL_MS); setInterval(function () { last = ""; }, 60000);
})();
