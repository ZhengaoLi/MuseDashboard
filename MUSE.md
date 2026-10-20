# Muse Control — how to publish (you are the only writer; the human answers by clicking)

**URL** `http://<dashboard-host>:5050` · **Write**: `POST /api/update` + `Authorization: Bearer $MUSE_TOKEN` · **Read**: `GET /api/state` · Health: `GET /api/health`

```python
from museboard import update   # env MUSE_URL, MUSE_TOKEN (or ~/.museboard)
update(threads=[{"id":"bike","title":"Buy a used bike","area":"purchase","template":"purchase",
                 "stages":["Browsing","Test ride","Negotiation","Purchase"],"stage":"Browsing"}],
       queue=[{"id":"bike-ride","title":"Book a test ride","thread":"bike","status":"active"}])
update(inbox=[{"id":"new_payee","title":"New payee added to your bank account — was this you?","kind":"confirm","sensitive":True}], journal={"kind":"alert","message":"…"})
```
CLI: `python museboard.py payload.json`. Retries are safe (idempotency key automatic).

## Model: organise by *real-world thing*, not by record type
- **thread** = one real-world item (a paper, a purchase, a course…). Everything about it (tasks, deadlines, events, reminders, projects) links to it via `thread`. Stable `id`, never the title. Create the thread first (same request is fine).
- **area** (stable enum): `research course career admin purchase logistics health life system`. A thread has one; records inherit it (set `area` only on records without a thread).
- **One fact, one record.** Don't create a deadline AND an event AND a reminder for the same moment: keep one (event preferred); the UI folds same-thread/same-instant items and attaches `kind:"once"` reminders to their event. If you must keep an exact duplicate, set `merged_into:"<other id in same section>"` — it is hidden but kept (never delete history).
- Finished/dropped things: set `status` to `done`/`dropped` (threads too). They move to Archive automatically. Don't delete.

## Sections & semantics
- Update body may carry any of: `focus threads inbox queue deadlines projects schedule automations journal heartbeat actions_seen`. Omitted = untouched; `[]` = no-op; one transaction, all-or-nothing; server timestamps.
- `focus`: object, **replaces** (`null` clears). Others upsert by `id`; partial updates merge; `null` clears a field; `{"id":..,"delete":true}` removes (refused while still referenced).
- Common optional links on queue/deadlines/projects/schedule/automations: `area`, `thread`, `merged_into`. Dates: `YYYY-MM-DD` or ISO datetime (naive = America/New_York).

| section | fields (* required on create) |
|---|---|
| threads | title, **area\***, template generic\|paper\|review\|course\|application\|purchase\|order\|cycle\|log\|admin\|event, status active\|waiting\|paused\|done\|dropped, stage (must be in stages), stages [≤8], summary, next_step, basis |
| inbox | title, detail, kind decision\|confirm\|alert\|todo, status open\|…, sensitive, options [≤…], area, thread, due, basis |
| queue | title, status todo\|active\|waiting\|blocked\|done\|dropped\|unknown, priority p0-p3, order, **planned_for** (local date `YYYY-MM-DD`), **time_hint** morning\|afternoon\|evening\|tonight (needs planned_for), project, note, basis |
| deadlines | title, **due\***, status open\|done\|dropped\|unknown, note, basis |
| projects | name, status, progress, next_step, blockers, note, basis |
| schedule | title, **start\***, end, location, status confirmed\|tentative\|unconfirmed\|cancelled, note, basis |
| automations | name, frequency, **kind daily\|weekly\|monthly\|once\|always**, last_run, next_run, status ok\|running\|failed\|paused\|unknown, note, basis |
| journal (append-only) | message, kind observed\|planned\|recommended\|decision\|note\|**digest**\|**alert**, title, area, thread |

`basis` = observed (verified) \| planned \| recommended \| unknown. A reminder sent ≠ task done; keep uncertainty explicit. A date-only deadline with `basis:"unknown"` is shown as "date not confirmed".
**digest** = your daily email read (one entry per day, `title` = headline). **decision** = a decision + why (long-term value). **alert** = something to flag; things that need the human go in `inbox`.

## Planning a task for a day (`planned_for` / `time_hint`) — keep these four things apart
| | field | meaning |
|---|---|---|
| formal deadline | `deadlines[].due` | someone else's/official due date. Never invent one. |
| planned day | `queue[].planned_for` (+ `time_hint`) | **the human intends to do it that day**. Date only; `time_hint` is a part of day, never a clock time. |
| reminder | `automations[]` `kind:"once"` | Muse pings at an exact time. |
| execution state | `queue[].status` | todo/active/waiting/blocked/done — independent of the plan |
(`priority` is a fourth, independent axis: it only sorts, it never decides "today".)
- "今晚完成 X" → on the existing/new task: `planned_for = <today's local date>`, `time_hint:"tonight"`; "明早" → tomorrow's date + `morning`. Date is the human's local day (`state.today` is the server's current local date — use it).
- **Do not flip `todo` → `active` just because it is planned.** Planned tasks (any status except done/dropped) appear in Now → *Today's plan*; Timeline shows future plans labelled **Plan** (no fake times). Overdue plans fall into a collapsed "Missed plans" list — re-plan them (new `planned_for`) or drop them; clear with `planned_for:null`.
- When you plan something: also update the thread's `next_step`, and **fix stale tasks/notes that contradict the new plan** (e.g. an old "don't replace yet" note). One plan = one task; don't create a deadline/event/reminder to make it show.
- Mark finished work `done` (the plan then disappears from Today). Never create a `planned_for` the human didn't state.

## Context from a phone automation (geofence) — observation, not instruction
The phone (e.g. an iOS Shortcut) sends each geofence event to Muse Control directly (`POST /api/context/events`, its own `CHRONICLE_TOKEN`; no Muse involvement, no LLM). The dashboard's Now page shows one quiet line while the latest event is fresh (TTL 30 min). **It never wakes you.** Read it when you need context: `state.context` → `{ttl_minutes, latest, recent}`; each event: `type` geofence_enter|geofence_exit, `place` home|office, `observed_at` (when it happened, UTC), `received_at`, `age_seconds`, `fresh`.
- **Observation** = "the phone reported geofence_enter(home) at 22:14". Only that. **Inference** = what you conclude (label it as such, `basis:"unknown"`/"planned"). **Memory** = what you already know about the person. Keep the three apart.
- `geofence_enter(home)` does **not** prove the user is resting, eating, or done working; `fresh:false` means it is no longer a live signal. Geofences are weak (iOS delivers late; nearby places overlap). When stronger evidence (e.g. computer activity) disagrees, trust that.
- Don't write short-term location events into long-term Memory, threads, journal or inbox, and **don't send notifications about arriving/leaving**. Use them only as quiet context when choosing *when* to act on something that is already worth acting on (deadline, planned task…).
- The endpoint cannot change focus/threads/tasks/deadlines/reminders/ui_actions; it is not a Muse update (does not refresh "last Muse update").
- v1 stores Home/Office enter/leave only (`arrived_home left_home arrived_office left_office`); other events (e.g. gym/woke_up) are accepted and ignored. Events: dedupe by `event_id` or (type, place, time), same event within 10 min keeps the first, out-of-order events never replace a newer state, timestamps >2 min in the future or >48 h old are rejected, rows kept 30 days.

**Phone setup (once per geofence automation):** add an action *Get Contents of URL* → `http://<dashboard-host>:5050/api/context/events`, Method POST, Headers `Authorization: Bearer <CHRONICLE_TOKEN>` and `Content-Type: application/json`, Request Body JSON = `{"event":"arrived_home","device":"iphone","ts":"<ISO time with offset>"}`. The phone must be able to reach the dashboard (e.g. over Tailscale). Failures are silent: Muse Control being down just means no line is shown.

## Human interaction (clicks) — read this
The human clicks buttons in the dashboard (Done, stage, choose an option, Confirm/Decline). Each click is stored as an **action** and applies one state change; see `state.actions` (`seen:false` = not yet processed by you) and `state.unseen_actions`. After handling, send `{"actions_seen": <max action id>}`.
- **A click is a one-off answer, not a preference.** Never infer standing preferences from clicks; only from explicit statements.
- Your own records can be overwritten by a click (e.g. status→done). Re-read state before pushing a field you may have changed; don't revert a click without telling the human.
- `inbox` = things only the human can decide: `kind:"confirm"` (yes/no), `"decision"` + `options` (one click picks; stored in `choice`), `"alert"` (acknowledge), `"todo"`.
- **Sensitive** (`sensitive:true`: money, account security, identity): you may only ASK. The server rejects you setting it to confirmed/declined/acknowledged, setting `choice`, or un-setting `sensitive`. Never act on money/account matters yourself; do not put account numbers/phone numbers in titles or details.
- Humans cannot publish and you cannot click: separate tokens.


## Declared actions (`threads[].ui_actions`) — let the human act on your intent with 2–3 clicks
You decide, from Memory + the thread's stage, what the human can do *right now*; the dashboard only renders it. Publish ≤3 per thread (the list **replaces** the old one; `[]`/`null` withdraws all):
```python
update(threads=[{"id":"bike","ui_actions":[{
  "id":"book_ride","label":"Book a test ride","type":"intent","intent":"schedule_ride",
  "steps":[{"id":"slot","prompt":"When?","choices":[{"value":"am","label":"Morning"},{"value":"pm","label":"Afternoon"}]}],
  "expires_at":"2026-10-20","sensitive":False}]}])
```
- `id`/`intent`/step ids: `[a-z][a-z0-9_]*`. ≤3 steps, 2–5 choices each (`value` `[A-Za-z0-9_-]`). No `steps` = a one-click button. Browser shows `Book a test ride → Morning/Afternoon → Confirm` **locally**; only the final Confirm sends **one** action.
- The server stores your declaration and only accepts that exact id + one valid value per step (anything else → 422). Expired/withdrawn → 410/422. Intent ids implying external side-effects (pay/purchase/send/email/delete/order/wire/post/sign…) are rejected: ask via a sensitive `inbox` item instead. `sensitive:true` forces a confirmation dialog.
- An intent is **a request to you, not authority**: act only within your existing permissions/approvals; never pay/buy/send because of an intent.
- **Consume** (60 s hook): read `state.actions` where `type=="ui_action"` and `status` is `accepted`/`seen`; fields: `target` (thread id), `params.intent`, `params.selection` (`{step: value}`), `params.selection_labels`, `ts`, `id`. Then send `{"actions_seen": <max id>}` when you've read them and, when truly done, `{"action_results":[{"id":12,"status":"completed|declined|unable","note":"short result"}]}` (first result per action wins). The UI shows: *已提交·等待 Muse* → *Muse 已读取* → *✓ 已完成：note*. Muse offline? The action stays queued until you return.
- Same thread+action+selection can't be queued twice while unprocessed. Withdraw or re-declare actions after you consume them so stale buttons don't linger.

## Home layout & archiving a thread (v2.3 Lite)
Home = **MUSE RECOMMENDS** (your `focus`) · NEEDS YOU · UPCOMING (today's plan + the next days, one list) · IN PROGRESS (active tasks) · ALL MATTERS (every thread by area, collapsed). Sections with nothing to show are hidden. Timeline/Log/System are separate pages.
- **Link the focus**: `focus.project` = a **thread id** (and/or `focus.task` = queue id). A linked recommendation gets an *Open* button that expands the thread; without a link it is shown plain — never invent an id.
- **Archiving a thread** (`done` on a thread, called *Archive thread…* in the UI, secondary entry) is checked by the **backend**: if the thread still has open tasks the server answers `409 open_work` with the impact unless the human acknowledged it. It **never cascades**: tasks, deadlines, booked events, reminders and routines keep their state and keep running. The action row carries `params.impact` (`tasks, waiting, deadlines, events, reminders, routines`) = what was still attached.
- **Marking a task Done never archives its thread.**
- On seeing an archive action: leave waiting tasks you are tracking; decide quietly what to do with reminders/routines that only served that thread; **never cancel an external booking/order/appointment because a thread was archived** — ask through a `sensitive` inbox item.

## Saying "no": Not attending / Not doing / Stop reminding (v2.4)
Row menus (`···`) let the human decline things. These are **internal statements, never external cancellations**:
- **Task → Not doing** = `status:"dropped"` (kept, restorable, not Done). **Event → Not attending** = `attendance:"not_going"` on the schedule item (the event's own `status` is untouched; `status:"cancelled"` means *the organiser cancelled it* — don't use it for "I won't go"). **Reminder → Stop reminding** = a **request to you**: the click changes nothing on the mirror (`status` stays) and the UI says *Stop requested · waiting for Muse to confirm*. Each has an inverse (Restore / Attending again / Cancel stop request or Resume reminder).
- They arrive in `state.actions` (`not_attending`, `attend`, `stop_reminding`, `resume_reminding`, `set_status`) with `params.internal_only` for the first four. **Your duty**: for `stop_reminding`, actually stop the **native Scheduled Task**; only then set the mirror `status:"paused"` and send `action_results` `completed` (+ note) — the user is shown "stopped" only after that. If you cannot stop it, send `action_results` `unable` (or `declined`) with a short reason and leave the mirror alone: the UI then shows *Muse couldn't stop this reminder: <reason>*. `resume_reminding` works the same way (re-enable the native task, set `status:"ok"`, report). **Withdrawing a stop ("Cancel stop request") is only a request**: if you have not stopped the native task yet, don't; if you already stopped it, re-enable it or report `unable`; if you are mid-way, finish and report the true final state. Report a result for **both** actions and keep the mirror equal to the real native task — never mark a withdraw `completed` unless the task really is unchanged/active. For `not_attending`: stop your own reminders for the event the same way and report. **Never cancel anything external (registration, booking, order, message) on your own** — if one exists, ask via a `sensitive` inbox item and wait. The human's decision is not Done and not a preference.
- Don't overwrite `attendance` / a paused reminder back without telling the human (journal).

## Time zones, deadlines and planned dates — hygiene
- The dashboard shows everything in **`state.tz` (America/New_York)**, whatever device looks. Naive datetimes (no offset) mean that zone with real DST rules; **offsets you write must be the real ones for that date** (EDT `-04:00` until 2026-11-01 02:00, EST `-05:00` after; `2026-11-01T09:00:00-04:00` is wrong = 08:00 EST). Prefer naive local times or compute offsets with a tz database — never hard-code `-04:00`.
- **AoE deadlines**: "10/21 AoE" = 2026-10-22T07:59:00-04:00 (23:59 UTC-12). Store the real instant.
- A **formal deadline** is an external due date. Tentative/unconfirmed intentions (e.g. "test-ride day", "pay", "DMV") are **not** deadlines: use `queue[].planned_for` (+ `time_hint`) or a `schedule` item with `status:"tentative"` until confirmed. Keep reminder text in line with the current plan (e.g. "check email first; call only if no reply").

## Scheduled Tasks (your own scheduler) ↔ Dashboard — mirror, don't duplicate
The dashboard **cannot read your Scheduled Tasks** (they live in your environment) and never notifies anyone. So the Scheduled Task is the source of truth for *firing*; the dashboard record is a **display-only mirror that you maintain**:
- In the **same step** that you create / change / delete a Scheduled Task, upsert/delete one `automations` record (`kind:"once"` or the recurring kind, `next_run` = its real next fire time, `thread` if it belongs to one, `id` derived from the task id, e.g. `st-<taskId>`). Never create a second notifying reminder to make something "show up".
- When the task fires or is removed, update/delete its mirror; reconcile on your daily pass (compare your task list with `state.automations`) and report any drift. A dashboard-only record with no real task behind it is a bug — don't leave one.
- If you cannot list your tasks, say so; do not claim two-way sync. (Known gap today: the left-eye lens task 2026-11-09 exists as a *deadline* but has no reminder mirror.)

## Errors
`{"ok":false,"error":{"code","message","details":[{"path","msg"}]}}` — 401 bad token, 422 invalid (nothing applied; e.g. unknown thread, stage not in stages), 409 idempotency conflict.
Success: `{"ok":true,"at","created":["queue:x"],"updated":[],"unchanged":[],"deleted":[],"not_found":[],"journal":n}`.
