# Muse Monitor (wall display)

A read-only page for a 1920×1080 screen on this machine. Nothing to click or scroll: the clock, the recommendation, who/what needs you, today and tomorrow, deadlines, tasks, waiting items, projects, later this week and today's papers are all on one screen.

- Address: `http://127.0.0.1:5050/static/monitor.html` (the normal dashboard stays at `/`).
- Data: `GET /api/state` every 10 s; the clock ticks every second. It makes no other request and sends nothing.
- If a panel has more rows than fit, the rest is summarised as "+N more" (never scrolls).
- Header: counts of what needs you / is due within 3 days / is to do / is on today; a dot shows how recently Muse updated (amber/red when stale or the connection is lost; the last data stays on screen, dimmed).
- A window that is not 1920×1080 is scaled to fit. The mouse pointer is hidden. The page reloads itself once a day around 04:00 so a new release is picked up.

Open it full-screen in a kiosk browser, for example:

    chromium --kiosk --noerrdialogs --disable-infobars --incognito http://127.0.0.1:5050/static/monitor.html
