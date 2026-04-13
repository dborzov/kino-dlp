# Specification

> One-line summary: user-facing behavior — config, CLI, WebSocket protocol, download flow, error handling, output layout.
>
> Last updated: 2026-04-13

For the system map and SQLite schema see [architecture.md](architecture.md). For why these
choices were made see [internals.md](internals.md).

---

## Config file

Default path: `~/.config/scrap-pub/config.json`. Created with defaults on first run.
Override with `scrap-pub-server --config /path/to/config.json`.

```json
{
  "website":           "",
  "output_dir":        "~/output",
  "tmp_dir":           "~/tmp",
  "db_path":           "~/.local/share/scrap-pub/queue.db",
  "cookies_path":      "~/.config/scrap-pub/cookies.txt",
  "concurrency":       2,
  "stall_timeout_sec": 300,
  "http_port":         8765,
  "ws_port":           8766,
  "video_quality":     "lowest",
  "audio_langs":       ["RUS", "ENG", "FRE"],
  "sub_langs":         ["rus", "eng", "fra"]
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `website` | `""` (unset) | Base URL of the target site (e.g. `https://example.com`). You must set this — the repo ships with no default target; scraping fails until it's configured. |
| `output_dir` | `~/output` | Final MKVs + sidecars + posters |
| `tmp_dir` | `~/tmp` | ffmpeg working dir; auto-cleaned on task done |
| `db_path` | `~/.local/share/scrap-pub/queue.db` | SQLite queue |
| `cookies_path` | `~/.config/scrap-pub/cookies.txt` | Netscape cookies.txt with target-site session cookies |
| `concurrency` | `2` | Parallel download workers |
| `stall_timeout_sec` | `300` | Seconds without ffmpeg progress before kill + retry |
| `http_port` | `8765` | Web UI HTTP port |
| `ws_port` | `8766` | WebSocket control port |
| `video_quality` | `lowest` | `lowest` \| `highest` \| `720p` \| `1080p` |
| `audio_langs` | `["RUS","ENG","FRE"]` | Audio tracks to embed (ISO-639-2 uppercase) |
| `sub_langs` | `["rus","eng","fra"]` | Subtitle sidecar languages (ISO-639-2 lowercase) |

All `~` paths expand at load time. Update at runtime:

```bash
scrap-pub config --set concurrency=4
scrap-pub config --set video_quality=highest
scrap-pub config --set audio_langs='["RUS","ENG"]'
```

Values are parsed as JSON: `4` for integers, `true`/`false` for booleans, `'[...]'` for lists. Bare strings also work.

---

## Session cookies

Kino-style, on-demand video websites typically have no public API and sit behind
Cloudflare. Every request from the scraper carries the same session cookies you'd
have in a logged-in browser on the target site. scrap-pub stores them in a
**Netscape `cookies.txt`** file — the same format yt-dlp, curl, and wget use — at
`~/.config/scrap-pub/cookies.txt` by default (configurable via `cookies_path`).

### Required cookies

The file must contain all five of these (the daemon rejects uploads missing
any of them):

| Cookie | What it is |
|--------|------------|
| `_identity` | long-lived identity token on the target site — tied to your user id |
| `token` | CSRF / session token paired with `_identity` |
| `_csrf` | Yii2 CSRF protection token |
| `PHPSESSID` | PHP session id |
| `cf_clearance` | Cloudflare "passed the JS challenge" clearance cookie |

Optional cookies like `_ga`, `__cflb`, etc. are loaded if present but don't
affect authentication.

### How to produce cookies.txt

Use a browser extension that exports cookies in Netscape format — the same
approach yt-dlp documents for logged-in sites:

1. Log into the target site (the one you set as `website` in config) in Chrome
   or Firefox.
2. Install **Get cookies.txt LOCALLY**
   ([Chrome](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) ·
   [Firefox](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt-one-click/)).
3. With the target site open in the active tab, click the extension → **Export**.
4. Save the file and load it into the daemon one of two ways:

   ```bash
   # Option A — drop it at the default path (daemon picks it up on startup)
   mv ~/Downloads/site_cookies.txt ~/.config/scrap-pub/cookies.txt

   # Option B — hot-reload into a running daemon
   scrap-pub cookies ~/Downloads/site_cookies.txt
   ```

Option B validates the file, atomically replaces `cookies_path`, clears the
`cookie_error` flag, and resumes paused workers — no restart needed.

### When to refresh

Cloudflare's `cf_clearance` cookie lasts a few days; the session cookies
(`_identity`, `token`, `PHPSESSID`) last weeks but can be invalidated by a
password change or manual logout. When any of them expires the daemon
detects 403s or login redirects, marks the session as stale, pauses all
workers, and broadcasts a `cookie_error` event. Re-export cookies.txt and
run `scrap-pub cookies FILE` to recover.

### File format reference

Plain tab-separated Netscape format — 7 fields per line, `#` for comments:

```
# Netscape HTTP Cookie File
.example.com	TRUE	/	TRUE	2147483647	_identity	...
.example.com	TRUE	/	TRUE	2147483647	token	...
.example.com	TRUE	/	TRUE	2147483647	_csrf	...
.example.com	TRUE	/	TRUE	2147483647	PHPSESSID	...
.example.com	TRUE	/	TRUE	2147483647	cf_clearance	...
```

Fields: `domain`, `include_subdomains`, `path`, `secure`, `expiration`, `name`, `value`.
Parsed via Python's `http.cookiejar.MozillaCookieJar`.

---

## CLI reference

All subcommands connect to a running daemon via WebSocket, send one command, print the reply, and exit. If the daemon isn't running you get:

```
error: cannot connect to ws://localhost:8766 — is scrap-pub-server running?
```

```
scrap-pub status
    Show daemon status: paused, active workers, queue counts, cookie_ok.

scrap-pub enqueue URL [URL ...]
    Scrape URL(s) on the configured target site and enqueue all episodes/movies found.
    URL forms (assuming `website` is set to e.g. https://example.com):
      https://example.com/item/view/12345          → all episodes of a series
      https://example.com/item/view/12345/s1e3     → specific episode
      https://example.com/item/view/12345/s0e1     → movie (season=0, episode=1)

scrap-pub list [--status STATUS] [--limit N]
    List tasks. STATUS: pending | active | done | failed | skipped.

scrap-pub logs [--task ID] [--limit N] [--follow]
    Show log entries. --follow streams new lines until Ctrl-C.

scrap-pub retry TASK_ID
    Reset a failed/done task back to pending (re-downloads from scratch).

scrap-pub skip TASK_ID
    Mark a task as skipped (won't be picked up by workers).

scrap-pub pause / resume
    Pause or resume all download workers.

scrap-pub cookies FILE
    Load session cookies from a Netscape cookies.txt file (same format as
    yt-dlp/curl). Auto-resumes if paused. The file is copied to
    `cookies_path` (default ~/.config/scrap-pub/cookies.txt).
    Required cookies: _identity, token, _csrf, PHPSESSID, cf_clearance

scrap-pub add-audio TASK_ID URL [--label LABEL]
    Download an extra audio track and remux it into the existing MKV.

scrap-pub add-sub TASK_ID URL [--lang LANG]
    Download a subtitle sidecar. LANG is ISO-639-2 (rus, eng, fra).

scrap-pub config [--set KEY=VALUE ...]
    Show current config or update one or more keys.
```

---

## WebSocket protocol

Single port (default `8766`). All messages are JSON.

### Commands (client → server)

| `cmd` | Fields | Description |
|-------|--------|-------------|
| `status` | — | Daemon status + queue counts |
| `list` | `status?`, `limit?`, `offset?` | Task list |
| `logs` | `task_id?`, `limit?` | Log entries |
| `enqueue` | `url` | Scrape + create tasks |
| `retry` | `task_id` | Reset task to pending |
| `skip` | `task_id` | Mark task skipped |
| `pause` | — | Pause workers |
| `resume` | — | Resume workers |
| `cookies` | `cookies_txt: str` | Upload raw Netscape cookies.txt contents |
| `config_get` | — | Return config dict |
| `config_set` | `key`, `value` | Update one config key |
| `add_audio` | `task_id`, `url`, `label?` | Queue extra audio track |
| `add_sub` | `task_id`, `url`, `lang?` | Queue subtitle download |

### Replies (server → client)

```json
{"type": "reply", "cmd": "status",  "ok": true,  "paused": false, "active_workers": 2, ...}
{"type": "reply", "cmd": "enqueue", "ok": false, "error": "missing 'url'"}
```

### Push events (server → all clients)

```json
{"type": "daemon_status",   "paused": false, "active_workers": 2, "queue_depth": 5, "cookie_ok": true, "counts": {...}}
{"type": "stream_progress", "task_id": 42, "stream_id": 7, "stream_type": "video", "pct": 34.2, "speed": 3.4, "size_bytes": 45000000}
{"type": "task_update",     "task_id": 42, "status": "done", "mkv_path": "..."}
{"type": "stream_update",   "stream_id": 7, "status": "done", "size_bytes": 92000000}
{"type": "cookie_error",    "msg": "Session expired — upload new cookies to resume."}
{"type": "log",             "task_id": 42, "level": "INFO", "msg": "...", "ts": "..."}
```

On connect the server immediately sends `daemon_status` + the last 50 global log lines.

---

## Download flow

For each claimed task the worker does:

1. **Idempotency check** — if `{output_dir}/{plex_stem}.mkv` already exists and is non-empty, mark task `done` and skip.
2. **Ensure metadata** — if `items.meta_json` is missing, call `scraper.scrape(url)` and upsert the row.
3. **Resolve manifest** — fetch a fresh signed HLS master `.m3u8`. URLs have a ~24h TTL, so this always happens on resume.
4. **Parse manifest** — extract video quality tiers, audio tracks, subtitle tracks.
5. **Select streams** — per `video_quality`, `audio_langs`, `sub_langs`.
6. **Upsert stream rows** — already-`done` rows are skipped (this is the resume mechanism).
7. **Scaffold Plex dirs** — create dirs, download poster + thumbnails, write `.info.json` (idempotent).
8. **Subtitles first** — small, fast, cheap. Sidecar `.srt` written next to the MKV location.
9. **Video stream** — `{tmp_dir}/{plex_stem}_video.mp4`.
10. **Audio streams** — `{tmp_dir}/{plex_stem}_audio_{lang}.m4a`, one per selected language.
11. **Merge** — single ffmpeg stream-copy invocation produces `{output_dir}/{plex_stem}.mkv`. No re-encode.
12. **Cleanup** — remove `{tmp_dir}/*` for this task; task `status=done`; emit `task_update`.

---

## Error handling

### Stall

ffmpeg stall watchdog kills the process after `stall_timeout_sec` with no progress line.

- Task status reverts to `pending`, `attempts += 1`
- Retry backoff: `30 * 2^attempts` seconds (30s, 60s, 120s)
- After 3 attempts: task `status=failed`. Recover with `scrap-pub retry TASK_ID`.

### Cookie expiry

Scraper detects 403 or login redirect → raises `CookieExpiredError`.

1. All `active` tasks revert to `pending`
2. `pause_event.clear()` — workers stop pulling new work
3. `kv.cookie_error = true`
4. Broadcast `{"type": "cookie_error"}` → UI shows red banner
5. User exports a fresh Netscape `cookies.txt` and uploads it via the Web UI
   Settings tab or `scrap-pub cookies FILE`
6. Daemon validates required keys, atomically writes the new `cookies.txt` to
   `cookies_path`, rebuilds the curl-cffi cookie jar, clears the error flag,
   `pause_event.set()`
7. Workers resume automatically

See [§ Session cookies](#session-cookies) for the full cookie list and export steps.

### Task failed

`scrap-pub list --status failed` shows failed tasks. `scrap-pub retry ID` resets a task to `pending` (streams that are still `done` are reused — not re-downloaded).

---

## Output structure

Mirrors Plex media naming conventions. Titles come from the target site's `og:title` in the **original language** of the content: English title for English shows, French for French films, Russian for Russian shows.

```
{output_dir}/
  Hoppers(2026)/
    Hoppers(2026).mkv          ← video + embedded RUS/ENG/FRE audio
    Hoppers(2026).rus.srt      ← sidecar subtitle (if available)
    Hoppers(2026).jpg          ← poster
    Hoppers(2026).info.json    ← scraped metadata

  Big Mistakes(2026)/
    poster.jpg
    show.info.json
    Season 01/
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.mkv
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.rus.srt
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.eng.srt
    thumbnails/
      s01e01.jpg
```

Full Plex naming reference: [plex_naming.md](plex_naming.md).

---

## Idempotency and resume

- **Task-level** — existing non-empty `mkv_path` short-circuits the worker
- **Stream-level** — only `pending` streams are re-downloaded; `done` streams are reused
- **Scaffold** — `scaffold()` skips already-downloaded posters/thumbnails; always re-writes `.info.json`
- **Enqueue** — the `UNIQUE(item_id, season, episode)` index dedupes; re-enqueuing is a no-op
