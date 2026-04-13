# scrap-pub Agent Skill

This document describes how an AI agent (Claude Code, OpenClaw, etc.) can use scrap-pub to
download content from a kino-style, on-demand video website into a Plex-ready library.

scrap-pub is a generic download pipeline; the user points it at whatever site they want
via the `website` config key — the daemon itself ships with no default target site.

---

## Prerequisites

- **scrap-pub daemon running** — start it once and leave it running:
  ```bash
  uv run python -m scrap_pub.daemon.server_main
  ```
- **Valid session cookies** — read at startup from the Netscape `cookies.txt`
  file at `~/.config/scrap-pub/cookies.txt` (same format yt-dlp uses). If
  cookies expire, export a fresh file from the browser and run
  `scrap-pub cookies FILE`.
- **ffmpeg installed** — required for HLS download and MKV merge.

---

## Daemon Control (CLI)

All CLI commands are short-lived WebSocket connections to the daemon. The daemon must be
running before any CLI command will work.

```bash
# Check daemon health
uv run python -m scrap_pub.daemon.cli_main status

# Enqueue a movie or specific episode (URLs match the site configured via `website`)
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/121639/s0e1"

# Enqueue one episode of a series
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/122266/s1e1"

# Enqueue all episodes in a series
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/122266"

# Check queue
uv run python -m scrap_pub.daemon.cli_main list
uv run python -m scrap_pub.daemon.cli_main list --status pending
uv run python -m scrap_pub.daemon.cli_main list --status active

# Watch logs in real time
uv run python -m scrap_pub.daemon.cli_main logs --follow
uv run python -m scrap_pub.daemon.cli_main logs --task 42 --follow

# Retry a failed task
uv run python -m scrap_pub.daemon.cli_main retry 42

# Skip a task (won't be downloaded)
uv run python -m scrap_pub.daemon.cli_main skip 42

# Pause / resume all workers
uv run python -m scrap_pub.daemon.cli_main pause
uv run python -m scrap_pub.daemon.cli_main resume
```

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
- The web UI shows a red "Session expired" banner

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
   uv run python -m scrap_pub.daemon.cli_main cookies ~/Downloads/site_cookies.txt
   ```

The file must contain: `_identity`, `token`, `_csrf`, `PHPSESSID`, `cf_clearance`.
The daemon atomically replaces `cookies_path`, rebuilds the cookie jar, clears
the error flag, and resumes workers automatically.

---

## Config

```bash
# Show current config
uv run python -m scrap_pub.daemon.cli_main config

# Common changes
uv run python -m scrap_pub.daemon.cli_main config --set concurrency=4
uv run python -m scrap_pub.daemon.cli_main config --set video_quality=highest
uv run python -m scrap_pub.daemon.cli_main config --set output_dir="/path/to/plex/library"
```

### Config keys

| Key | Default | Description |
|-----|---------|-------------|
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
# 1. Get the task ID
uv run python -m scrap_pub.daemon.cli_main list --status done

# 2. Queue the extra audio download (provide the HLS audio .m3u8 URL)
uv run python -m scrap_pub.daemon.cli_main add-audio 42 "https://cdn.../audio_fra.m3u8" --label "Français"
```

The audio track is downloaded and remuxed into the existing MKV (stream-copy, no re-encode).

### Add a subtitle sidecar

```bash
uv run python -m scrap_pub.daemon.cli_main add-sub 42 "https://cdn.../sub.vtt" --lang fra
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
```

**Push events** — server broadcasts to all connected clients:
```json
{"type": "daemon_status",   "paused": false, "active_workers": 2, "queue_depth": 5}
{"type": "stream_progress", "task_id": 42, "stream_type": "video", "pct": 67.3, "speed": 3.4}
{"type": "task_update",     "task_id": 42, "status": "done", "mkv_path": "..."}
{"type": "cookie_error",    "msg": "Session expired — upload new cookies to resume."}
{"type": "log",             "task_id": 42, "level": "INFO", "msg": "...", "ts": "..."}
```

On connect, the server immediately pushes `daemon_status` and the last 50 log lines.

---

## Common Agent Workflows

### Enqueue a film and wait for it to finish

```bash
# Enqueue
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/121639/s0e1"
# → "Enqueued 1 task(s): [3]"

# Poll until done (or watch logs)
uv run python -m scrap_pub.daemon.cli_main logs --task 3 --follow
```

### Batch-enqueue a full TV series

```bash
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/122266"
# → "Enqueued 8 task(s): [4, 5, 6, 7, 8, 9, 10, 11]"

uv run python -m scrap_pub.daemon.cli_main list --status pending
```

### Handle cookie expiry

```bash
uv run python -m scrap_pub.daemon.cli_main status
# → cookie_ok: false

# Re-export cookies.txt from the browser, then:
uv run python -m scrap_pub.daemon.cli_main cookies ~/Downloads/site_cookies.txt
# → daemon auto-resumes
```

### Recover a failed task

```bash
uv run python -m scrap_pub.daemon.cli_main list --status failed
# → Task #5: status=failed [stall after 305s, attempt 3/3]

uv run python -m scrap_pub.daemon.cli_main retry 5
# → Task 5 reset to pending
```

