# Muse Dashboard

A small, **read-only personal operations dashboard** for an autonomous agent (called "Muse" here).
The agent is the only writer of content (through a bearer-token API); the human watches and interacts by **clicking** whitelisted actions — no typing.

FastAPI + SQLite (WAL) + vanilla JS (no build step), shipped with Docker Compose. No server-side LLM, no external services.

## Security model — read this first
This is a **single-user tool for a private network** (localhost / Tailscale / LAN). There are no user accounts or login pages.
- Three independent bearer tokens, all set in `.env`: `MUSE_TOKEN` (agent writes `/api/update`), `MUSE_HUMAN_TOKEN` (browser clicks `/api/action`, entered once per browser) and `CHRONICLE_TOKEN` (optional phone geofence events).
- **Do not expose port 5050 to the public internet.** The page itself is readable by anyone who can reach it.
- `ui-deploy/` is an optional, advanced sandbox that lets the agent edit the frontend through a validating deploy gate; it needs root and the Docker group — read it before installing.

## Quick start
```bash
cp .env.example .env            # then replace every change-me-* value with long random strings
docker compose up -d --build
curl -s localhost:5050/api/health
```
Open http://localhost:5050. Publish something:
```bash
curl -s -X POST localhost:5050/api/update -H "Authorization: Bearer $MUSE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"focus":{"title":"Write the report","reason":"Due Friday"},"journal":{"kind":"note","message":"hello"}}'
```
Agents can use the stdlib client in `client/museboard.py`. The full API, data model and agent protocol are in **`MUSE.md`**; the frontend-sandbox contract is in **`MUSE-UI.md`**.
Defaults assume `TZ=America/New_York` (display timezone is `state.tz`, set via `TZ` in `.env`); some tests use real DST dates for that zone.

## Layout
```
app/            FastAPI backend (main.py routes+auth, db.py storage, schema.py validation)
app/protected/  click/auth/confirm layer served at /_ui/ (not editable by the frontend sandbox)
app/static/     frontend (index.html, app.css, app.js, theme.js)
client/         museboard.py — stdlib client for the agent
tests/          pytest suite
ui-deploy/      optional frontend deploy gate (validator, installer, permission test)
```

## Tests
```bash
docker run --rm -v "$PWD":/srv -w /srv $(docker build -q -f Dockerfile.test .) pytest -q
```

## Features
Threads grouped by life area · focus · needs-you inbox (confirm/decline/choose, sensitive items need a dialog) · 7-day view · timeline · append-only journal and action log · dark mode · phone geofence context line.

## License
MIT — see `LICENSE`.
