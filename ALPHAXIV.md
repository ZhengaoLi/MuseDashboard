# alphaXiv as an extra Papers candidate source (merged into main for v2.7.0)

## Where Muse's collection actually runs (evidence, 2026-10-24)
Not on this server: no Muse process, scheduler entry or script here; the hourly pushes (:38-:47) have **no matching SSH logins**, `muse-ui` never logged in, SSH logins are the owner's occasional sessions from the Mac. Muse's "native" scheduled tasks run in Muse's own platform and reach `/api/update` over the network with the Muse token. Its Memory is separate (a static export sits in the owner's home; it is not live and was not read).
Consequence: a key stored on this server cannot be used *by* Muse's tasks, and a key stored in Muse's environment could be used to call **any** of the 21 MCP tools (including follow/delete/move) because alphaXiv API keys have no scopes. A code-level allowlist is only real if the key never leaves the server.

## Design: the dashboard server holds the key and exposes a read-only door
`app/alphaxiv.py` (stdlib): default-deny allowlist = `get_for_you_feed`, `get_hot_feed` (free of assistant quota per the docs). Any other tool name is rejected **before any network use**; arguments are validated exactly; the key (env `ALPHAXIV_API_KEY`, in the already-protected `.env`) goes only to `api.alphaxiv.org` over https, redirects refused, 1 MB response cap, never logged/returned/in an error. Cache 6 h and a hard cap of 4 upstream calls per feed per day. Every failure is a short code.
`GET /api/papers/source/alphaxiv?feed=hot|for_you&limit=N` (Muse token only; human token and anonymous: 401) returns `{ok, ids:[{paper_id, arxiv_id|null}], ...}` or `{ok:false, error:code}` with status 200, so the daily routine just continues with arXiv / Hugging Face / Scholar. Ids are classified, not assumed: only ids that look like arXiv ids are mapped (and used for de-duplication); others (e.g. uploaded papers) stay unmapped and are skipped.
`client/papers.py: collect(alphaxiv=(dashboard_url, muse_token))` is optional; metadata comes from public arXiv; the source link is `https://www.alphaxiv.org/abs/<id>`. De-duplication is the existing logic: a repeat only adds the alphaXiv link, never rewriting the reason, rank or the human's rating. alphaXiv is a candidate source only; Muse still judges relevance.
No new cron, service or database. Paid/quota tools (`discover_papers`, `get_paper_content`, `answer_pdf_queries`) are deliberately NOT enabled; adding one is a reviewed edit of `ALLOWED`.

## Verified with the real key (2026-10-24, read-only)
- Hot (50): 43 map to an arXiv id, median age 2 days; For You (50): 49 map, median age 18 days, 0 overlap with the Hugging Face daily list; 90/90 mapped ids exist on arXiv; the unmapped ones are `YYMM.some-slug` link-blog style ids (hence strict arXiv-id matching, never "assume universal id = arXiv id"). No version suffixes. ~1 s per call; headers show only a global rate limit (1.5M/h), no personal quota.
- **Key scope:** `tools/list` with this key shows **23 tools, 9 of them write/destructive** (save/remove/move papers, create/rename/delete folder, edit metadata, follow/unfollow) plus two read-only tools missing from the docs. API keys have no scopes, so anyone holding the key can use all of them. Therefore the key stays on the server only; Muse gets ids from the allow-listed route, never the key or the MCP.
- Usage rules: Hot = candidates for new papers; For You = supplementary discovery (no age cut-off; Muse decides); papers already recommended on ANY day (even rated "not relevant") are dropped before choosing (`known_papers` + `drop_known`), and if one slips through the server merges it (adds the link only), keeps the original day/reason/rank/rating and answers with `already_recommended`; unmapped ids are skipped and counted (`stats`).

## Setup (owner, local only; the key never goes through chat, Git, journal, dashboard or logs)
1. alphaxiv.org -> Settings -> API Keys -> create a key (a personal account; terms say personal use, no scraping).
2. `cd /path/to/muse-control && read -rsp "alphaXiv API key: " K && echo && printf 'ALPHAXIV_API_KEY=%s\n' "$K" >> .env && unset K && chmod 600 .env && grep -c '^ALPHAXIV_API_KEY=' .env` (prints `1`)
3. `python3 ops/poc/alphaxiv_poc.py` -> statistics only.

## Rollout after the PoC (needs separate approval)
Backend (compose rebuild, tag the previous image) -> frontend source label (`alphaXiv`) through the normal gate -> Muse passes `alphaxiv=(url, token)` in its existing daily collection. Rollback: previous image tag; remove the key line from `.env`.

## Tests
`tests/test_alphaxiv.py` (10): default deny for all 23 other names incl. every documented write tool (nothing sent), argument validation, local-only base override, JSON and event-stream replies, id classification, error codes without the key, no-key sends nothing, cache + daily cap, route auth and 200-on-failure, source acceptance and dedup-only-adds-link, client helper degradation.
