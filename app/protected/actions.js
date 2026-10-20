/* Protected interaction layer (backend-owned; NOT part of the editable frontend sandbox).
 * Turns real user clicks on [data-act] elements into authorised POST /api/action calls.
 * - only trusted (human) click events are honoured; script-generated clicks are ignored
 * - the human token lives in this browser only (localStorage) and is entered in this script's own dialog
 * - sensitive items always get a confirmation dialog built here from server data, not from page markup
 */
(function () {
  "use strict";
  var KEY = "muse-human-token", busy = false, root;

  function el_(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  function token() { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } }
  function setToken(t) { try { localStorage.setItem(KEY, t); } catch (e) {} }
  function clearToken() { try { localStorage.removeItem(KEY); } catch (e) {} }

  function ensureRoot() {
    if (root) return root;
    var l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/_ui/actions.css"; document.head.appendChild(l);
    root = el_("div"); root.id = "muse-actions-root"; document.body.appendChild(root);
    return root;
  }
  function toast(msg, bad) {
    ensureRoot();
    var t = el_("div", "ma-toast" + (bad ? " bad" : ""), msg); t.setAttribute("role", "status");
    root.appendChild(t); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 3500);
  }
  function dialog(title, bodyNodes, buttons) {
    ensureRoot();
    var back = el_("div", "ma-back"), box = el_("div", "ma-box");
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
    box.appendChild(el_("div", "ma-title", title));
    bodyNodes.forEach(function (n) { box.appendChild(n); });
    var row = el_("div", "ma-row");
    function close() { if (back.parentNode) back.parentNode.removeChild(back); document.removeEventListener("keydown", onKey, true); }
    function onKey(e) { if (e.key === "Escape") close(); }
    buttons.forEach(function (b) {
      var x = el_("button", "ma-btn" + (b.primary ? " primary" : ""), b.label); x.type = "button";
      x.addEventListener("click", function () { if (!b.keep) close(); b.run && b.run(close); });
      row.appendChild(x);
    });
    box.appendChild(row); back.appendChild(box); root.appendChild(back);
    document.addEventListener("keydown", onKey, true);
    var first = box.querySelector("input,button.primary,button"); if (first) first.focus();
    return close;
  }

  function askToken(then) {
    var inp = el_("input", "ma-input"); inp.type = "password"; inp.autocomplete = "off"; inp.placeholder = "Actions token";
    var note = el_("div", "ma-note", "Enter the actions token once to enable click actions on this device. It is stored only in this browser.");
    dialog("Enable actions", [note, inp], [
      { label: "Cancel" },
      { label: "Save", primary: true, run: function () { if (inp.value.length >= 16) { setToken(inp.value.trim()); then(); } else toast("Token too short", true); } }
    ]);
  }

  var LABEL = { not_attending: "Not attending", attend: "Attending again", stop_reminding: "Stop reminding", resume_reminding: "Resume reminder", ui_action: "Submit", done: "Mark done", reopen: "Reopen", set_status: "Change status", set_stage: "Change stage", ack: "Acknowledge",
                confirm: "Confirm", decline: "Decline", resolve: "Resolve", choose: "Choose" };

  function impactOf(s, tid) {
    var o = { tasks: 0, waiting: 0, deadlines: 0, events: 0, reminders: 0, routines: 0 }, now = Date.now() / 1000;
    function mine(r) { return r.thread === tid && !r.merged_into; }
    (s.queue || []).forEach(function (q) { if (mine(q) && q.status !== "done" && q.status !== "dropped") { o.tasks++; if (q.status === "waiting") o.waiting++; } });
    (s.deadlines || []).forEach(function (d) { if (mine(d) && (d.status === "open" || d.status === "unknown" || !d.status)) o.deadlines++; });
    (s.schedule || []).forEach(function (e) { if (mine(e) && !e.past && e.status !== "cancelled") o.events++; });
    (s.automations || []).forEach(function (a) { if (mine(a) && a.status !== "paused") { if (a.kind === "once") { if (a.when_ts && a.when_ts > now) o.reminders++; } else o.routines++; } });
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
  function archiveDialog(title, impact, body, el) {
    var nodes = [el_("div", "ma-item", title), el_("div", "ma-note", "Still attached: " + impactText(impact)),
                 el_("div", "ma-note", "Archiving does not delete or cancel anything: tasks stay (waiting ones stay tracked), and reminders, deadlines, booked events and routines keep running. To change those, ask Muse.")];
    dialog("Archive this thread?", nodes, [{ label: "Cancel" }, { label: "Archive thread", primary: true, run: function () {
      var b2 = JSON.parse(JSON.stringify(body)); b2.params = b2.params || {}; b2.params.acknowledge_open = true; b2.request_id = rid(); send(b2, el);
    } }]);
  }

  function lookup(section, id) {
    return fetch("/api/state", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (s) {
      var list = s[section] || [];
      for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
      return null;
    });
  }

  function rid() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return "r" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }

  function send(body, el) {
    if (el) { var tn = Date.now(); if (el._sent && tn - el._sent < 1500) return Promise.resolve(); el._sent = tn; }     // a double click is one request
    busy = true;
    if (el) { el.setAttribute("aria-busy", "true"); el.disabled = true; }
    function free() { busy = false; if (el) { el.removeAttribute("aria-busy"); el.disabled = false; } }
    return fetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token() }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, json: j }; }); })
      .then(function (res) {
        free();
        if (res.status === 200) {
          if (body.type === "ui_action") toast(res.json.duplicate ? "已提交过 · 等待 Muse 处理" : "已提交 · 等待 Muse 处理");
          else toast({ not_attending: "Marked as not attending", attend: "Attending again", stop_reminding: "Stop requested — waiting for Muse to confirm", resume_reminding: "Request sent — waiting for Muse to confirm" }[body.type] || "Done");
          window.dispatchEvent(new CustomEvent("muse:refresh", { detail: { action: res.json.action } })); return;
        }
        if (res.status === 401) { clearToken(); toast("Token rejected — re-enter it", true); return; }
        if (res.status === 503) { toast("Actions are disabled on the server", true); return; }
        if (res.status === 409 && res.json && res.json.error && res.json.error.code === "open_work") {
          var det = (res.json.error.details || [])[0] || {}; archiveDialog("This thread", det.impact || {}, body, el); return;
        }
        if (res.status === 410) { toast("此操作已过期，没有提交", true); window.dispatchEvent(new CustomEvent("muse:refresh")); return; }
        if (res.json && res.json.error && res.json.error.code === "unknown_action") { toast("此操作已被撤回，没有提交", true); window.dispatchEvent(new CustomEvent("muse:refresh")); return; }
        toast((res.json && res.json.error && res.json.error.message) || "Action failed", true);
      }).catch(function () { free(); toast("网络错误，没有提交", true); });
  }

  function run(d, el) {
    var body = { type: d.act, section: d.section, id: d.id, request_id: rid() };
    var params = {};
    if (d.status) params.status = d.status;
    if (d.stage) params.stage = d.stage;
    if (d.option) params.option = d.option;
    if (d.act === "ui_action") {
      params.ui_action = d.ua;
      try { params.selection = d.selection ? JSON.parse(d.selection) : {}; } catch (e) { toast("Bad selection", true); return; }
    }
    if (Object.keys(params).length) body.params = params;
    lookup(d.section, d.id).then(function (rec) {
      if (!rec) { toast("Item no longer exists", true); return; }
      var needs = !!rec.sensitive, ua = null;
      if (d.act === "ui_action") {
        ua = (rec.ui_actions || []).filter(function (u) { return u.id === d.ua; })[0];
        if (!ua) { toast("此操作已被撤回，没有提交", true); window.dispatchEvent(new CustomEvent("muse:refresh")); return; }
        needs = !!ua.sensitive;
      }
      if (!needs && d.section === "threads" && d.act === "done") {
        fetch("/api/state", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (st) {
          var imp = impactOf(st, d.id);
          if (!impactText(imp)) { send(body, el); return; }
          archiveDialog(rec.title || d.id, imp, body, el);
        }).catch(function () { toast("Network error — nothing changed", true); });
        return;
      }
      if (!needs && (d.act === "not_attending" || d.act === "stop_reminding")) {
        var att = d.act === "not_attending";
        dialog(att ? "Not attending?" : "Stop reminding?", [el_("div", "ma-item", rec.title || rec.name || d.id),
          el_("div", "ma-note", att ? "This records that you won't go and lets Muse stop its own reminders for it." : "This asks Muse to stop its own scheduled reminder. It shows as 'stop requested' until Muse confirms it really stopped; if Muse can't, you will see that. Deadlines, events and other reminders are not changed."),
          el_("div", "ma-note", att ? "It does not cancel any registration, booking or message. If one exists, Muse will ask you before doing anything outside." : "It does not cancel anything outside.")],
          [{ label: "Cancel" }, { label: att ? "Not attending" : "Stop reminding", primary: true, run: function () { send(body, el); } }]);
        return;
      }
      if (!needs) { send(body, el); return; }
      var nodes = [el_("div", "ma-item", (ua && ua.label) || rec.title || rec.name || d.id)];
      if (ua) (ua.steps || []).forEach(function (st) {
        var sel = (params.selection || {})[st.id], lab = (st.choices.filter(function (c) { return c.value === sel; })[0] || {}).label;
        nodes.push(el_("div", "ma-note", st.prompt + " " + (lab || sel)));
      });
      if (rec.detail) nodes.push(el_("div", "ma-note", rec.detail));
      nodes.push(el_("div", "ma-warn", "Sensitive item. Muse can only ask; this click is your own explicit answer. Muse will not act on money or account security by itself."));
      dialog(((ua && ua.label) || LABEL[d.act] || d.act) + "?", nodes, [
        { label: "Cancel" },
        { label: (ua && "确认") || LABEL[d.act] || "Confirm", primary: true, run: function () { body.sensitive_ack = true; send(body, el); } }
      ]);
    });
  }

  document.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-act]") : null;
    if (!t) return;
    e.preventDefault();
    if (!e.isTrusted) return;                       // scripted clicks never act
    if (busy) return;
    var d = { act: t.getAttribute("data-act"), section: t.getAttribute("data-section"), id: t.getAttribute("data-id"),
              status: t.getAttribute("data-status"), stage: t.getAttribute("data-stage"), option: t.getAttribute("data-option"),
              ua: t.getAttribute("data-ua"), selection: t.getAttribute("data-selection") };
    if (!d.act || !d.section || !d.id) return;
    ensureRoot();
    if (!token()) { askToken(function () { run(d, t); }); return; }
    run(d, t);
  }, true);

  window.MuseActions = { version: 1, hasToken: function () { return !!token(); }, forget: clearToken };
})();
