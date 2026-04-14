# scrap-pub Agent Skill

This document describes how an AI agent (Claude Code, OpenClaw, etc.) can use scrap-pub to
download content from a kino-style, on-demand video website into a Plex-ready library.

scrap-pub is a generic download pipeline; the user points it at whatever site they want
via the `website` config key — the daemon itself ships with no default target site.

For direct SQL questions against the queue DB, see the companion skill
[`scrappub_sql_skill.md`](scrappub_sql_skill.md).

---

## Prerequisites

- **scrap-pub daemon running** — start it once and leave it running. After a
  one-time `uv pip install -e .` the console script is on `$PATH`:
  ```bash
  scrap-pub-server
  ```
  The daemon validates its config at boot and exits with code **2** if
  anything critical is missing (unset `website`, bad URL scheme, unwritable
  output/tmp/db paths, port collisions). A missing cookies file is only a
  warning — the daemon starts anyway and downloads will fail clearly until
  you upload one.
- **Valid session cookies** — read at startup from the Netscape `cookies.txt`
  file at `~/.config/scrap-pub/cookies.txt` (same format yt-dlp uses). If
  cookies expire, export a fresh file from the browser and run
  `scrap-pub cookies FILE`.
- **ffmpeg installed** — required for HLS download and MKV merge.

On NTFS mounts where `.venv` shebangs can't be executed, prefix every command
with `uv run` (e.g. `uv run scrap-pub status`).

---

## Daemon Control (CLI)

All CLI commands are short-lived WebSocket connections to the daemon. The daemon must be
running before any CLI command will work.

```bash
# Check daemon health
scrap-pub status

# Enqueue a movie or specific episode (URLs match the site configured via `website`)
scrap-pub enqueue "https://example.com/item/view/121639/s0e1"

# Enqueue one episode of a series
scrap-pub enqueue "https://example.com/item/view/122266/s1e1"

# Enqueue all episodes in a series
scrap-pub enqueue "https://example.com/item/view/122266"

# Inspect the queue
scrap-pub list                                    # most recent 50
scrap-pub list --status pending
scrap-pub list --status active
scrap-pub list --since week --kind movie -v       # time-windowed + per-stream progress
scrap-pub list --since today --status failed --json
scrap-pub show 42                                 # full detail for one task

# Watch logs in real time
scrap-pub logs --follow
scrap-pub logs --task 42 --follow

# Retry a failed task (clears completed_at; next claim sets a fresh started_at)
scrap-pub retry 42

# Skip a task (won't be downloaded)
scrap-pub skip 42

# Pause / resume all workers
scrap-pub pause
scrap-pub resume
```

### Time filters

`--since`, `--until`, and `--completed-since` accept human-friendly specs:
`today`, `yesterday`, `week`, `month`, `7d`, `24h`, `30m`, or an ISO
timestamp. Pending/active/failed tasks are always included in the web UI view
regardless of window (`include_unfinished=true`), so you never lose sight of
in-flight work.

### Task detail — `scrap-pub show`

```bash
scrap-pub show 42
```

Prints:

- id, status, kind, title/stem
- `enqueued_at`, `started_at`, `completed_at` (absolute + relative)
- attempts, output size, mkv path, last error (wrapped)
- when the task is still active: per-stream progress bars with `%`, ETA, speed, size

Add `--json` for a machine-readable reply.

### Ad-hoc SQL — `scrap-pub sql`

`scrap-pub sql` is a read-only-by-default escape hatch for structured
questions about the queue. It runs through the daemon (single SQLite owner)
and enforces a server-side whitelist: only `SELECT`/`WITH`/`PRAGMA`/`EXPLAIN`
are allowed unless you pass `--write`.

```bash
scrap-pub sql "SELECT id, status, enqueued_at FROM tasks ORDER BY id DESC LIMIT 10"
scrap-pub sql "SELECT COUNT(*) FROM tasks GROUP BY status" --json
scrap-pub sql "UPDATE tasks SET status='pending' WHERE id=42" --write
```

See [`scrappub_sql_skill.md`](scrappub_sql_skill.md) for the schema reference
and a recipe collection.

---

## URL Patterns

Target-site URLs (the sites scrap-pub is designed for) encode the content type like
this — replace `{website}` with whatever you set in config:

| URL pattern | Meaning |
|-------------|---------|
| `{website}/item/view/ITEM_ID` | Series root — enqueuing creates tasks for ALL episodes |
| `{website}/item/view/ITEM_ID/s1e3` | Specific TV episode (season 1, episode 3) |
| `{website}/item/view/ITEM_ID/s0e1` | Movie (season=0, episode=1) |

The item ID is the numeric ID in the URL path.

---

## Cookie Management

Kino-style target sites use browser-impersonation cookies that expire periodically.
When expired:
- All active tasks are automatically paused
- `scrap-pub status` shows `cookie_ok: false`
- The web UI shows a red "Session expired" banner and an error toast

**Cookies are stored as a Netscape `cookies.txt` file** (the same format yt-dlp,
curl, and wget use) at `~/.config/scrap-pub/cookies.txt` by default.

**To update cookies:**

1. Log into the target site (whatever you set as `website` in config) in Chrome
   or Firefox.
2. Install a cookies.txt exporter — we recommend **Get cookies.txt LOCALLY**
   (available for Chrome and Firefox).
3. With the target site open, click the extension → **Export** → save the file.
4. Load it into the daemon:
   ```bash
   scrap-pub cookies ~/Downloads/site_cookies.txt
   ```

The file must contain: `_identity`, `token`, `_csrf`, `PHPSESSID`, `cf_clearance`.
The daemon atomically replaces `cookies_path`, rebuilds the cookie jar, clears
the error flag, and resumes workers automatically.

---

## Config

```bash
# Show current config
scrap-pub config

# Common changes
scrap-pub config --set concurrency=4
scrap-pub config --set video_quality=highest
scrap-pub config --set output_dir="/path/to/plex/library"
scrap-pub config --set website="https://example.com"
```

### Config keys

| Key | Default | Description |
|-----|---------|-------------|
| `website` | `""` | Base URL of the target site. **Required** — daemon refuses to start without it. |
| `output_dir` | `<project>/output` | Where Plex-ready MKVs are written |
| `tmp_dir` | `<project>/tmp` | Working directory for ffmpeg (cleaned on task done) |
| `concurrency` | `2` | Parallel download workers |
| `video_quality` | `lowest` | `lowest` \| `highest` \| `720p` \| `1080p` |
| `audio_langs` | `["RUS","ENG","FRE"]` | Which audio tracks to include in MKV |
| `sub_langs` | `["rus","eng","fra"]` | Which subtitle languages to download as sidecars |
| `stall_timeout_sec` | `300` | Seconds without ffmpeg progress before killing download |

---

## Output Structure

Downloads land at `{output_dir}` with Plex-ready names:

```
{output_dir}/
  Hoppers(2026)/
    Hoppers(2026).mkv                    ← movie, h264 video + RUS/ENG audio tracks
    Hoppers(2026).rus.srt                ← subtitle sidecar (if available)
    Hoppers(2026).jpg                    ← poster
    Hoppers(2026).info.json              ← metadata

  Big Mistakes(2026)/
    Season 01/
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.mkv
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.rus.srt
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.eng.srt
      ...
    poster.jpg
    show.info.json
    thumbnails/
      s01e01.jpg
      ...
```

Naming follows Plex media naming conventions (see `docs/spec.md → Output structure`).

---

## Post-download Operations

### Add an extra audio track to an existing MKV

```bash
# 1. Find the task
scrap-pub list --status done --since month

# 2. Queue the extra audio download (provide the HLS audio .m3u8 URL)
scrap-pub add-audio 42 "https://cdn.../audio_fra.m3u8" --label "Français"
```

The audio track is downloaded and remuxed into the existing MKV (stream-copy, no re-encode).

### Add a subtitle sidecar

```bash
scrap-pub add-sub 42 "https://cdn.../sub.vtt" --lang fra
```

---

## WebSocket Protocol (for direct integration)

The daemon speaks JSON over WebSocket at `ws://localhost:8766`.

**Send a command:**
```python
import asyncio, json, websockets

async def cmd(payload):
    async with websockets.connect("ws://localhost:8766") as ws:
        await ws.send(json.dumps(payload))
        async for msg in ws:
            m = json.loads(msg)
            if m.get("type") == "reply" and m.get("cmd") == payload["cmd"]:
                return m

# Examples
asyncio.run(cmd({"cmd": "status"}))
asyncio.run(cmd({"cmd": "enqueue", "url": "https://example.com/item/view/121639/s0e1"}))
asyncio.run(cmd({"cmd": "list", "status": "pending", "limit": 20}))
asyncio.run(cmd({"cmd": "list", "verbose": True, "since": "2026-04-01T00:00:00+00:00"}))
asyncio.run(cmd({"cmd": "get",  "task_id": 42}))
asyncio.run(cmd({"cmd": "sql",  "query": "SELECT id, status FROM tasks ORDER BY id DESC LIMIT 5"}))
```

**Push events** — server broadcasts to all connected clients:
```json
{"type": "daemon_status",   "paused": false, "active_workers": 2, "queue_depth": 5}
{"type": "stream_progress", "task_id": 42, "stream_type": "video", "pct": 67.3, "speed": 3.4, "eta_sec": 120, "size_bytes": 412000000}
{"type": "task_update",     "task_id": 42, "status": "done", "mkv_path": "..."}
{"type": "cookie_error",    "msg": "Session expired — upload new cookies to resume."}
{"type": "log",             "task_id": 42, "level": "INFO", "msg": "...", "ts": "..."}
```

On connect, the server immediately pushes `daemon_status` and the last 50 log lines.

Key reply extensions for `list`/`get`:

- `task.output_size_bytes` — computed on the fly (mkv stat for `done`, sum of streams otherwise)
- `task.enqueued_at` / `started_at` / `completed_at` — ISO-8601 UTC
- `streams_by_task` (verbose list only) — `{task_id: [stream_dict, …]}` with live `pct`/`speed`/`eta_sec`/`size_bytes` overlaid from the in-memory progress cache

---

## Common Agent Workflows

### Enqueue a film and wait for it to finish

```bash
# Enqueue
scrap-pub enqueue "https://example.com/item/view/121639/s0e1"
# → "Enqueued 1 task(s): [3]"

# Poll with show (shows live progress %, ETA, output size)
scrap-pub show 3

# Or watch logs
scrap-pub logs --task 3 --follow
```

### Batch-enqueue a full TV series

```bash
scrap-pub enqueue "https://example.com/item/view/122266"
# → "Enqueued 8 task(s): [4, 5, 6, 7, 8, 9, 10, 11]"

scrap-pub list --status pending
scrap-pub list --since today --verbose
```

### Handle cookie expiry

```bash
scrap-pub status
# → cookie_ok: false

# Re-export cookies.txt from the browser, then:
scrap-pub cookies ~/Downloads/site_cookies.txt
# → daemon auto-resumes
```

### Recover a failed task

```bash
scrap-pub list --status failed --since week
# → #5  ✗  Some Movie(2025)  failed  2h ago  [stall after 305s, attempt 3/3]

scrap-pub retry 5
# → Task 5 reset to pending

scrap-pub show 5
# → completed_at is cleared; the task will get a fresh started_at on next claim
```

### Audit disk usage and long-running downloads

```bash
# Largest finished tasks this month
scrap-pub sql "
  SELECT id, plex_stem, mkv_path
  FROM tasks
  WHERE status='done' AND completed_at >= date('now','-30 days')
  ORDER BY id DESC LIMIT 20
"

# Tasks that started more than 2 hours ago but are still active (likely stuck)
scrap-pub sql "
  SELECT id, plex_stem, started_at
  FROM tasks
  WHERE status='active' AND started_at < datetime('now','-2 hours')
"
```

See [`scrappub_sql_skill.md`](scrappub_sql_skill.md) for more recipes and the
full schema reference.
