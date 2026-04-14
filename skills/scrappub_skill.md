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

## Finding file paths (agents: read this first)

You likely don't know where this user's installation writes its files — the
paths are all driven by `~/.config/scrap-pub/config.json` and differ per
machine. **Do not guess** `./output`, `./tmp`, or `~/Downloads/...` — ask the
CLI.

`scrap-pub paths` runs locally against the config file and does **not**
require the daemon to be running. Use it any time you need to open, `cd`,
`ls`, grep, or attach to a downloaded file.

```bash
# Show every path at once (output, tmp, db, cookies, config, website)
scrap-pub paths

# Echo a single value — ideal for shell substitution
scrap-pub paths output      # /home/user/media/plex
scrap-pub paths tmp         # /var/tmp/scrap-pub
scrap-pub paths cookies     # ~/.config/scrap-pub/cookies.txt
scrap-pub paths db          # ~/.local/share/scrap-pub/queue.db
scrap-pub paths config      # path to the config.json currently in effect
scrap-pub paths website     # https://example.com (base URL of the target site)
```

Typical agent recipes:

```bash
# Go look at a finished MKV
cd "$(scrap-pub paths output)" && ls -lh

# Inspect ffmpeg's in-progress working files for task 42
ls -lh "$(scrap-pub paths tmp)"

# Open the SQLite queue DB directly (read-only)
sqlite3 "$(scrap-pub paths db)" "SELECT id, status, plex_stem FROM tasks ORDER BY id DESC LIMIT 10;"

# Show the full config (including any keys not in the table below)
cat "$(scrap-pub paths config)"
```

For downloaded-file paths **per task**, prefer `scrap-pub show TASK_ID` — its
`mkv` line is the absolute path to the finished file. That value is already
an absolute path, so you don't need to join it with `paths output` yourself.

---

## Daemon Control (CLI)

Most CLI commands are short-lived WebSocket connections to the daemon; the
daemon must be running for those. Two commands are purely local and work
without the daemon: `scrap-pub paths` (prints resolved config paths) and
`scrap-pub lookup` (fetches + parses one item page — see the [Lookup
section](#lookup-pre-enqueue-reconnaissance) below).

```bash
# Check daemon health
scrap-pub status

# Inspect an item page BEFORE enqueueing — prints kind/title/year and
# tells you which Plex library it should go into. No daemon needed.
scrap-pub lookup "https://example.com/item/view/121639/s0e1"

# Enqueue a movie or specific episode (URLs match the site configured via `website`)
scrap-pub enqueue "https://example.com/item/view/121639/s0e1"

# Enqueue one episode of a series
scrap-pub enqueue "https://example.com/item/view/122266/s1e1"

# Enqueue all episodes in a series
scrap-pub enqueue "https://example.com/item/view/122266"

# Enqueue directly into a Plex library directory (see "Downloading into a
# Plex library" below for the full recipe and error modes)
scrap-pub enqueue "https://example.com/item/view/122266" \
  --output-dir "/mnt/plex/TV Shows"

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
timestamp. `today` and `yesterday` resolve to the **user's local calendar
day** (midnight local, converted to UTC for the SQLite comparison), not UTC
midnight. `week` / `month` / `Nd` / `Nh` / `Nm` are rolling offsets from
the current instant.

The same parser runs both client-side (`scrap-pub list --since ...`) and
server-side (the web UI's Today / Week / Month chips), so both surfaces
agree on what "today" means. Pending/active/failed tasks are always
included in the web UI view regardless of window (`include_unfinished=true`),
so you never lose sight of in-flight work.

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

**Important caveat:** *you cannot tell from the URL alone whether `ITEM_ID`
is a movie or a TV show.* The site uses `/s0e1` for movies and `/sXeY` for
episodes, but a bare `/item/view/ITEM_ID` could be either — and a URL that
*looks* like a movie (`/s0e1`) can occasionally be something surprising.
**Always run `scrap-pub lookup URL` first** (next section) before choosing
a Plex library path.

### Per-episode vs whole-series scope

When you enqueue `/item/view/ITEM_ID/s3e5`, scrap-pub narrows *everything*
to that one episode:

- `scrape()` only fetches season 3's page — no rate-limited walk across
  every other season of a 13-season show.
- `scaffold()` only creates `s03e05.info.json` + `s03e05-thumb.jpg` — not
  metadata for every episode of every season.
- The show-level `show.info.json` + `poster.jpg` are written **only if
  they don't already exist** on disk. Enqueuing a second episode of the
  same show skips show-level work entirely.

When you enqueue `/item/view/ITEM_ID` (no season suffix), the full walk
happens and every episode of every season gets a task row + metadata file.
Prefer episode URLs when you only want one episode — the difference is
large on long-running shows.

---

## Lookup (pre-enqueue reconnaissance)

`scrap-pub lookup URL` fetches the page on the target site and prints the
core metadata about the item — **Russian title, original-language title,
year, and whether it is a movie or a TV show** — without enqueueing
anything, without starting a download, and **without requiring the daemon
to be running**. The only prerequisite is a valid `cookies.txt` at
`~/.config/scrap-pub/cookies.txt` (same as every other scraping command).

**Agents: run this first.** When you are given an unknown URL and asked to
download it, you likely do not know whether the target is a movie or a TV
show, and therefore cannot decide which Plex library directory (`Movies`
vs `TV Shows`) to pass to `--output-dir` on `enqueue`. `lookup` exists to
answer that one question before you commit. The output includes an
explicit "Hint for agents:" line with the right enqueue command pre-formed
for the detected kind — prefer reading that hint over constructing the
command yourself.

```bash
# Movie — prints Type: movie, Year, Duration, and a Movies enqueue hint
scrap-pub lookup "https://example.com/item/view/121936/s0e1"

# TV show — prints Type: series, Year, Seasons list, the current S/E from
# the URL (if any), and a TV Shows enqueue hint
scrap-pub lookup "https://example.com/item/view/30658/s10e4"

# Full episode breakdown: walk every season and list each episode URL.
# For a 13-season show this takes ~20 s and prints a progress bar on
# stderr while it works. Use this when you need the complete set of
# per-episode URLs (e.g. to enqueue a single specific episode).
scrap-pub lookup "https://example.com/item/view/30658/s10e4" --episodes

# Also print the synopsis/plot text scraped from the item page, wrapped
# to the terminal. Useful when deciding what to download or for a human-
# readable blurb alongside title/year/kind.
scrap-pub lookup "https://example.com/item/view/121936/s0e1" --description

# Machine-readable output — suitable for piping into jq or another agent.
# The `description` field is always present in --json regardless of -d.
scrap-pub lookup "https://example.com/item/view/121936/s0e1" --json
```

Typical non-JSON output for a TV show:

```
================================================================
  When Calls the Heart
  Когда зовёт сердце
================================================================
  Type         series
  Year         2014
  Seasons      13 available: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
  Current      S10E04
  URL          https://example.com/item/view/30658

  Hint for agents: this is a TV show → enqueue with
    scrap-pub enqueue "https://example.com/item/view/30658" --output-dir "/path/to/TV Shows"
```

See [Downloading into a Plex library](#downloading-into-a-plex-library)
below for the enqueue side of the workflow — `lookup` is designed so you
can read its hint line and then run exactly that command (swapping in
your real Plex library path).

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
| `output_dir` | `<project>/output` | **Default** Plex-ready MKV root. Each task can override it with `--output-dir` — do *not* `config --set output_dir=...` as a per-task workaround. |
| `tmp_dir` | `<project>/tmp` | Working directory for ffmpeg (cleaned on task done) |
| `concurrency` | `2` | Parallel download workers. **Keep this at 2** — higher values are not faster (bandwidth-bound) and make the traffic pattern look non-human to Cloudflare, triggering challenges. |
| `video_quality` | `lowest` | `lowest` \| `highest` \| `720p` \| `1080p` |
| `audio_langs` | `["RUS","ENG","FRE"]` | Which audio tracks to include in MKV |
| `sub_langs` | `["rus","eng","fra"]` | Which subtitle languages to download as sidecars |
| `stall_timeout_sec` | `300` | Seconds without ffmpeg progress before killing download |
| `min_free_space_gb` | `10` | Advisory free-space floor for both the default `output_dir` and any per-task `--output-dir`. The enqueue-time and task-start checks refuse to proceed when the target filesystem has less than this much free (plus a duration-aware adjustment, ~3 GB per hour of runtime). Set to `0` to disable entirely — not recommended, the daemon will still crash mid-download on ENOSPC, it just won't give you the early warning. |

---

## Output Structure

Downloads land at `{output_root}` with Plex-ready names. `{output_root}` is
`config.output_dir` by default, or the per-task `--output-dir` path if one
was passed at enqueue (see [Downloading into a Plex library](#downloading-into-a-plex-library) below).

```
{output_root}/
  Hoppers(2026)/
    Hoppers(2026).mkv                    ← movie, h264 video + RUS/ENG audio tracks
    Hoppers(2026).rus.srt                ← subtitle sidecar (if available)
    Hoppers(2026).jpg                    ← poster
    Hoppers(2026).info.json              ← metadata (includes `description`)

  Big Mistakes(2026)/
    Season 01/
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.mkv
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.rus.srt
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.eng.srt
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.info.json
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace-thumb.jpg
      ...
    poster.jpg
    show.info.json                       ← show-level metadata (includes `description`)
```

Naming follows Plex media naming conventions (see `docs/spec.md → Output structure`).

**Metadata notes:**
- Both `show.info.json` (series) and `{Title}.info.json` (movie) carry a
  `description` field holding the synopsis/plot text scraped from the item
  page. For series this is the same text shown on any individual episode
  page — the site doesn't expose a distinct "show overview" blurb.
- `show.info.json` + `poster.jpg` are written once. When a second episode
  of an already-scaffolded series is enqueued, `scaffold()` short-circuits
  the show-level work instead of re-downloading the poster or overwriting
  `show.info.json`.

---

## Downloading into a Plex library

The default `config.output_dir` is a single directory shared by every task.
When you want one specific download to land under a Plex (or Jellyfin /
Emby / Kodi) library path — so the media server picks it up without any
move/symlink step — pass `--output-dir PATH` at enqueue time:

```bash
# TV show → "TV Shows" library
scrap-pub enqueue "https://example.com/item/view/122266" \
  --output-dir "/mnt/plex/TV Shows"

# Movie → "Movies" library
scrap-pub enqueue "https://example.com/item/view/121639/s0e1" \
  --output-dir "/mnt/plex/Movies"

# Multiple URLs in one invocation — the flag applies to all of them
scrap-pub enqueue URL1 URL2 URL3 --output-dir "/mnt/plex/TV Shows"
```

The inside-the-root layout is identical to the default (same Plex-ready
`ShowName(Year)/Season XX/episode.mkv` structure), so a Plex scanner
watching `/mnt/plex/TV Shows` will see a clean tree with poster, thumbnails,
`.info.json`, and subtitle sidecars in the right places.

### Rules and guarantees

- `PATH` is **resolved client-side** before the WebSocket call — `~`
  expands and relative paths become absolute. The daemon re-validates on
  its own filesystem (which may differ from the CLI host when using a
  remote daemon).
- If the directory doesn't exist but its *parent* does and is writable,
  the daemon creates it. If the parent is missing too, the enqueue fails
  immediately — this catches typos like `/mtn/plex` → no stray trees.
- Every task's `output_dir` is stored on the row and persists across
  daemon restarts. Use `scrap-pub show TASK_ID` to verify — the
  `output_dir  : /mnt/plex/TV Shows  (custom)` line shows up when the
  override is active.
- **Re-enqueueing a URL with a different `--output-dir` fails loudly**
  with a "task N already exists with a different output_dir; delete it
  first" error. `INSERT OR IGNORE` would have silently kept the original
  path, and you would never know your override was ignored — so the code
  checks explicitly. Delete the old task first if you really want to
  change the destination.
- **Never** use `scrap-pub config --set output_dir=...` as a per-task
  workaround. That changes the default for every *other* queued task
  too. `--output-dir` is the only correct mechanism.
- The `tmp_dir` is not overridable — scratch files always live under
  `config.tmp_dir`, no matter where the final output goes.

### Free-space check (advisory)

Before enqueue succeeds, the daemon checks:

```
free_space(PATH) >= min_free_space_gb * 1 GB
```

After the manifest is parsed (and duration is known) a second, more
refined check fires using `max(min_free_space_gb, duration_sec/3600 * 3 +
2)` GB. For a 10-hour season that's 32 GB, roughly what a 1080p rip
takes. Both checks are **advisory**, not atomic: two workers can both pass
the check and then race for the last few GB — in that case the merge fails
with a `TaskFSError` that surfaces as a readable `last_error`.

Set `min_free_space_gb=0` in config to disable the check entirely. Not
recommended — the daemon will still crash on ENOSPC mid-download, you just
won't get the early warning.

---

## Filesystem errors (troubleshooting)

Every filesystem failure — whether it hits at enqueue, at task start, or
mid-download — surfaces as a concise, path-tagged string. Read the message
literally; the daemon does not truncate it.

| Error | Meaning | Fix |
|-------|---------|-----|
| `output directory parent does not exist: /mtn/plex (typo?)` | You passed `--output-dir /mtn/plex/...` but `/mtn` doesn't exist. Almost always a typo (`/mtn` → `/mnt`). | Re-run with the correct path. The daemon never creates parent directories — it wants you to notice typos. |
| `not a directory: /home/user/plex` | A *file* already exists at that path. | Remove/rename it, or point `--output-dir` at an actual directory. |
| `not writable: /mnt/plex (permission denied)` | The directory exists but the daemon can't write to it. Common when the library is owned by the `plex` user and scrap-pub runs as your user. | Fix the permissions (e.g. `sudo chown -R $USER:$USER /mnt/plex` in a dev setup, or add your user to the `plex` group, or set `g+w` on the library). |
| `insufficient free space at /mnt/plex: 2.1 GB free, need 10 GB` | Less than `min_free_space_gb` of room on the target filesystem. | Free up space, lower `min_free_space_gb`, or point `--output-dir` at a different volume. |
| `task 42 already exists for this item with a different output_dir (/mnt/plex/Movies). Delete it first, then re-enqueue.` | Re-enqueue conflict. The item was previously enqueued with a different destination. | `scrap-pub skip 42` (or delete the row directly) and re-enqueue. |
| `writing merged MKV to /mnt/plex/foo.mkv: No space left on device` | Disk filled up during the final merge, *after* both free-space checks passed. The checks are advisory — this happens when two workers race, or when a parallel unrelated process wrote a large file at the wrong moment. | Free space and `scrap-pub retry <ID>`. |
| `output directory disappeared: /mnt/plex` (in `last_error`) | The mount vanished or the directory was renamed between enqueue and task start. | Re-mount / re-create the directory and `scrap-pub retry <ID>`. |

`scrap-pub show TASK_ID` and the web UI both display `last_error`
verbatim — there's no hidden stack trace to dig up.

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

# Per-task output directory — optional; omit for the default output_dir
asyncio.run(cmd({
    "cmd": "enqueue",
    "url": "https://example.com/item/view/122266",
    "output_dir": "/mnt/plex/TV Shows",
}))

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
