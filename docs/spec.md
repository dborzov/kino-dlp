# Specification

> One-line summary: user-facing behavior — config, CLI, WebSocket protocol, download flow, error handling, output layout.
>
> Last updated: 2026-04-14

For the system map and SQLite schema see [architecture.md](architecture.md). For why these
choices were made see [internals.md](internals.md).

---

## Config file

Default path: `~/.config/scrap-pub/config.json`. Created with defaults on first run.
Override with `scrap-pub-server --config /path/to/config.json`.

```json
{
  "website":            "",
  "output_dir":         "~/output",
  "tmp_dir":            "~/tmp",
  "db_path":            "~/.local/share/scrap-pub/queue.db",
  "cookies_path":       "~/.config/scrap-pub/cookies.txt",
  "concurrency":        2,
  "stall_timeout_sec":  300,
  "http_port":          8765,
  "ws_port":            8766,
  "video_quality":      "lowest",
  "audio_langs":        ["RUS", "ENG", "FRE"],
  "sub_langs":          ["rus", "eng", "fra"],
  "min_free_space_gb":  10
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `website` | `""` (unset) | Base URL of the target site (e.g. `https://example.com`). You must set this — the repo ships with no default target; scraping fails until it's configured. |
| `output_dir` | `~/output` | **Default** final-output root. A task enqueued without `--output-dir` lands here. Per-task overrides take precedence and do not retroactively move tasks that are already queued or done. |
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
| `min_free_space_gb` | `10` | Advisory free-space floor for enqueue and task-start checks. Applies to both the default `output_dir` and any per-task `--output-dir`. A season of 1080p content can easily exceed 40 GB, so this is a "don't even try when the disk is nearly full" guard, *not* a per-task estimate. The check is **not** atomic across concurrent workers — treat it as advisory. Set to `0` to disable entirely. |

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

Most subcommands connect to a running daemon via WebSocket, send one command, print the reply, and exit. If the daemon isn't running you get:

```
error: cannot connect to ws://localhost:8766 — is scrap-pub-server running?
```

Two subcommands are purely local and work without the daemon: `scrap-pub paths` (echoes resolved config paths) and `scrap-pub lookup` (fetches + parses one target-site item page).

```
scrap-pub status
    Show daemon status: paused, active workers, queue counts, cookie_ok.

scrap-pub enqueue URL [URL ...] [-o PATH | --output-dir PATH]
    Scrape URL(s) on the configured target site and enqueue all episodes/movies found.
    URL forms (assuming `website` is set to e.g. https://example.com):
      https://example.com/item/view/12345          → all episodes of a series
      https://example.com/item/view/12345/s1e3     → specific episode
      https://example.com/item/view/12345/s0e1     → movie (season=0, episode=1)

    --output-dir PATH
        Write this task's output (MKV, metadata, poster/thumbnails, subtitle
        sidecars) under PATH instead of the daemon-wide `output_dir` config.
        Intended for dropping content directly into a media-server library:
            scrap-pub enqueue URL --output-dir "/mnt/plex/TV Shows"
        The inside-the-root layout (ShowName(Year)/Season XX/…) does not
        change. The flag applies to every URL in this invocation.
        PATH is resolved client-side (absolute, `~` expanded), then the
        daemon re-validates parent/permissions/free-space on its own
        filesystem before any task row or scaffold file is written. If the
        same item has already been enqueued under a different `--output-dir`,
        the command fails with a "delete existing task first" error — it
        does **not** silently ignore the flag. Missing directories are
        auto-created *only* when the parent exists and is writable.

scrap-pub lookup URL [-e|--episodes] [--json]
    Fetch ONE item URL on the configured target site and print its core
    metadata — Russian title, original-language title, year, and whether
    the item is a movie or a TV show — without enqueueing anything.
    Intended as a pre-enqueue reconnaissance step, especially for AI
    agents that need to decide which Plex library (`Movies` vs
    `TV Shows`) to pass to `enqueue --output-dir`. The human-readable
    output includes an explicit "Hint for agents:" line with a ready-to-
    run enqueue command tailored to the detected kind.

    URL may be either the series root (`/item/view/ID`) or a specific
    episode (`/item/view/ID/sXeY`). When the URL encodes a season and
    episode, the output includes a "Current S10E04"-style line so you
    can confirm the reference matches what you expected.

    -e, --episodes
        For TV shows, also walk every season and list every episode with
        its per-episode URL. This fires one HTTP request per season with
        a 0.8–2.0 s jitter between requests, so a 13-season show takes
        ~15–25 s. A progress bar is drawn on stderr while seasons are
        being fetched; the structured output still goes to stdout so
        `--json | jq` keeps working.

    --json
        Emit the parsed metadata dict as JSON (title_ru, title_orig,
        kind, year, seasons, seasons_data when --episodes was passed,
        cast, genres, rating/id fields).

    `lookup` is LOCAL — it does not talk to the daemon. The only
    prerequisite is a valid cookies file at
    `~/.config/scrap-pub/cookies.txt`; on cookie expiry it exits with a
    clean "cookies expired or missing" error. It will refuse to run if
    the `website` config key is unset.

scrap-pub list [--status STATUS] [--kind K] [--since SPEC] [--until SPEC]
              [--completed-since SPEC] [--limit N] [--offset N] [-v] [--json]
    List tasks with optional filters.
      STATUS : pending | active | done | failed | skipped
      KIND   : movie | episode
      SPEC   : today | yesterday | week | month | Nd | Nh | Nm | ISO timestamp
      -v / --verbose : include per-stream rows with % and ETA under each task
    Each task row carries `output_size_bytes` computed on demand (mkv stat for
    `done`, sum of stream sizes otherwise).

scrap-pub show TASK_ID [--json]
    Print a full detail block for one task: status, timestamps (enqueued /
    started / completed, with relative time), attempts, last error, output
    size, and — when the task is still active — per-stream progress bars
    overlaid with live % / ETA / speed from the in-memory progress cache.

scrap-pub sql "QUERY" [--write] [--json|--csv] [--limit N]
scrap-pub sql -f FILE [...]
scrap-pub sql -f - [...]      # read from stdin
    Run a SQL query against the daemon's SQLite DB through the WebSocket.
    Read-only by default — only SELECT/WITH/PRAGMA/EXPLAIN are accepted
    unless --write is passed. Results are capped at --limit rows (default
    1000); the reply's `truncated` flag indicates when the cap was hit.
    Exit codes: 0 on success, 2 on safety-gate rejection, 1 on SQLite error.
    See skills/scrappub_sql_skill.md for the schema and recipes.

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

scrap-pub paths [KEY]
    Print paths resolved from the local config file. Runs WITHOUT the
    daemon — reads ~/.config/scrap-pub/config.json directly and echoes the
    expanded values. With no KEY, prints every entry as `key  value`.
    With a KEY, prints just that value so it can be used in shell
    substitution: `cd $(scrap-pub paths output)`, `ls $(scrap-pub paths tmp)`,
    `sqlite3 $(scrap-pub paths db) ...`.
      KEY : output | tmp | db | cookies | config | website
    This is the recommended way for AI agents and scripts to discover where
    downloaded files, working files, the queue DB, and the cookies file live
    on this specific installation — do not hard-code `./output` or `./tmp`.
```

---

## WebSocket protocol

Single port (default `8766`). All messages are JSON.

### Commands (client → server)

| `cmd` | Fields | Description |
|-------|--------|-------------|
| `status` | — | Daemon status + queue counts |
| `list` | `status?`, `kind?`, `since?`, `until?`, `completed_since?`, `limit?`, `offset?`, `verbose?`, `include_unfinished?` | Task list. Each task carries `output_size_bytes`; `verbose=true` also attaches `streams_by_task` with live progress overlaid. |
| `get` | `task_id` | Single task with `streams`, live progress overlay, and `output_size_bytes`. Replies `ok: false` if not found. |
| `sql` | `query`, `params?`, `write?`, `max_rows?` | Run a SQL query. Read-only by default (first-token gate: `SELECT`/`WITH`/`PRAGMA`/`EXPLAIN`); set `write: true` to allow DML/DDL. Reply includes `columns`, `rows`, `rowcount`, `truncated`. Row cap defaults to 1000. |
| `logs` | `task_id?`, `limit?` | Log entries |
| `enqueue` | `url`, `output_dir?` | Scrape + create tasks. Optional `output_dir` overrides the default `output_dir` config for every task created by this call. The server validates it (parent exists, writable, free space ≥ `min_free_space_gb`) before any scraping happens; on failure the reply is `{ok: false, error: "..."}` and no task rows or scaffold files are written. A re-enqueue of an already-queued item with a different `output_dir` also fails with a clear error. |
| `retry` | `task_id` | Reset task to pending (clears `completed_at`) |
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
{"type": "stream_progress", "task_id": 42, "stream_id": 7, "stream_type": "video", "pct": 34.2, "speed": 3.4, "eta_sec": 180, "size_bytes": 45000000}
{"type": "task_update",     "task_id": 42, "status": "done", "mkv_path": "..."}
{"type": "stream_update",   "stream_id": 7, "status": "done", "size_bytes": 92000000}
{"type": "cookie_error",    "msg": "Session expired — upload new cookies to resume."}
{"type": "log",             "task_id": 42, "level": "INFO", "msg": "...", "ts": "..."}
```

`stream_progress.eta_sec` is `(duration_sec - elapsed_sec) / speed`, unsmoothed. It is `null` when speed is unknown or the stream is within a second of the end. The same value is attached to stream dicts surfaced via `list verbose` / `get`.

On connect the server immediately sends `daemon_status` + the last 50 global log lines.

---

## Download flow

For each claimed task the worker does:

0. **Resolve output root** — `task.output_dir` (per-task override) if set, else `config.output_dir`. Re-validates existence, writability, and free space. If the directory vanished between enqueue and claim (unmount, rename, permissions change) the task is marked `failed` with a readable `last_error` like `"output directory not writable: /mnt/plex"` and the worker moves on. References below to `{output_root}` mean this resolved path.
1. **Idempotency check** — if `{output_root}/{plex_stem}.mkv` already exists and is non-empty, mark task `done` and skip.
2. **Ensure metadata** — if `items.meta_json` is missing, call `scraper.scrape(url)` and upsert the row.
3. **Resolve manifest** — fetch a fresh signed HLS master `.m3u8`. URLs have a ~24h TTL, so this always happens on resume.
4. **Parse manifest** — extract video quality tiers, audio tracks, subtitle tracks. After `duration_sec` is known, a rough disk-space re-check fires using `max(min_free_space_gb, duration_sec/3600 * 3 + 2)` GB; failure marks the task failed with a clear message.
5. **Select streams** — per `video_quality`, `audio_langs`, `sub_langs`.
6. **Upsert stream rows** — already-`done` rows are skipped (this is the resume mechanism).
7. **Scaffold Plex dirs** — create dirs under `{output_root}`, download poster + thumbnails, write `.info.json` (idempotent).
8. **Subtitles first** — small, fast, cheap. Sidecar `.srt` written next to the MKV location in `{output_root}`.
9. **Video stream** — `{tmp_dir}/{plex_stem}_video.mp4`.
10. **Audio streams** — `{tmp_dir}/{plex_stem}_audio_{lang}.m4a`, one per selected language.
11. **Merge** — single ffmpeg stream-copy invocation produces `{output_root}/{plex_stem}.mkv`. No re-encode.
12. **Cleanup** — remove `{tmp_dir}/*` for this task; task `status=done`; emit `task_update`.

Note that `{tmp_dir}` always comes from `config.tmp_dir` — per-task overrides only affect the final-output root, not the scratch directory.

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

### Filesystem errors

Filesystem problems are caught at three checkpoints and surface as concise,
actionable error messages:

1. **Enqueue time** (`scrap-pub enqueue --output-dir`, WS `enqueue` payload):
   `validate_task_output_dir` runs before any scraping or DB write. It expands
   `~`, resolves to absolute, auto-creates missing directories when the
   *parent* exists and is writable (so typos like `/mtn/plex` fail rather than
   materialising a stray tree), and runs a write-probe. Failure → CLI/WS
   error, no task row, no poster, no metadata JSON. Error messages include
   the offending path and the reason, e.g.:
   - `"output directory parent does not exist: /mtn/plex/TV Shows — did you mean /mnt/plex/TV Shows?"`
   - `"not a directory: /home/user/plex-not-a-dir"`
   - `"not writable: /mnt/plex/locked (permission denied)"`
   - `"insufficient free space at /mnt/plex: 2.1 GB free, need 10 GB"`
2. **Task start** (top of `download_task`): the effective output root is
   re-resolved and re-validated. A disk can be unmounted or a directory
   renamed between enqueue and worker claim, and this catches it instead of
   crashing mid-download. The task transitions to `failed` with the
   validation message in `last_error`.
3. **Mid-download**: `OSError`s from `mkdir`, writing the merged MKV, or
   writing subtitle sidecars are wrapped in `TaskFSError`, which carries
   the operation *and* the path. `last_error` reads like
   `"writing merged MKV to /mnt/plex/foo.mkv: No space left on device"`
   instead of the raw `[Errno 28]`.

The disk-free check is **advisory**: it is not atomic across concurrent
workers, and it uses a conservative `~3 GB/hour` floor added on top of
`min_free_space_gb`, which is right for 1080p content most of the time but
not a precise reservation. Two workers can both pass the check and then run
out of space during the merge — they fall through to the `TaskFSError`
handler above.

### Task failed

`scrap-pub list --status failed` shows failed tasks. `scrap-pub retry ID` resets a task to `pending` (streams that are still `done` are reused — not re-downloaded).

---

## Output structure

Mirrors Plex media naming conventions. Titles come from the target site's `og:title` in the **original language** of the content: English title for English shows, French for French films, Russian for Russian shows.

The output **root** is either `config.output_dir` (default) or the per-task
`--output-dir` passed at enqueue. The inside-the-root layout is identical
either way, so pointing a task directly at a Plex library drops a scanner-
ready tree in place:

```
{output_root}/
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
- **Enqueue** — the `UNIQUE(item_id, season, episode)` index dedupes; re-enqueuing an item with the *same* (or no) `--output-dir` is a no-op. Re-enqueuing with a *different* `--output-dir` fails loudly instead of silently ignoring the new path — delete the existing task first.
