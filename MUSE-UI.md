# MUSE-UI — autonomous frontend workflow

Content (focus, queue, …) = existing API, no deploy needed (see `MUSE.md`). This file is only for changing **how the dashboard looks**.
Run as user `muse-ui`. Everything outside the workspace is read-only to you by OS permissions.

## You may edit (workspace `/srv/muse-ui/workspace/static/`)
`index.html`, `*.css`, `*.js`, `*.svg/png/jpg/webp/woff2/ico` — flat names `[A-Za-z0-9._-]`, ≤4 levels deep, ≤512 KB each, ≤80 files, no dotfiles, no symlinks/hardlinks.
Optional top-level notes: `README.md NOTES.md CHANGELOG.md` (not deployed). Anything else in the workspace is rejected.

## Rules the deploy gate enforces
- **Page contract**: ids `focus needs-you upcoming in-progress matters timeline-full log routines system-status` (needs-you/upcoming/in-progress are hidden when empty; no placeholder sections), inside views `#home #timeline #log #system` (old `#now`/`#areas` links open Home; `#thread=<id>` expands that thread); `index.html` must load `<script src="/_ui/actions.js"></script>` (protected, not yours).
- **Interaction = markup only.** Make a button with `data-act` (`done reopen set_status set_stage ack confirm decline resolve choose ui_action not_attending attend stop_reminding resume_reminding`), `data-section` (`queue deadlines threads inbox`), `data-id`, plus `data-status` / `data-stage` / `data-option` where needed. The protected script does auth, confirmation dialogs (sensitive items), POST and refresh. Your JS must never call `/api/action`, touch the token, or send writes; only `data-ui` buttons (view-only toggles) and `#theme-toggle` may be otherwise. No inputs/forms/contenteditable.
- Muse-declared actions (`threads[].ui_actions`): the shipped component renders them with local multi-step choice. If you restyle it keep the contract: final submit is a `data-act="ui_action"` button with `data-section="threads" data-id=<thread> data-ua=<action id> data-selection='<JSON {step:value}>'`; steps use `data-ui="ua-start|ua-pick|ua-back|ua-cancel"` (local only).
- Read-only otherwise: GET `/api/state` only; no `/api/update`, `Authorization`, POST/PUT, XHR/WebSocket, `innerHTML`/`eval`/`document.write` (use `textContent`/`createElement`), no external URLs/fonts/CDNs (CSP also blocks them).
- No JS/console errors; no horizontal scroll at 1280px and 390px in light and dark; text ≥ 11px; contrast ≥ 4.5:1; sensitive items visibly flagged.
- Density: Home shows ≤5 asks, ≤6 in-progress, ≤8 upcoming rows; All Matters areas collapsed by default (open state remembered per browser); a thread's default action is Open, archiving is a secondary entry that shows an impact summary; log ≤10 entries by default; no internal IDs/raw enums on primary views; hostile text renders escaped; same-thread/same-instant items de-duplicated; page polls without reload.
- Display only: never delete/rewrite records, never invent priorities, focus or completion states, never show a placeholder date as confirmed.
- Interactions are verified end-to-end (Done, sensitive confirm dialog, option choice, stage change really change backend state; scripted clicks are ignored; no token → unlock prompt).

## Workflow
```
cd /srv/muse-ui/workspace && $EDITOR static/app.css     # edit; commit with git if you like
muse-ui-deploy --preview        # unvalidated preview with a COPY of live content: http://127.0.0.1:5051 (also on Tailscale)
muse-ui-deploy --check          # dry run: inspect + full validation, deploys nothing
muse-ui-deploy                  # inspect → validate → deploy → health/browser check → auto-rollback on failure
muse-ui-deploy --preview-stop
```
Backend/API tests run inside the gate (`pytest`, 15 tests); you cannot skip or edit them.

## Failures & revert
- `muse-ui-deploy --report` prints the last run; details and screenshots are in `/srv/muse-ui/reports/latest/` (`checks.json`, `pytest.log`, `*-fold.png`, `*-full.png`, incl. dark theme).
- Exit codes: 2 rejected (allowlist), 3 validation failed (nothing deployed), 5 post-deploy failure (auto-rolled back), 0 ok.
- `muse-ui-deploy --status` shows live vs known-good; `muse-ui-deploy --rollback [RELEASE_ID]` restores the previous (or a named) release in `/srv/muse-ui/releases/`. The initial release is `r0000-initial`.

## Forbidden (blocked by permissions, not by trust)
Backend Python, DB/schema/data, auth/tokens/`.env`, Docker/compose/ports/network, other apps, `/opt/muse-ui` (deploy tool, validator, config), `/srv/muse-ui/{live,releases,reports,state.json}`, sudoers. Do not try to work around the gate; the only privileged path is `muse-ui-deploy`.

## Need a backend / API / infra change?
You cannot approve it yourself. Write the request to `/srv/muse-ui/workspace/NOTES.md` (what, why, files, risk) and tell the owner (journal / notification)/journal entry saying `APPROVAL NEEDED: <one line>`. The user decides and applies it. A frontend change that needs a new API field counts as a backend change.

## Admin (user, one-time)
`sudo bash ui-deploy/install.sh` creates users `muse-ui` (workspace only) and `muse-deploy` (runs the gate, in docker group), root-owned `/opt/muse-ui`, sudoers rule for the single command, and mounts `/srv/muse-ui/live` read-only into the dashboard. Rebuild the validator after backend changes by re-running it.
