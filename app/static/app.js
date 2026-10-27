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
  function isTest(r) { var n = r.title || r.name || ""; return /^(zz-|e2e-|hook-e2e)/i.test(r.id || "") || /^(E2E\b|ACCEPTANCE TEST)/i.test(n); }
  function vis(r) { return !r.merged_into && !isTest(r); }
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

  // ---------- context line: latest Chronicle observation (never claims where the user is now) ----------
  function renderContext() {
    var el = document.getElementById("context-line"), c = S.context, l = c && c.latest;
    if (lost || !l || !l.fresh) { el.hidden = true; el.textContent = ""; return; }      // past the TTL it is no longer a live signal
    var place = { home: "Home", office: "Office" }[l.place] || l.place, verb = l.type === "geofence_exit" ? "left" : "entered";
    var clock = new Date(l.observed_at).toLocaleTimeString([], withTz(TOPT));
    el.textContent = "";
    add(el, h("span", "dot"), h("span", null, "Chronicle detected: " + verb + " " + place + " geofence · " + clock + " · " + dur(l.age_seconds) + " ago"));
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
    if (th) { add(meta, add(h("span"), document.createTextNode("About "), h("b", null, th.title))); n++; }
    else { var name = ""; S.projects.forEach(function (p) { if (p.id === f.project) name = p.name; }); if (name) { add(meta, add(h("span"), document.createTextNode("About "), h("b", null, name))); n++; } }
    if (f.deadline) { add(meta, add(h("span"), document.createTextNode("Due "), h("b", null, fmtDate(f.deadline) + (fmtTime(f.deadline) ? " · " + fmtTime(f.deadline) : "")))); n++; }
    if (n) b.appendChild(meta);
    var mine = S.queue.filter(function (q) { return vis(q) && isRecommended(q) && (q.status === "active" || q.status === "blocked") && q.plan_state !== "today"; });
    mine.slice(0, 2).forEach(function (q) { add(b, add(h("div", "s next"), h("b", null, "In progress: "), document.createTextNode(q.title))); });
    var fa = focusAction();
    if (fa) { var bs = h("div", "btns"); add(bs, fa.target ? gotoBtn(fa.label, fa.target) : openBtn(fa.thread, fa.label)); b.appendChild(bs); }
  }
  // the main button follows what the recommendation really is: a decision to make, a task to look at, or progress to follow
  function focusAction() {
    var l = focusLink(), th = l.thread; if (!th) return null;
    var ask = S.inbox.filter(function (i) { return vis(i) && i.thread === th.id && (i.status === undefined || i.status === "open"); })[0];
    if (ask) return { label: ask.options && ask.options.length ? "Choose an option" : "See what is needed", target: "needs-you" };
    return { label: l.task ? "View task" : "View progress", thread: th.id };
  }
  function gotoBtn(label, target) { var x = h("button", "btn primary", label); x.type = "button"; x.setAttribute("data-ui", "goto"); x.setAttribute("data-target", target); return x; }
  function tlink(text, tid) {
    if (!tid || !thr(tid)) return h("span", null, text);
    var a = h("a", "tlink", text); a.href = "#thread=" + tid; a.setAttribute("data-ui", "open-thread"); a.setAttribute("data-thread", tid); return a;
  }
  function openBtn(tid, label) { var x = h("button", "btn primary", label || "Open"); x.type = "button"; x.setAttribute("data-ui", "open-thread"); x.setAttribute("data-thread", tid); return x; }

  function leadOf(t) {          // first sentence (or ~200 characters) shown up front; the full text stays one tap away
    if (t.length <= 220) return t;
    var m = /^(.{40,220}?[.!?\u3002\uff01\uff1f])(\s|$)/.exec(t); return m ? m[1] : t.slice(0, 200).replace(/\s+\S*$/, "") + "\u2026";
  }
  function askCard(i) {
    var c = h("div", "ask" + (i.sensitive ? " sens" : ""));
    var top = h("div", "row2"); add(top, h("div", "t", i.title), i.sensitive ? chip("Sensitive · confirm here only", "red") : null);
    add(c, top);
    var th = i.thread && thr(i.thread);
    if (i.detail) {
      var full = i.detail, lead = leadOf(full);
      add(c, h("div", "s full", lead));
      if (lead.length < full.length) { var dd = details("ask:" + i.id, "more", "Show details"); dd.appendChild(h("div", "s full", full)); c.appendChild(dd); }
    }
    if (th) add(c, add(h("div", "about"), document.createTextNode("About "), tlink(th.title, th.id)));
    var bs = h("div", "btns");
    if (i.status === undefined || i.status === "open" || i.status === "acknowledged") {
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
  function trackRow(g) {
    var t = g.thread, r = h("div", "trk"), top = h("div", "row2");
    add(top, h("div", "t", t ? t.title : g.qs[0].title), h("span", "state amber", "Waiting"));
    r.appendChild(top);
    var prog = line((t && (t.summary || t.next_step)) || g.qs[0].note || "");
    if (prog) r.appendChild(h("div", "s full", prog));
    var nx = t && t.next_step ? t.next_step : "";
    if (nx) r.appendChild(add(h("div", "s full next"), h("b", null, "Next: "), document.createTextNode(line(nx))));
    var meta = [];
    if (g.qs.length > 1 || t) meta.push(g.qs.length + (g.qs.length === 1 ? " task" : " tasks") + " waiting");
    if (g.upd) meta.push("updated " + ago(g.upd));
    if (meta.length) r.appendChild(h("div", "more", meta.join(" \u00b7 ")));
    if (t) { var bs = h("div", "btns"), ob = openBtn(t.id, "Open"); ob.className = "btn"; add(bs, ob); r.appendChild(bs); }
    return r;
  }
  function renderMotion() {
    var b = body("in-progress");
    var live = S.queue.filter(function (q) { return vis(q) && q.plan_state !== "today" && !isRecommended(q) && (q.status === "active" || q.status === "blocked") && !(q.thread && thr(q.thread) && closed(thr(q.thread))); });
    live.sort(function (a, c) { return pr(a.priority) - pr(c.priority) || (a.order || 99) - (c.order || 99); });
    var by = {}, groups = [];
    S.queue.forEach(function (q) {            // waiting work, grouped by the real-world item it belongs to
      if (!vis(q) || q.plan_state === "today" || q.status !== "waiting") return;
      var th = q.thread && thr(q.thread); if (th && closed(th)) return;
      var k = th ? th.id : "q:" + q.id;
      if (!by[k]) { by[k] = { thread: th, qs: [], upd: "" }; groups.push(by[k]); }
      by[k].qs.push(q); var u = (th && th.updated_at) || q.updated_at || ""; if (u > by[k].upd) by[k].upd = u;
    });
    groups.sort(function (a, c) { return a.upd < c.upd ? 1 : a.upd > c.upd ? -1 : 0; });
    document.getElementById("in-progress").hidden = !live.length && !groups.length;
    if (!live.length && !groups.length) return;
    live.slice(0, 6).forEach(function (q) { b.appendChild(qrow(q)); });
    if (live.length > 6) {
      var d = details("motion-more", "more", (live.length - 6) + " more in motion");
      live.slice(6).forEach(function (q) { d.appendChild(qrow(q)); }); b.appendChild(d);
    }
    groups.slice(0, 3).forEach(function (g) { b.appendChild(trackRow(g)); });
    if (groups.length > 3) {
      var w = details("motion-wait", "more", (groups.length - 3) + " more being tracked");
      groups.slice(3).forEach(function (g) { w.appendChild(trackRow(g)); }); b.appendChild(w);
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
    var merged = [], by2 = {};      // same item, same day: a date-only record and a timed one are one real-world event (display only)
    res.forEach(function (x) {
      if (x.kind === "Reminder" || x.kind === "Plan" || !x.rec.thread) { merged.push(x); return; }
      var k = x.rec.thread + "#" + dayNum(x.ts), pv = by2[k];
      if (pv && (pv.dateOnly || x.dateOnly)) {
        var host = pv.dateOnly && !x.dateOnly ? x : pv, other = host === pv ? x : pv;
        host.also = host.also.concat([other], other.also); host.reminders = host.reminders.concat(other.reminders);
        if (host === x) { merged[merged.indexOf(pv)] = x; by2[k] = x; }
        return;
      }
      by2[k] = x; merged.push(x);
    });
    res = merged;
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
  function tlRow(x, bk) {
    var wrap = h("div", "card-row"), r = h("div", "tl"), n = nowS(), key = "tl:" + x.sec + ":" + x.rec.id;
    add(r, h("div", "when", (bk === "Today" || bk === "Tomorrow") && !x.dateOnly ? new Date(x.ts * 1000).toLocaleTimeString([], withTz(TOPT)) : fmtTs(x.ts, x.dateOnly)));
    var m = h("div"), t = h("div", "t", x.rec.title || x.rec.name);
    if (x.kind !== "Event") t.appendChild(h("span", "chip2", x.kind));
    var fl = S.focus ? focusLink() : null;
    if (fl && ((fl.task && x.rec.id === fl.task.id && x.sec === "queue") || (fl.thread && x.rec.thread === fl.thread.id))) t.appendChild(h("span", "chip2 rec", "Recommended"));
    var th = threadTitle(x.rec), subs = [];
    if (x.kind === "Plan" && x.rec.time_hint) subs.push(HINT[x.rec.time_hint]);
    if (th) subs.push(th);
    if (x.kind === "Reminder") { var ri = remInfo(x.rec); if (ri.stopped) subs.push("Stopped"); if (ri.tag) subs.push(ri.tag); }
    if (x.kind === "Event" && x.rec.attendance === "not_going") subs.push("Not attending");
    else if (x.rec.status === "unconfirmed" || x.cert === 0) subs.push("Not confirmed"); else if (x.rec.status === "tentative") subs.push("Tentative");
    x.reminders.forEach(function (y) { if (y.ts !== x.ts) subs.push("Reminder " + fmtTs(y.ts, false).split(" \u00b7 ").pop()); });
    var al = []; x.also.forEach(function (y) { if (y.kind !== x.kind && al.indexOf(y.kind.toLowerCase()) < 0) al.push(y.kind.toLowerCase()); });
    if (al.length) subs.push("Also listed as " + al.join(" and "));
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
    var all = timeline().filter(function (x) { return !(x.kind === "Plan" && x.rec.plan_state === "overdue") && !(x.kind === "Event" && x.rec.attendance === "not_going") && !(x.kind === "Reminder" && remInfo(x.rec).stopped); });
    var cnt = {};     // an event that keeps coming back (dinner, a daily run) is a routine, not news
    all.forEach(function (x) { if (x.kind === "Event" && x.ts < n + 14 * DAY) { var k = (x.rec.title || "").toLowerCase(); cnt[k] = (cnt[k] || 0) + 1; } });
    var main = [], habits = [];
    all.forEach(function (x) {
      if (x.ts >= n + (x.kind === "Deadline" ? 14 : 7) * DAY) return;           // formal deadlines are shown two weeks ahead
      (x.kind === "Event" && cnt[(x.rec.title || "").toLowerCase()] >= 3 ? habits : main).push(x);
    });
    var missed = plannedList("overdue");
    sec.hidden = !main.length && !habits.length && !missed.length;
    if (sec.hidden) return;
    var dl = main.filter(function (x) { return x.kind === "Deadline"; }).slice(0, 3), keep = dl.slice();      // formal deadlines always make it onto Home
    main.forEach(function (x) { if (keep.length < 8 && keep.indexOf(x) < 0) keep.push(x); });
    var shown = main.filter(function (x) { return keep.indexOf(x) >= 0; });
    var lastB = "", bk;
    shown.forEach(function (x) { bk = bucket(x, n); if (bk !== lastB) { b.appendChild(h("div", "day", bk)); lastB = bk; } b.appendChild(tlRow(x, bk)); });
    if (main.length > shown.length) b.appendChild(h("div", "more", (main.length - shown.length) + " more \u2014 see Timeline"));
    if (!main.length) b.appendChild(h("div", "empty", "Nothing important coming up."));
    if (habits.length) {
      var nx = habits[0], hd = details("habits", "more", "Daily routines (" + habits.length + ") \u00b7 next: " + (nx.rec.title || "") + " in " + short(Math.max(0, nx.ts - n)));
      var lb = ""; habits.forEach(function (x) { var k = bucket(x, n); if (k !== lb) { hd.appendChild(h("div", "day", k)); lb = k; } hd.appendChild(tlRow(x, k)); });
      b.appendChild(hd);
    }
    if (missed.length) { var mm = details("today-missed", "more", "Missed plans (" + missed.length + ")"); missed.forEach(function (q) { mm.appendChild(prow(q, true)); }); b.appendChild(mm); }
  }
  function renderTimeline() {
    var b = body("timeline-full"), n = nowS(), items = timeline();
    if (tzLabel()) b.appendChild(h("div", "tznote", "All times are " + tzLabel() + (S.tz ? " (" + S.tz + ")" : "") + "."));
    if (!items.length) return empty(b, "Nothing scheduled.");
    var lastB = "";
    items.forEach(function (x) { var k = bucket(x, n); if (k !== lastB) { b.appendChild(h("div", "day", k)); lastB = k; } b.appendChild(tlRow(x, k)); });
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
        add(row, h("div", "t", u.label), h("div", "tag amber", "Submitted · " + (a.status === "seen" ? "Muse has read it, working on it" : "waiting for Muse") + (uaPicked(a) ? " · " + uaPicked(a) : "")));
      } else if (!steps.length) {
        add(row, uaButton(u.label, { "data-act": "ui_action", "data-section": "threads", "data-id": t.id, "data-ua": u.id, "data-selection": "{}" }, u.sensitive ? "warn" : ""));
      } else if (!st) {
        add(row, uaButton(u.label, { "data-ui": "ua-start", "data-thread": t.id, "data-ua": u.id }, u.sensitive ? "warn" : ""));
      } else if (st.step < steps.length) {                                    // Pending: choosing locally
        var s0 = steps[st.step];
        add(row, h("div", "t", u.label + "  " + (st.step + 1) + "/" + (steps.length + 1)), h("div", "s", s0.prompt));
        var bs = h("div", "btns");
        s0.choices.forEach(function (c) { add(bs, uaButton(c.label, { "data-ui": "ua-pick", "data-thread": t.id, "data-ua": u.id, "data-value": c.value })); });
        add(bs, uaButton("Cancel", { "data-ui": "ua-cancel", "data-thread": t.id, "data-ua": u.id })); row.appendChild(bs);
      } else {                                                                // final local step: confirm
        var picked = steps.map(function (sp) { var c = sp.choices.filter(function (x) { return x.value === st.sel[sp.id]; })[0]; return c ? c.label : ""; }).join(" · ");
        add(row, h("div", "t", u.label + "  " + (steps.length + 1) + "/" + (steps.length + 1)), h("div", "s", "Confirm submission: " + picked));
        var bs2 = h("div", "btns");
        add(bs2, uaButton("Confirm", { "data-act": "ui_action", "data-section": "threads", "data-id": t.id, "data-ua": u.id, "data-selection": JSON.stringify(st.sel) }, "primary"),
            uaButton("Back", { "data-ui": "ua-back", "data-thread": t.id, "data-ua": u.id }), uaButton("Cancel", { "data-ui": "ua-cancel", "data-thread": t.id, "data-ua": u.id })); row.appendChild(bs2);
      }
      if (a && a.status !== "accepted" && a.status !== "seen" && a.result_at && (Date.parse(S.server_time) - Date.parse(a.result_at)) < DAY * 1000) {   // Completed
        add(row, h("div", "tag " + (a.status === "completed" ? "green" : "amber"), (a.status === "completed" ? "✓ Done" : a.status === "declined" ? "Muse declined" : "Muse could not complete") + (a.note ? ": " + a.note : "")));
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
    if (t.stage) add(tags, /^unknown$/i.test(t.stage) ? chip("Unknown") : chip(t.stage, "green")); else if (t.status !== "active") add(tags, chip({ waiting: "Waiting", paused: "Paused", done: "Archived", dropped: "Dropped" }[t.status] || t.status));
    if (closed(t) && imp.tasks - imp.waiting > 0) add(tags, chip((imp.tasks - imp.waiting) + " open", "amber"));
    else if (closed(t) && imp.waiting) add(tags, chip("tracking " + imp.waiting));
    var nUa = actionsOn() ? (t.ui_actions || []).filter(function (u) { return !u.expired; }).length : 0;
    if (nUa) add(tags, chip("\u25CF " + nUa + (nUa === 1 ? " action available" : " actions available"), "amber"));
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
    if (t.summary) d.appendChild(h("div", "s full", t.summary));       // the whole text, line breaks kept (a weekly menu, a plan...): never cut to two lines
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
    var more = btn(closed(t) ? "Reopen" : "Archive…", closed(t) ? "reopen" : "done", "threads", t.id);
    if (more) { var mn = h("details", "sec"), ms = h("summary", null, "···"); ms.setAttribute("aria-label", "More"); add(mn, ms, more); d.appendChild(mn); }
    return d;
  }
  function renderMatters() {
    var b = body("matters"), any = false;
    S.areas.filter(function (a) { return a.id !== "system"; }).concat([{ id: "", label: "Not grouped yet" }]).forEach(function (a) {
      var ths = S.threads.filter(function (t) { return (t.area || "") === a.id && !closed(t) && !isTest(t); });
      var orphans = ["queue", "deadlines", "schedule", "projects"].reduce(function (acc, k) {
        return acc.concat(S[k].filter(function (r) { return vis(r) && !r.thread && areaOf(r) === a.id && !closed(r) && !r.past; }).map(function (r) { return { k: k, r: r }; }));
      }, []);
      if (!ths.length && !orphans.length) return;
      any = true;
      var d = details("area:" + a.id, "area", null), s2 = d.querySelector("summary");
      var cnt = ths.length + orphans.length;
      add(s2, h("span", null, a.label), h("span", "n", cnt + (cnt === 1 ? " item" : " items")));
      ths.forEach(function (t) { d.appendChild(threadCard(t)); });
      if (orphans.length) {
        var box = h("div", "rel"); box.appendChild(h("div", "grp", "Not yet grouped"));
        orphans.forEach(function (o) { box.appendChild(itRow(o.r.title || o.r.name)); });
        d.appendChild(box);
      }
      b.appendChild(d);
    });
    var arch = S.threads.filter(function (t) { return closed(t) && t.area !== "system" && !isTest(t); }), recs = [], merged = 0;
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
    if (!any) empty(b, "Nothing here yet. Muse groups everything by area.");
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
  function weekItem(x) {        // clock times shown when the item has one; order within a day stays chronological
    var muted = (x.kind === "Event" && x.rec.attendance === "not_going");
    var el = h("div", "wk-item " + x.kind.toLowerCase() + (muted ? " muted" : "")), t = h("div", "wk-time");
    var clock = "";
    if (!x.dateOnly && x.ts) {
      clock = new Date(x.ts * 1000).toLocaleTimeString([], withTz(TOPT));
      if (x.kind === "Event" && x.rec.end) { var ce = fmtTime(x.rec.end); if (ce && ce !== clock) clock += " – " + ce; }
    }
    var wlabel = x.kind === "Deadline" ? "Due" : x.kind === "Plan" ? "Plan" + (x.rec.time_hint ? " · " + HINT[x.rec.time_hint] : "") : x.kind;
    t.textContent = clock ? (x.kind === "Event" ? clock : clock + " · " + wlabel) : wlabel;
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
    b.appendChild(h("div", "more", "Read-only. Items are in chronological order within each day. Routines are not shown."));
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

  // ---------- SYSTEM: a plain status line first; the developer detail stays complete but folded ----------
  function renderRoutines() {
    var b = body("routines"), a = S.automations.filter(function (x) { return vis(x) && x.kind !== "once"; });
    if (!a.length) return empty(b, "No recurring routines recorded.");
    var box = details("sys-routines", "more", a.length + " recurring routines \u2014 show");
    function tod(x) { if (!x.when_ts) return "Unscheduled"; var hr = new Date(x.when_ts * 1000).getHours(); return hr < 12 ? "Morning" : hr < 18 ? "Afternoon" : "Evening"; }
    var groups = [["Always on", function (x) { return x.kind === "always"; }]];
    ["Morning", "Afternoon", "Evening", "Unscheduled"].forEach(function (g) { groups.push([g + " (daily)", function (x) { return x.kind === "daily" && tod(x) === g; }]); });
    groups.push(["Weekly", function (x) { return x.kind === "weekly"; }], ["Monthly", function (x) { return x.kind === "monthly"; }], ["Other", function (x) { return !x.kind; }]);
    groups.forEach(function (g) {
      var items = a.filter(g[1]).sort(function (x, y) { return (x.when_ts ? new Date(x.when_ts * 1000).getHours() * 60 + new Date(x.when_ts * 1000).getMinutes() : 9999) - (y.when_ts ? new Date(y.when_ts * 1000).getHours() * 60 + new Date(y.when_ts * 1000).getMinutes() : 9999); });
      if (!items.length) return;
      box.appendChild(h("div", "grp", g[0]));
      var seen = {};
      items.forEach(function (x) {
        var k = x.name + "|" + (x.frequency || "") + "|" + (x.when_ts || ""); if (seen[k]) { seen[k].n++; return; }      // identical mirrors are listed once (display only)
        var r = h("div", "kvl"), l = h("span"), nm = h("span", null, x.name); seen[k] = { n: 1, el: nm }; add(l, nm, x.status === "failed" ? chip(" \u00b7 failed", "red") : x.status === "paused" ? chip(" \u00b7 paused") : null);
        add(r, l, h("span", "tag", (x.frequency || "") + (x.when_ts ? " \u00b7 next " + fmtTs(x.when_ts, false) : ""))); box.appendChild(r);
      });
      Object.keys(seen).forEach(function (k) { if (seen[k].n > 1) seen[k].el.textContent += "  \u00d7" + seen[k].n; });
    });
    b.appendChild(box);
    var once = S.automations.filter(function (x) { return vis(x) && x.kind === "once"; }).length;
    if (once) b.appendChild(h("div", "more", once + " one-time reminder(s) are shown with their events in the timeline."));
  }
  function renderSystem() {
    var b = body("system-status"), m = S.muse;
    var state = lost ? ["red", "Connection lost \u2014 showing the last data received."] : m.freshness === "fresh" ? ["green", "Muse is reporting normally \u00b7 updated " + ago(m.last_update)] :
      m.freshness === "aging" ? ["amber", "Muse is updating slowly \u00b7 last update " + ago(m.last_update)] : m.freshness === "stale" ? ["red", "No update from Muse for " + dur(m.age_seconds)] : ["", "Muse has not reported yet"];
    b.appendChild(h("div", "state " + state[0] + " big", state[1]));
    function row(k, v) { return add(h("div", "kvl"), h("span", null, k), h("span", "tag", v)); }
    var tech = details("sys-tech", "more", "Technical details");
    add(tech, row("Last Muse update", m.last_update ? ago(m.last_update) + " \u00b7 " + m.freshness : "never"),
      row("Click actions", S.human_actions_enabled ? (window.MuseActions && window.MuseActions.hasToken() ? "enabled on this device" : "enabled \u2014 first click asks for the token") : "disabled on server"),
      row("Actions Muse hasn't processed", String(S.unseen_actions)),
      row("Items", String(S.threads.length)), row("Records", String(S.queue.length + S.deadlines.length + S.schedule.length + S.projects.length + S.automations.length + S.inbox.length)));
    b.appendChild(tech);
    var sysT = S.threads.filter(function (t) { return t.area === "system" && !closed(t) && !isTest(t); });
    if (sysT.length) { var bg = details("sys-bg", "more", "Muse's own background items (" + sysT.length + ")"); sysT.forEach(function (t) { bg.appendChild(threadCard(t, true)); }); b.appendChild(bg); }
    var tests = [];       // test and debug records never clutter daily views, but stay searchable here
    S.threads.forEach(function (t) { if (isTest(t)) tests.push(t); });
    var recs = [];
    ["queue", "deadlines", "schedule", "automations", "inbox", "projects"].forEach(function (k) { S[k].forEach(function (r) { if (isTest(r)) recs.push(k + ": " + (r.title || r.name || r.id)); }); });
    if (tests.length || recs.length) {
      var td = details("sys-test", "more", "Test & debug items (" + (tests.length + recs.length) + ")");
      tests.forEach(function (t) { td.appendChild(threadCard(t, true)); });
      recs.forEach(function (x) { td.appendChild(h("div", "src", x)); });
      b.appendChild(td);
    }
  }

  // ---------- PAPERS: a few worth-reading papers per day (public metadata; Muse chooses, you only rate) ----------
  var PAP = null, papTab = "today", papDays = 7, papSig = "", papBusy = false;
  var SRC = { arxiv: "arXiv", huggingface: "Hugging Face", scholar: "Google Scholar", alphaxiv: "alphaXiv", other: "Link" };
  function loadPapers(force) {
    if (papBusy) return; papBusy = true;
    fetch("/api/papers?days=" + papDays, { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { PAP = d; papSig = papSignature(); papBusy = false; renderPapers(); })
      .catch(function () { papBusy = false; if (!PAP) { var b = body("papers"); empty(b, "Papers are unavailable right now."); } });
  }
  function papStatus(p) { return p.status; }
  function papSignature() { return (S.papers || []).map(function (x) { return x.id + ":" + x.status; }).sort().join(","); }
  function safeUrl(u) { return typeof u === "string" && u.indexOf("https://") === 0; }
  function srcLinks(p) {
    var box = h("div", "psrc");
    (p.sources || []).forEach(function (s) {
      if (!safeUrl(s.url)) return;
      var a = h("a", "chip2", (SRC[s.name] || "Link") + " \u2197"); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer"; box.appendChild(a);
    });
    return box;
  }
  function projLabel(id) { var t = thr(id); if (t) return t.title; var r = null; S.projects.forEach(function (x) { if (x.id === id) r = x.name; }); return r; }
  function papBtn(label, act, p, st, want) {
    var on = st === want, b = actionsOn() ? btn(label, on ? "reopen" : act, "papers", p.id, null, on ? "primary" : "") : null;
    if (b) { b.setAttribute("aria-pressed", on ? "true" : "false"); b.setAttribute("data-paper-action", act); }
    return b;
  }
  function paperCard(p) {
    var st = papStatus(p), c = h("div", "paper" + (st === "not_relevant" ? " muted" : "")), top = h("div", "row2");
    var main = (p.sources || []).filter(function (s) { return safeUrl(s.url); }).sort(function (a, b2) { var o = { arxiv: 0, huggingface: 1, scholar: 2 }; return (o[a.name] === undefined ? 3 : o[a.name]) - (o[b2.name] === undefined ? 3 : o[b2.name]); })[0];
    var tt = main ? h("a", "ptitle", p.title) : h("span", "ptitle", p.title);
    if (main) { tt.href = main.url; tt.target = "_blank"; tt.rel = "noopener noreferrer"; }
    add(top, tt, st !== "recommended" ? chip({ saved: "Saved", read_later: "Read later", not_relevant: "Not relevant" }[st] || st, st === "saved" ? "green" : "") : null);
    c.appendChild(top);
    var au = p.authors || [], meta = [au.slice(0, 3).join(", ") + (au.length > 3 ? " +" + (au.length - 3) : ""), p.published ? "Published " + fmtDate(p.published) : "", p.updated && p.updated !== p.published ? "Updated " + fmtDate(p.updated) : ""].filter(Boolean);
    if (meta.length) c.appendChild(h("div", "pmeta", meta.join(" \u00b7 ")));
    if (p.reason) c.appendChild(add(h("div", "s full why"), h("b", null, "Muse's reason: "), document.createTextNode(p.reason)));
    if (p.quote) c.appendChild(p.quote_ok === true ? h("div", "pquote", "\u201c" + p.quote + "\u201d") : h("div", "pmeta", "Quote not found in the abstract \u2014 check the source."));       // only says the sentence is in the abstract, not that Muse's reason is right
    var pj = (p.projects || []).map(projLabel).filter(Boolean);
    if (pj.length) c.appendChild(h("div", "pmeta", "Matches: " + pj.join(", ")));
    if (p.abstract) { var d = details("pabs:" + p.id, "more", "Abstract (from the source)"); d.appendChild(h("div", "s full", p.abstract)); c.appendChild(d); }
    c.appendChild(srcLinks(p));
    var known = (S.papers || []).some(function (x) { return x.id === p.id; });
    if (known && actionsOn()) { var bs = h("div", "btns"); add(bs, papBtn("Save", "save", p, st, "saved"), papBtn("Read later", "read_later", p, st, "read_later"), papBtn("Not relevant", "not_relevant", p, st, "not_relevant")); c.appendChild(bs); }
    return c;
  }
  function renderPapers() {
    var b = body("papers"); if (!PAP) { if (view === "papers") { loadPapers(false); } empty(b, "Loading papers\u2026"); return; }
    if (papSignature() !== papSig && !papBusy) { loadPapers(false); }       // a new pick or a rating from another device
    var tabs = h("div", "tabs");
    [["today", "Today"], ["saved", "Saved"], ["read_later", "Read later"], ["history", "History"]].forEach(function (t) {
      var x = h("button", "btn", t[1]); x.type = "button"; x.setAttribute("data-ui", "pap-tab"); x.setAttribute("data-tab", t[0]); x.setAttribute("aria-pressed", papTab === t[0] ? "true" : "false"); tabs.appendChild(x);
    });
    b.appendChild(tabs);
    var days = PAP.days, today = PAP.today;
    if (papTab === "today") {
      var d0 = days.filter(function (d) { return d.date === today; })[0], ps = d0 ? d0.papers : [];
      var shown = ps.filter(function (p) { return papStatus(p) !== "not_relevant"; }), hid = ps.filter(function (p) { return papStatus(p) === "not_relevant"; });
      shown.forEach(function (p) { b.appendChild(paperCard(p)); });
      if (ps.length && !shown.length) b.appendChild(h("div", "empty", "Everything from today is hidden as not relevant."));
      if (!ps.length) {
        var latest = days.filter(function (d) { return d.date < today && d.papers.length; })[0];
        b.appendChild(h("div", "empty", d0 ? (d0.note || "Nothing today was worth your time.") + (d0.candidates ? " (" + d0.candidates + " papers checked.)" : "") : "No picks yet today. Muse posts them once a day."));
        if (latest) b.appendChild(h("div", "more", "Latest picks: " + fmtDate(latest.date) + " \u2014 see History."));
      }
      if (hid.length) { var hd = details("pap-hidden", "more", hid.length + " hidden as not relevant"); hid.forEach(function (p) { hd.appendChild(paperCard(p)); }); b.appendChild(hd); }
    } else if (papTab === "saved" || papTab === "read_later") {
      var want = papTab === "saved" ? "saved" : "read_later", n = 0;
      days.forEach(function (d) { d.papers.filter(function (p) { return papStatus(p) === want; }).forEach(function (p) { n++; b.appendChild(h("div", "day", fmtDate(d.date))); b.appendChild(paperCard(p)); }); });
      if (!n) b.appendChild(h("div", "empty", papTab === "saved" ? "Nothing saved yet." : "Nothing queued for later."));
    } else {
      days.forEach(function (d) {
        var dd = details("pday:" + d.date, "more", fmtDate(d.date) + " \u00b7 " + (d.papers.length ? d.papers.length + (d.papers.length === 1 ? " pick" : " picks") : "no picks"));
        d.papers.forEach(function (p) { dd.appendChild(paperCard(p)); });
        if (!d.papers.length && d.note) dd.appendChild(h("div", "empty", d.note));
        b.appendChild(dd);
      });
      if (PAP.next_before) { var mb = h("button", "btn", "Load older days"); mb.type = "button"; mb.setAttribute("data-ui", "pap-more"); b.appendChild(mb); }
    }
  }

  // ---------- views, theme, polling ----------
  var VIEWS = ["home", "week", "timeline", "papers", "log", "system"];
  function showView() {
    var v = (location.hash || "#home").slice(1);
    if (v === "now" || v === "areas" || v === "matters" || v.indexOf("thread=") === 0) v = "home";      // old links keep working
    if (v === "week" || v.indexOf("week=") === 0) v = "week";
    if (VIEWS.indexOf(v) < 0) v = "home";
    view = v;
    document.querySelector(".page").classList.toggle("wide", v === "week");
    VIEWS.forEach(function (k) { document.getElementById("view-" + k).hidden = k !== v; });
    if (v === "papers" && S) loadPapers(false);
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
    renderWeek(); renderTimeline(); renderPapers(); renderLog(); renderRoutines(); renderSystem();
    ["week-grid", "focus", "needs-you", "upcoming", "in-progress", "matters", "timeline-full", "papers", "log", "routines", "system-status"].forEach(function (id) { document.getElementById(id).classList.toggle("dim", lost); });
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
    if (ui === "goto") { var gt = document.getElementById(t.getAttribute("data-target")); if (gt) gt.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    if (ui === "menu") { var mk = t.getAttribute("data-key"); menuOpen = menuOpen === mk ? null : mk; if (S) render(); return; }
    if (ui === "open-thread") { var tid = t.getAttribute("data-thread"); history.replaceState(null, "", "#thread=" + tid); if (S) openThread(tid); return; }
    if (ui === "pap-tab") { papTab = t.getAttribute("data-tab"); if (S) renderPapers(); return; }
    if (ui === "pap-more") { papDays += 7; loadPapers(false); return; }
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
  window.addEventListener("muse:refresh", function () { menuOpen = null; last = ""; poll(); if (PAP) { papBusy = false; loadPapers(false); } });

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
