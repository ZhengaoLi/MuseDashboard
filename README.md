# Muse Dashboard

A small, **read-only personal operations dashboard for an autonomous agent** (called "Muse" in the docs).
The agent is the only writer of content (bearer-token JSON API); the human watches and interacts only by **clicking** whitelisted actions — no typing.

FastAPI + SQLite (WAL, single worker) + vanilla JS (no build step), packaged with Docker Compose. No server-side LLM, no external services, no telemetry.

> **If you are an agent deploying this:** follow **[Part A](#part-a--deploy-the-dashboard-required)** top to bottom, run the verification block, then read `MUSE.md` (how to publish) and report back to your human. **Part B** (frontend sandbox) is optional and needs root — do it only if your human asks for it.

---

## 0. Security model (read first)

- Single-user tool for a **private network** (localhost / Tailscale / LAN). There are no accounts or login pages. The page and `GET /api/state` are readable by anyone who can reach the port.
- **Never expose port 5050 to the public internet** and never bind it to a public/shared interface (e.g. campus or café Wi-Fi). The compose file binds only `127.0.0.1` and one extra address you choose.
- Three **independent** bearer tokens, all in `.env` (never commit it — it is git-ignored):

| Variable | Used by | Purpose | Required |
|---|---|---|---|
| `MUSE_TOKEN` | the agent | `POST /api/update` (writes all content) | **yes** (compose refuses to start without it) |
| `MUSE_HUMAN_TOKEN` | the human's browser | `POST /api/action` (clicks: done / confirm / archive …). Entered once per browser, kept in `localStorage`. | recommended (≥16 chars; without it the buttons are disabled) |
| `CHRONICLE_TOKEN` | a phone automation | `POST /api/context/events` (optional geofence line) | optional (≥16 chars; unset = endpoint returns 503) |

  Use ≥32 random characters each, all different. The agent must never be given `MUSE_HUMAN_TOKEN`.

---

## Part A — Deploy the dashboard (required)

### A1. Prerequisites
- Linux host (any distro) with **Docker Engine ≥ 24** and the **Compose v2 plugin** (`docker compose version` must work), and `curl`.
- The user running the commands must be able to run `docker` (member of the `docker` group, or use `sudo`).
- Optional: [Tailscale](https://tailscale.com) if the human wants to open the dashboard from a phone/laptop.
- No Python install is needed on the host; everything runs in containers.

### A2. Get the code
```bash
git clone git@github.com:ZhengaoLi/MuseDashboard.git      # or https://github.com/ZhengaoLi/MuseDashboard.git
cd MuseDashboard
```

### A3. Create `.env`
```bash
cp .env.example .env
# fill the three tokens with fresh random values (keep the file private):
for k in MUSE_TOKEN MUSE_HUMAN_TOKEN CHRONICLE_TOKEN; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 24)|" .env
done
chmod 600 .env
```
Then edit the remaining values:

| Variable | Default | Meaning |
|---|---|---|
| `TZ` | `America/New_York` (example) | **Set to the human's IANA timezone** (e.g. `Europe/Berlin`). All times in the UI are shown in this zone; it is also exposed as `state.tz`. Wrong TZ = events displayed at wrong hours. |
| `MUSE_BIND_TAILSCALE` | `127.0.0.2` (harmless placeholder) | Extra address to publish port 5050 on. For remote access set it to the host's Tailscale IPv4: `tailscale ip -4`. Must be an address that exists on the host *when Docker starts*, otherwise the container fails to start. |
| `FRESH_MINUTES` / `STALE_MINUTES` | `60` / `360` | Header freshness: fresh ≤ 60 min since the agent's last update, aging ≤ 6 h, stale beyond. (Web server up ≠ agent alive.) |
| `CONTEXT_TTL_MINUTES` | `30` | How long a phone geofence observation counts as "fresh". (Set in compose `environment` if you want to change it.) |

Give the human their `MUSE_HUMAN_TOKEN` through a private channel (they paste it once in the browser when they first click a button). Keep `MUSE_TOKEN` for yourself only.

### A4. Start
```bash
docker compose up -d --build
docker compose ps                      # muse-control should be "running"
```
Data lives in the Docker volume `muse_data` (SQLite at `/data/muse.db`) and survives rebuilds/restarts. `restart: unless-stopped` brings it back after reboot as long as Docker starts on boot (`sudo systemctl enable docker`).

### A5. Verify (all four must succeed)
```bash
set -a; . ./.env; set +a
curl -fsS localhost:5050/api/health                                   # {"status":"ok","db":"ok",...}
curl -fsS -o /dev/null -w "page %{http_code}\n" localhost:5050/       # page 200
curl -fsS -X POST localhost:5050/api/update \
  -H "Authorization: Bearer $MUSE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"focus":{"title":"Dashboard is live","reason":"deployment check"},"journal":{"kind":"note","message":"deployed"}}'   # {"ok":true,...}
curl -s -o /dev/null -w "no-token %{http_code}\n" -X POST localhost:5050/api/update -H 'Content-Type: application/json' -d '{}'   # must be 401
```
If you set `MUSE_BIND_TAILSCALE`, also check `curl -fsS http://<that-ip>:5050/api/health` from the host, and ask the human to open `http://<that-ip>:5050` on their device.

Replace the test focus with real content afterwards (`focus` is replaced by each update; the journal line is append-only and stays — that is fine).

### A6. Tell the agent where to publish (configure yourself)
- Install the stdlib client: copy `client/museboard.py` onto your `PYTHONPATH` (single file, no dependencies). It reads `MUSE_URL` (default `http://127.0.0.1:5050`) and `MUSE_TOKEN` from the environment, or from `~/.museboard` as `{"url": "...", "token": "..."}`.
  ```python
  from museboard import update
  update(focus={"title": "Write the report", "reason": "Due Friday"}, journal="started")
  ```
  Or without Python: `POST /api/update` with `Authorization: Bearer $MUSE_TOKEN`.
- **Read `MUSE.md` now** — it is the agent-facing contract: data model (threads, areas, inbox, queue, deadlines, schedule, automations, journal), update semantics (partial upserts, idempotency keys, all-or-nothing transactions), how to read human clicks from `state.actions` and acknowledge them with `action_results`, and the rules for sensitive items.
- Poll `GET /api/state` (no auth) to read what the human sees and what they clicked. Send a `heartbeat` (or any update) at least every `FRESH_MINUTES` so the header stays "fresh".

### A7. Start on boot (optional but recommended)
Docker's `restart: unless-stopped` is enough on most hosts. If the Tailscale address must exist before the port can bind, use the systemd template:
```bash
sed -e "s|__USER__|$USER|" -e "s|__PROJECT_DIR__|$PWD|" ui-deploy/muse-control.service.tpl | sudo tee /etc/systemd/system/muse-control.service
sudo systemctl daemon-reload && sudo systemctl enable --now muse-control
```
(`$USER` must be in the `docker` group.)

### A8. Operate
```bash
docker compose logs -f muse-control      # logs
docker compose up -d --build             # apply code changes (data is kept)
docker compose down                      # stop (volume kept); add -v ONLY to delete all data
docker run --rm -v "$PWD":/srv -w /srv $(docker build -q -f Dockerfile.test .) pytest -q   # test suite (110 tests)
```
Backup: `docker run --rm -v muse_data:/data -v "$PWD":/out alpine tar czf /out/muse-data.tgz /data` (the DB is WAL; for a perfectly consistent copy stop the container first).

### A9. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `set MUSE_TOKEN in .env` on `compose up` | `.env` missing or token empty. |
| `cannot assign requested address` on start | `MUSE_BIND_TAILSCALE` is not an address on this host (Tailscale not up yet). Use `127.0.0.2` or fix the IP. |
| `401` on `/api/update` | Wrong/missing `Authorization: Bearer <MUSE_TOKEN>` header, or you changed `.env` without `docker compose up -d`. |
| Buttons in the UI do nothing / ask for a token | The human hasn't entered `MUSE_HUMAN_TOKEN`, or it is shorter than 16 chars (buttons disabled). |
| Times look wrong by N hours | `TZ` in `.env` is not the human's timezone. |
| Browser shows an old UI after an update | Hard refresh; the server already sends `no-cache`. |
| Header says "stale" | You haven't published within `STALE_MINUTES`; send a `heartbeat`. |

---

## Part B — Let the agent edit the frontend (optional, needs root)

By default the UI is what's in `app/static/` baked into the image. Part B installs a **sandbox + deploy gate** so the agent can change HTML/CSS/JS on its own, safely. Skip it unless the human asks. It creates two system users, a root-owned `/opt/muse-ui`, a state tree `/srv/muse-ui`, and a single sudoers rule; it never touches the database, volume or tokens, and is idempotent.

1. Edit `ui-deploy/config.json.tpl` → set `"tz"` to the human's timezone (the validator checks the UI in a different timezone to prove it never depends on the browser's).
2. As an admin: `sudo bash ui-deploy/install.sh` (set `MUSE_UI_TS_IP=<tailscale ip>` if Tailscale is installed but you want to override). It appends `COMPOSE_FILE=docker-compose.yml:docker-compose.ui.yml` to `.env` and restarts the stack so the container serves `/srv/muse-ui/live` read-only.
3. Run the agent's frontend work as user `muse-ui` (`sudo -u muse-ui -i`). It can write **only** `/srv/muse-ui/workspace/static/` (plus `CHANGELOG.md`, `NOTES.md`, `README.md`). It has no Docker access, no sudo, cannot read `.env`, and cannot change the backend, compose file or protected click layer (`/_ui/actions.js`).
4. The only privileged command it may run is `muse-ui-deploy` (flags: default = validate + deploy, `--check`, `--preview`, `--preview-stop`, `--status`, `--rollback [ID]`, `--report`). Deploy = snapshot → allow-list and banned-pattern scan → tests + Playwright checks (desktop + mobile, no JS errors, read-only UI, no external requests) in throw-away containers → swap `live` → post-deploy health check → **automatic rollback** on failure.
5. Prove the boundary on your own host (optional): `docker run --rm -v "$PWD":/proj:ro ubuntu:24.04 bash /proj/ui-deploy/perm-test.sh` (expects `RESULT: 24 passed, 0 failed`).

The full contract the agent works under is in **`MUSE-UI.md`**. Known limit: `muse-deploy` is in the `docker` group (root-equivalent), so `/opt/muse-ui` must stay root-owned.

---

## Optional: phone geofence context line
A phone automation (e.g. an iOS Shortcut) can POST `{"event":"arrived_home","device":"iphone","ts":"<ISO time with offset>"}` to `http://<dashboard-host>:5050/api/context/events` with `Authorization: Bearer <CHRONICLE_TOKEN>`. The dashboard then shows one quiet "Phone detected …" line while the event is fresh. It never notifies the agent; details and rules of use are in `MUSE.md`.

## Layout
```
app/            FastAPI backend (main.py routes+auth, db.py storage, schema.py validation, redact.py secret scrubbing,
                trajectory.py event log + export, alphaxiv.py optional read-only paper source)
app/protected/  click/auth/confirm layer served at /_ui/ (not editable by the frontend sandbox)
app/static/     frontend (index.html, app.css, app.js, theme.js; monitor.html = read-only 1920x1080 wall display)
client/         museboard.py — stdlib client for the agent; papers.py — daily paper collector
tests/          pytest suite (API, auth, idempotency, append-only journal, XSS, DST/timezones, actions)
ui-deploy/      optional frontend deploy gate (installer, validator, permission test, systemd template)
MUSE.md         agent-facing API + protocol          MUSE-UI.md   frontend-sandbox contract
MONITOR.md      wall-display page                    ALPHAXIV.md  optional alphaXiv candidate source
```

## Features
Threads grouped by life area · focus · "needs you" inbox (confirm / decline / choose; sensitive items need a dialog) · 7-day view · timeline · append-only journal and action log · dark mode · optional phone geofence line · **Papers** tab (≤5 recommended papers/day, deduplicated across sources, verifiable quote, human feedback) · secret redaction on write and read · append-only trajectory event log with offline export · read-only wall monitor at `/static/monitor.html` (see `MONITOR.md`).

## License
MIT — see `LICENSE`.
