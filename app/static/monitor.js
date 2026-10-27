(function () {
  "use strict";
  // Read-only wall monitor: 1920x1080, no scrolling, no clicks. Everything comes from GET /api/state.
  var POLL_MS = 10000, S = null, lost = false, loadedAt = Date.now(), DAY = 86400;

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null && text !== "") e.textContent = String(text);
    return e;
  }
  function add(p) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) p.appendChild(arguments[i]); return p; }
  function nowS() { return Date.now() / 1000; }
  function tz() { return (S && S.tz) || undefined; }

  // ---------- data helpers ----------
  function thr(id) { for (var i = 0; i < S.threads.length; i++) if (S.threads[i].id === id) return S.threads[i]; return null; }
  function isTest(r) { var n = r.title || r.name || ""; return /^(zz-|e2e-|hook-e2e)/i.test(r.id || "") || /^(E2E\b|ACCEPTANCE TEST)/i.test(n); }
  function vis(r) { return !r.merged_into && !isTest(r); }
  function closed(r) { return r.status === "done" || r.status === "dropped"; }
  function pr(p) { var o = { p0: 0, p1: 1, p2: 2, p3: 3 }; return o[p] === undefined ? 9 : o[p]; }
  function threadTitle(r) { var t = r.thread && thr(r.thread); return t ? t.title : ""; }
  function line(note) {
    if (!note) return "";
    var seg = String(note).split(/[；;。\n]/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (!seg.length) return ""; var out = seg[0]; if (out.length < 14 && seg[1]) out += "；" + seg[1]; return out;
  }

  // ---------- time ----------
  function parts(ts, opt) { return new Date(ts * 1000).toLocaleString("en-US", Object.assign({ timeZone: tz() }, opt)); }
  function hhmm(ts) { return new Date(ts * 1000).toLocaleTimeString("en-GB", { timeZone: tz(), hour: "2-digit", minute: "2-digit", hourCycle: "h23" }); }
  function dayKey(ts) { return new Date(ts * 1000).toLocaleDateString("en-CA", { timeZone: tz() }); }
  function dayLabel(ts, n) {
    var d = dayKey(ts), t = dayKey(n), tm = dayKey(n + DAY);
    if (d === t) return "TODAY"; if (d === tm) return "TOMORROW";
    return parts(ts, { weekday: "long", month: "short", day: "numeric" }).toUpperCase();
  }
  function span(s) {
    s = Math.abs(Math.round(s)); var m = Math.floor(s / 60);
    if (m < 1) return "now"; if (m < 60) return m + "m";
    var hr = Math.floor(m / 60); if (hr < 24) return hr + "h " + (m % 60 ? (m % 60) + "m" : "");
    var d = Math.floor(hr / 24); return d + "d" + (hr % 24 ? " " + (hr % 24) + "h" : "");
  }
  function countdown(ts, n) { var d = ts - n; return d < 0 ? "overdue " + span(d) : "in " + span(d); }
  function urgency(ts, n) { var d = ts - n; return d < 0 ? "red" : d < 86400 ? "red" : d < 3 * 86400 ? "amber" : ""; }

  // ---------- layout helpers ----------
  function panel(id) { var p = document.getElementById(id), r = p.querySelector(".rows"); r.textContent = ""; return { p: p, r: r }; }
  // append rows until the panel is full; the rest is summarised as "+N more" (nothing scrolls)
  function fill(box, rows, emptyMsg) {
    var r = box.r;
    if (!rows.length) { r.appendChild(h("div", "empty", emptyMsg || "Nothing.")); return; }
    rows.forEach(function (x) { r.appendChild(x); });
    var shown = rows.length, more = h("div", "more");
    function over() { return r.scrollHeight > r.clientHeight + 1; }
    if (over()) {
      r.appendChild(more);
      while (shown > 1 && over()) { r.removeChild(rows[shown - 1]); shown--; more.textContent = "+" + (rows.length - shown) + " more"; }
      if (shown === rows.length) r.removeChild(more);
    }
  }
  function row(whenTxt, whenCls, title, sub, side, sideCls, extra) {
    var r = h("div", "row" + (extra ? " " + extra : "")), m = h("div", "main");
    if (whenTxt !== null) r.appendChild(h("div", "when " + (whenCls || ""), whenTxt));
    add(m, h("div", "t", title), sub ? h("div", "s", sub) : null);
    r.appendChild(m);
    if (side) r.appendChild(h("div", "side " + (sideCls || ""), side));
    return r;
  }

  // ---------- panels ----------
  function renderHeader() {
    var n = nowS();
    document.getElementById("clock").textContent = hhmm(n);
    document.getElementById("date").textContent = parts(n, { weekday: "long", month: "long", day: "numeric" });
    var el = document.getElementById("pulse"), t = document.getElementById("pulse-text"), m = S.muse;
    el.className = "pulse " + (lost ? "lost" : m.freshness);
    var age = (Date.parse(S.server_time) - Date.parse(m.last_update)) / 1000;
    if (lost) t.textContent = "Connection lost — showing last data";
    else if (m.freshness === "never") t.textContent = "Muse hasn't reported yet";
    else if (m.freshness === "stale") t.textContent = "No update from Muse in " + span(age);
    else t.textContent = "Muse updated " + span(age) + " ago";
  }
  function renderCounts(dl, tasks, ev, needs) {
    var c = document.getElementById("counts"); c.textContent = "";
    function k(n, label, hot) { var s = h("span", hot ? "hot" : ""); add(s, h("b", null, n), document.createTextNode(label)); c.appendChild(s); }
    k(needs, "needs you", needs > 0); k(dl.overdue + dl.soon, "due ≤ 3 days", dl.overdue > 0); k(tasks, "to do"); k(ev, "events today");
  }
  function focusPanel(n) {
    var b = panel("p-focus"), f = S.focus;
    if (!f || !f.title) { fill(b, [], "No recommendation right now."); return; }
    var r = b.r; r.appendChild(h("div", "ft", f.title));
    if (f.reason) r.appendChild(h("div", "fr", f.reason));
    var due = f.deadline && Date.parse(f.deadline);
    if (due) { var ts = due / 1000; r.appendChild(h("div", "fm " + urgency(ts, n), "Due " + dayLabel(ts, n).toLowerCase() + " " + hhmm(ts) + " · " + countdown(ts, n))); }
  }
  function needsPanel() {
    var b = panel("p-needs"), open = S.inbox.filter(function (i) { return vis(i) && (i.status === "open" || i.status === undefined); });
    b.p.hidden = !open.length; if (!open.length) return open;
    fill(b, open.map(function (i) { return row(null, "", i.title, line(i.detail), "", "", "wrap"); }));
    return open;
  }
  function events(n) {
    return S.schedule.filter(function (e) {
      if (!vis(e) || e.status === "cancelled" || e.attendance === "not_going") return false;
      var end = e.end ? Date.parse(e.end) / 1000 : NaN;
      return (isNaN(end) ? e.when_ts + 3600 : end) > n;
    }).sort(function (a, c) { return a.when_ts - c.when_ts; });
  }
  function todayPanel(evs, n) {
    var b = panel("p-today"), tm = dayKey(n + DAY), rows = [], last = "";
    evs.filter(function (e) { return dayKey(e.when_ts) <= tm; }).forEach(function (e) {
      var lab = dayLabel(e.when_ts, n); if (lab !== last) { rows.push(h("div", "day", lab)); last = lab; }
      var end = e.end ? Date.parse(e.end) / 1000 : NaN, ongoing = e.when_ts <= n;
      var loc = /^https?:/i.test(e.location || "") ? "online" : (e.location || "");
      var sub = [loc, e.status === "tentative" ? "tentative" : ""].filter(Boolean).join(" · ");
      var r = row(hhmm(e.when_ts) + (isNaN(end) ? "" : "–" + hhmm(end)), ongoing ? "amber" : "", e.title, sub, ongoing ? "NOW" : (e.when_ts - n < 6 * 3600 ? "in " + span(e.when_ts - n) : ""), ongoing ? "amber" : "", ongoing ? "now compact" : "compact");
      r.querySelector(".when").style.width = "190px"; rows.push(r);
    });
    fill(b, rows, "Nothing scheduled for today or tomorrow.");
  }
  function deadlinesPanel(n) {
    var b = panel("p-deadlines"), cnt = { overdue: 0, soon: 0 };
    var dl = S.deadlines.filter(function (d) { return vis(d) && d.status === "open" && d.when_ts && d.when_ts < n + 21 * DAY; })
      .sort(function (a, c) { return a.when_ts - c.when_ts; });
    dl.forEach(function (d) { if (d.when_ts < n) cnt.overdue++; else if (d.when_ts < n + 3 * DAY) cnt.soon++; });
    fill(b, dl.map(function (d) {
      var u = urgency(d.when_ts, n), t = threadTitle(d);
      return row(dayLabel(d.when_ts, n) === "TODAY" || dayLabel(d.when_ts, n) === "TOMORROW" ? dayLabel(d.when_ts, n)[0] + dayLabel(d.when_ts, n).slice(1).toLowerCase() : parts(d.when_ts, { month: "short", day: "numeric" }),
        u, d.title, [hhmm(d.when_ts), t && t !== d.title ? t : ""].filter(Boolean).join(" · "), countdown(d.when_ts, n), u, "compact");
    }), "No open deadlines in the next 3 weeks.");
    return cnt;
  }
  function tasksPanel() {
    var b = panel("p-tasks");
    var q = S.queue.filter(function (x) { return vis(x) && !closed(x) && x.status !== "waiting"; });
    function rank(x) { return x.plan_state === "overdue" ? 0 : x.plan_state === "today" ? 1 : x.plan_state === "tomorrow" ? 3 : 2; }
    q.sort(function (a, c) { return rank(a) - rank(c) || pr(a.priority) - pr(c.priority) || (a.order || 99) - (c.order || 99); });
    var HINT = { morning: "morning", afternoon: "afternoon", evening: "evening", tonight: "tonight" };
    fill(b, q.map(function (x) {
      var r = h("div", "row"), m = h("div", "main"), t = h("div", "t", x.title), th = threadTitle(x);
      if (x.plan_state === "today") t.appendChild(h("span", "tag", "today" + (HINT[x.time_hint] ? " · " + HINT[x.time_hint] : "")));
      else if (x.plan_state === "overdue") t.appendChild(h("span", "tag red", "missed"));
      else if (x.plan_state === "tomorrow") t.appendChild(h("span", "tag", "tomorrow"));
      var sub = [th && th !== x.title ? th : "", line(x.note)].filter(Boolean).join(" · ");
      add(m, t, sub ? h("div", "s", sub) : null);
      add(r, h("div", "pri " + (x.priority || ""), x.priority ? x.priority.toUpperCase() : "·"), m);
      if (x.status === "blocked") r.appendChild(h("div", "side red", "Blocked"));
      return r;
    }), "Nothing on the list.");
    return q.length;
  }
  function waitingPanel() {
    var b = panel("p-waiting"), by = {}, groups = [];
    S.queue.forEach(function (x) {
      if (!vis(x) || x.status !== "waiting") return;
      var th = x.thread && thr(x.thread); if (th && closed(th)) return;
      var k = th ? th.id : "q:" + x.id;
      if (!by[k]) { by[k] = { th: th, qs: [], upd: "" }; groups.push(by[k]); }
      by[k].qs.push(x); var u = (th && th.updated_at) || x.updated_at || ""; if (u > by[k].upd) by[k].upd = u;
    });
    groups.sort(function (a, c) { return a.upd < c.upd ? 1 : a.upd > c.upd ? -1 : 0; });
    fill(b, groups.map(function (g) {
      var title = g.th ? g.th.title : g.qs[0].title, sub = g.qs.map(function (x) { return x.title; }).join(" · ");
      if (g.th && g.th.next_step) sub = "Next: " + line(g.th.next_step);
      return row(null, "", title, sub, g.qs.length > 1 ? g.qs.length + " waiting" : "", "mut");
    }), "Nothing is waiting on anyone.");
  }
  function projectsPanel() {
    var b = panel("p-projects");
    var ps = S.projects.filter(function (p) { return vis(p) && p.status === "active"; });
    fill(b, ps.map(function (p) { return row(null, "", p.name || p.title, p.next_step ? "Next: " + line(p.next_step) : line(p.progress)); }), "No active projects.");
  }
  function weekPanel(evs, n) {
    var b = panel("p-week"), tm = dayKey(n + DAY), rows = [];
    evs.filter(function (e) { return dayKey(e.when_ts) > tm && e.when_ts < n + 7 * DAY; }).forEach(function (e) {
      rows.push(row(parts(e.when_ts, { weekday: "short", day: "numeric" }), "", e.title, "", hhmm(e.when_ts), "mut", "compact"));
    });
    fill(b, rows, "Nothing else this week.");
  }
  function papersPanel() {
    var b = panel("p-papers"), ps = (S.papers || []).filter(function (p) { return p.status !== "not_relevant"; });
    var latest = ps.reduce(function (m, p) { return p.rec_date > m ? p.rec_date : m; }, "");
    var today = ps.filter(function (p) { return p.rec_date === latest; });
    fill(b, today.map(function (p) { return row(null, "", p.title, "", p.status === "saved" ? "saved" : "", "green", "compact"); }), "No papers recommended yet.");
  }

  function render() {
    var n = nowS();
    renderHeader();
    var needs = needsPanel(), evs = events(n);
    focusPanel(n); todayPanel(evs, n);
    var dl = deadlinesPanel(n), tasks = tasksPanel();
    waitingPanel(); projectsPanel(); weekPanel(evs, n); papersPanel();
    var tk = dayKey(n);
    renderCounts(dl, tasks, evs.filter(function (e) { return dayKey(e.when_ts) === tk; }).length, needs.length);
    document.getElementById("screen").classList.toggle("dim", lost);
  }

  // ---------- 1920x1080 stage, scaled if the window differs ----------
  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080), el = document.getElementById("screen");
    el.style.transform = s === 1 ? "" : "scale(" + s + ")";
  }

  // ---------- polling ----------
  var busy = false;
  function poll() {
    if (busy) return; busy = true;
    var ctl = new AbortController(), to = setTimeout(function () { ctl.abort(); }, 8000);
    fetch("/api/state", { cache: "no-store", signal: ctl.signal })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { S = d; lost = false; render(); })
      .catch(function () { lost = true; if (S) render(); else document.getElementById("pulse-text").textContent = "Cannot reach the dashboard"; })
      .then(function () { clearTimeout(to); busy = false; });
  }
  setInterval(poll, POLL_MS);
  setInterval(function () { if (S) { renderHeader(); } }, 1000);
  // a long-running kiosk picks up a new frontend release by reloading once a day, around 04:00 local time
  setInterval(function () {
    var d = new Date();
    if (d.getHours() === 4 && d.getMinutes() < 2 && Date.now() - loadedAt > 3600000) window.location.reload();
  }, 30000);
  window.addEventListener("resize", fit);
  fit(); poll();
})();
