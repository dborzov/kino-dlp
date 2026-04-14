# Internals

> One-line summary: implementation rationale — why the code looks the way it does, and the gotchas that will bite future contributors.
>
> Last updated: 2026-04-14

For the user-facing behavior see [spec.md](spec.md). For the system map see
[architecture.md](architecture.md). This doc is for anyone changing the code.

---

## Why curl-cffi (not aiohttp / httpx)

Kino-style target sites sit behind Cloudflare. The `cf_clearance` cookie is tied to a TLS fingerprint and a JA3 hash — any client that doesn't impersonate a real browser at the TLS layer gets served a challenge page instead of the real response.

`curl_cffi` wraps libcurl-impersonate and ships presets for Chrome, Edge, Safari, Firefox. We use the `chrome` preset. `aiohttp` and `httpx` fail here with 403 or login-redirect even when cookies are valid.

Consequence: scraper calls are blocking libcurl. They run on `net_executor` (a 4-thread pool) via `loop.run_in_executor`. The scheduler loop stays reactive.

## Why SQLite WAL mode

WAL (write-ahead log) lets the WebSocket handler read the DB while a worker is writing to it. Without WAL, a reader blocks on a writer and vice versa — which would starve the UI during active downloads.

All writes go through `db_executor` (a single-thread pool) so we never hit SQLite's thread-affinity rules. Readers and writers end up on the same thread in practice, but WAL is cheap insurance for the cases where they don't.

## Why one row per stream

Naive approach: one `tasks` row per episode, with JSON columns for video/audio/sub URLs. Resume would mean re-parsing JSON and re-running the whole task. We chose one row per track (`streams` table) instead because it buys three things for almost no cost:

1. **Granular resume** — if video is `done` but audio was mid-download when the daemon was killed, only audio restarts. The video file stays on disk, the row stays at `done`.
2. **Post-hoc additions** — `scrap-pub add-audio TASK_ID URL` is a single-row insert. The worker picks it up as a normal stream and remuxes into the existing MKV.
3. **Per-stream UI progress** — each row has its own `progress_pct`, so the UI can show a bar per track.

The UNIQUE index (see below) ensures we don't double-insert the same track on re-scrape.

## `db_run` and `functools.partial`

`loop.run_in_executor(executor, fn, *args)` takes positional args only — it **cannot forward `**kwargs`**. When a DB helper has keyword-only parameters (most of them do), you have to bind them first:

```python
from functools import partial

# Wrong — run_in_executor silently drops the kwarg
await loop.run_in_executor(db_executor, db.upsert_stream, task_id, stream_type=...)

# Right
await loop.run_in_executor(db_executor, partial(db.upsert_stream, task_id, stream_type=...))
```

`downloader.py` does this for every DB call. If you add a new helper with kwargs and skip the `partial`, it won't crash — the kwarg just vanishes. Hunt for this when a field mysteriously stays `None`.

## `Config._cfg_path` — load path vs save path

Early versions of `config.py` had `save()` always write to the default `~/.config/scrap-pub/config.json`. This broke tests: a test would call `Config.load(tmp_path)`, mutate it, call `save()`, and the change would land in the real user's config. Worse, test-run-to-test-run contamination: `concurrency: 5` would leak in from a prior test.

Fix: `Config.load()` stashes the path it loaded from in `_cfg_path`. `save()` and `update()` write back there.

When writing tests:

```python
cfg = Config.load(tmp_path / "config.json")   # good — isolated
cfg = Config.load()                            # bad in tests — hits real user config
```

## Movie task sentinel values

The `tasks` table has `UNIQUE(item_id, season, episode)` to dedupe re-enqueues. For TV episodes that's natural: `(12345, 1, 3)`. For movies there's no season or episode — we'd want `NULL` but SQLite's UNIQUE treats each `NULL` as distinct (which defeats dedupe).

Solution: movies use `season=0, episode=1` as sentinel values. Both columns are declared `NOT NULL`. The UNIQUE constraint then catches duplicate movie enqueues.

If you later want season=0 to mean "specials" for series, pick a different sentinel for movies (e.g. `-1`) rather than collide.

## Per-task `output_dir` + filesystem guards

The final-output root used to be hard-coded to `config.output_dir`. That's
fine for a single user who only downloads into one library, but it forces
any "drop this into my Plex TV Shows folder" workflow to mutate the global
config, which then affects every *other* queued task too. The fix: one
nullable `tasks.output_dir` column and an optional `--output-dir` flag on
`enqueue`. `None` = use the config default, which is the long-standing
behaviour.

### Why a helper (`task_output_root`) and not `task.output_dir or config.output_dir`

Lots of code paths need the effective root: mkv_path computation, scaffold
call, sub sidecar path, `add_sub_to_task`, etc. Spreading `task.get("output_dir") or config.output_dir` across all of them is a recipe for
the CLI-override fix being "mostly implemented". `downloader.task_output_root(task, config)` is one line, does the `~` expansion, and is imported by
every site that needs it. If a future column participates in the resolution
(e.g. a relative subdir), there's one place to change.

### Validation in three checkpoints, not one

A single enqueue-time check is not enough:

1. **At enqueue** (ws_server `CMD_ENQUEUE`): `validate_task_output_dir`
   fires *before* `scrape()` and before any DB write. Failure → WS error, no
   task, no poster, no `.info.json`. The motivation is an AI agent mis-typing
   `/mtn/plex` — you don't want the error to surface after a minute of
   scraping and thumbnail downloads that end up in the wrong place. The
   validator auto-creates missing directories only when the *parent* exists
   and is writable, which gives the user typo-catching for free.
2. **At task start** (top of `download_task`): re-resolve and re-validate.
   Between enqueue and the moment a worker claims the task, the disk could
   be unmounted, the directory renamed, or permissions tightened. Without
   this check the worker crashes mid-download with a raw `OSError`; with it
   the task transitions to `failed` with a one-line `last_error` the UI
   can render.
3. **Mid-download**: `TaskFSError` wraps `OSError` with the operation name
   and the path at the three fragile sites (work-dir `mkdir`, merged-MKV
   write, sidecar write). `worker_task`'s existing `except Exception` hook
   turns this into a readable `last_error` — `writing merged MKV to
   /mnt/plex/foo.mkv: No space left on device` beats `[Errno 28] ENOSPC`.

### Why the free-space check is advisory, not atomic

`shutil.disk_usage(path).free` gives a snapshot. Two workers can both pass
the check and then race for the last few GB during a simultaneous merge —
that's the TaskFSError handler's job. Making the check atomic would require
a reservation table that is cleaned up on every task exit, including
crashes, which is way more state than it's worth.

The check uses `estimate_min_free_gb(duration_sec, base_min_gb)` which
returns `max(base_min_gb, int(duration_sec/3600 * 3) + 2)` — a conservative
3 GB/hour floor plus 2 GB overhead. For a 10-hour season that's 32 GB,
which is roughly what a 1080p rip takes. At enqueue time `duration_sec` is
unknown so the check falls back to `min_free_space_gb` from config; at task
start (after manifest parse) the refined estimate fires a second time.

### Re-enqueue conflict: fail, don't silently ignore

`db_insert_task` uses `INSERT OR IGNORE` on `UNIQUE(item_id, season,
episode)`, so re-enqueueing the same episode is a no-op. But with the new
feature that means a re-enqueue with a *different* `--output-dir` would
silently keep the original and ignore the new path. An AI agent scripted
against scrap-pub would have no way to tell whether `--output-dir` was
honoured. `_enqueue_url` now explicitly checks for existing matching rows
*before* insert and raises a `ValueError` naming the existing task id if
the new and old `output_dir` differ. `INSERT OR IGNORE` is still used
afterwards so the no-conflict case is still idempotent.

### Why not move `add_sub_to_task`'s sidecar into `config.output_dir` regardless

Tempting — "subtitles always live under the daemon default, tasks are
mutable, done" — but wrong. The whole point of `--output-dir` is that a
Plex scanner watching `/mnt/plex/TV Shows` sees a coherent tree. A sidecar
that landed under the default `~/output` would be ignored by the scanner
and silently orphaned. `add_sub_to_task` reloads the task row and calls
`task_output_root(task, config)` just like the initial download path does.

## Scaffold scope: one episode, not the whole season

Early `scaffold()` unconditionally walked every episode of every season
present in `info["seasons_data"]` and wrote a `.info.json` + `-thumb.jpg`
for each one. For a single `scrap-pub enqueue URL/s03e05` that meant
dozens of HTTP fetches and image writes for episodes the user never asked
for — and every re-enqueue of a neighbour episode repeated the work.

Two companion changes fix this:

1. **`scaffold(info, root, *, only=(season, ep))`** — when `only` is set,
   the per-episode loop skips every other `(season, episode)` pair. The
   downloader passes its task's own `(season, episode)` tuple; movies
   pass `None` (they have no episode tree to walk). `scrape(url, only_season=N)` is the companion: when the caller only needs one
   episode, we also avoid fetching every *other* season's episode listing
   over the network, not just skipping them at write time.
2. **Show-level short-circuit** — before writing `show.info.json` /
   `poster.jpg`, `scaffold()` checks whether both already exist on disk.
   If they do, it leaves them untouched. Rationale: once a series has
   been scaffolded, re-running scaffold for a different episode is
   redundant work *and* would overwrite any hand-edits the user made to
   `show.info.json` (episode numbering fixes, translated titles).

Why a presence check instead of a "scaffold-ran" flag in the DB: the
filesystem is the source of truth for a Plex library. The user can
delete `show.info.json` to force a re-scaffold, or symlink it in from
elsewhere — both cases are supported by a dumb `exists()` probe and
would break if we tracked scaffold state in SQLite.

The `description` field on `show.info.json` is populated from the same
`#plot` text the individual episode pages use — target sites publish
the same plot on every episode URL within a series, so the show-level
description is just the per-episode description reused. `scrape()`
already visits at least one episode page to resolve the media tree, so
there's no extra network call.

## Shared `timespec.parse_since`: server-side, not CLI-side

`--since today` / `--since week` exists in two places: the CLI (`scrap-pub list`) and the web UI's Today/Week/Month chips. Both go through
`CMD_LIST` and both end up in a SQL `WHERE enqueued_at >= ?`. Originally
the CLI parsed the spec into a UTC ISO string before sending, while the
UI sent the literal string `today`. The daemon then compared
`enqueued_at >= 'today'` — which SQLite happily runs lexically, and
`'2026-04-14T12:00:00+00:00' < 'today'` in ASCII, so the UI's Today chip
silently returned nothing for tasks completed today.

Fix: a single `scrap_pub.daemon.timespec.parse_since` module, called
from **both** the CLI (before sending) and `ws_server.CMD_LIST` (on
receive). The server-side call is the important one — it closes the
door on any future client that forwards the literal spec. An invalid
spec raises `ValueError`, which the CLI reports directly and the WS
server wraps in an error reply.

Two behavioural details worth keeping in mind:

- **`today`/`yesterday` pivot on the user's local calendar day**, not
  UTC's. A user in PDT expects "today" to mean local 00:00–24:00; with
  UTC midnight they'd see last night's downloads drop off the list at 5
  PM local time. The parser calls `datetime.now().astimezone()` to pick
  up the system timezone, computes local midnight, then converts to UTC
  for the wire format.
- **`week`/`month` are rolling offsets**, not calendar boundaries. A
  literal "past 7 days" is what the chip labels imply. `week` = `now - 7d`, `month` = `now - 30d`.

Output is always a UTC ISO-8601 string so SQLite's lex comparison on
`enqueued_at` / `completed_at` stays correct regardless of what
timezone the client is in.

---

## SQLite UNIQUE + COALESCE — must be a separate index

You cannot write:

```sql
CREATE TABLE streams (
    ...,
    UNIQUE(task_id, stream_type, COALESCE(label, ''))   -- ← SQLite rejects this
);
```

SQLite only allows column names in inline `UNIQUE` clauses, not expressions. The workaround is a separate `CREATE UNIQUE INDEX`:

```sql
CREATE UNIQUE INDEX idx_streams_task_type_label
    ON streams(task_id, stream_type, COALESCE(label, ''));
```

Expression indexes work fine — it's only the inline table-level `UNIQUE` that's restricted. This matters because video streams have no `label` (NULL) and we still need to dedupe them.

## Why ETA is computed in `_parse_progress_line`

ETA needs three numbers: `duration_sec` (target), `elapsed_sec` (how far into the media), and `speed` (ffmpeg's x-realtime factor). All three are already parsed out of a single ffmpeg progress line in `_parse_progress_line()`. Computing ETA anywhere else would mean re-deriving those values or carrying them forward through a second function — both are invitations for drift.

So `_parse_progress_line()` returns `eta_sec` directly, and every caller (downloader progress callback → `stream_progress` cache → WebSocket `stream_progress` event → CLI `show`/`list -v` overlay → UI stream row) reads the same field. There is no separate smoothing pass: the value is `(duration - elapsed) / speed`, rounded to int. It's jittery on the first few ticks and stable afterwards, which matches what the user expects from an ffmpeg-style progress display. ETA is `None` when speed is unknown, duration is missing, or the stream is within one second of the end — "almost done" reads better than "0s remaining".

## Why output size is computed on demand, not persisted

`tasks` has no `output_size_bytes` column by design. Two reasons:

1. **Ground truth differs by lifecycle phase.** For a `done` task the MKV file is the final artefact — `os.stat(mkv_path).st_size` is authoritative and the per-stream sizes are stale (tmp files are cleaned on remux). For anything earlier (`pending`/`active`/`failed`), the MKV doesn't exist yet and the only signal is `SUM(streams.size_bytes)`, with a live override from `state.stream_progress[sid].size_bytes` during active downloads. A persisted column would have to be updated at both transitions, and would still be wrong between them.
2. **Cost is trivial.** `CMD_LIST` already fetches the streams per task for verbose mode; non-verbose fires one extra batched `SELECT task_id, id, size_bytes FROM streams WHERE task_id IN (…)` for the page (O(200) rows). That's cheaper than the write amplification a persisted column would add on every progress tick.

The helper lives in `ws_server._compute_output_size(state, task, streams)` and stamps `output_size_bytes` onto task dicts before replying. It is a computed field, never written back to the DB.

## Why the SQL gate lives server-side

`scrap-pub sql` is a read-only-by-default escape hatch. The safety gate (strip comments, inspect first token, accept only `SELECT`/`WITH`/`PRAGMA`/`EXPLAIN`) runs on the **server** inside `ws_server`, not in the CLI. Three reasons:

1. **Defense in depth for LLM agents.** An agent invoking the daemon via raw WebSocket can skip the CLI entirely. If the gate lived in `cli_main.py`, `{"cmd": "sql", "query": "DROP TABLE tasks"}` from a direct WS client would succeed.
2. **Single SQLite owner.** Every SQL statement runs on `db_executor` through `db_run`, so all writes are funneled through one thread and one connection. Letting the gate run anywhere else would split policy from enforcement.
3. **Comment-stripping is not optional.** `/* SELECT */ DROP TABLE tasks` and `WITH x AS (...) DELETE FROM tasks` both look like SELECTs to a naive prefix check. The gate strips `--` and `/* */` comments before tokenizing, so it must be a real SQL-aware step, not a shell-side regex.

`--write` is the explicit escape: the CLI prints a warning and the server bypasses the whitelist. DML/DDL is deliberately noisy to use.

## ffmpeg stall detection

`ffmpeg.run_ffmpeg()` runs ffmpeg with `stderr=PIPE`, parses progress lines (`frame=`, `time=`, `speed=`, `size=`) as they arrive, and maintains a `last_tick` timestamp. A sibling `watchdog` task wakes every 30 seconds and kills the process if `last_tick` is older than `stall_timeout_sec` (default 300s).

Why a separate watchdog task: ffmpeg can hang mid-TCP-read and produce no stderr output. If we only reset the timer inside the stderr parsing loop, a hung ffmpeg would never be noticed because we'd be blocked on `proc.stderr.readline()`.

On kill, `run_ffmpeg` raises `StallError`. The downloader catches it, marks the stream `pending` (not failed), and lets the retry logic handle backoff.

## WebSocket server port in the sandbox

`websockets.asyncio.server.serve(host, port)` with a fixed non-zero port hangs forever in the Claude Code sandbox — no error, no bind, no log. Same code works fine on a normal host. It's a sandbox network restriction on binding to predetermined ports.

Tests work around this with `port=0` (random available port) and read back the actual port from the server object. The daemon itself uses the configured port and works fine on real hosts — only the in-sandbox test runs needed this.

If you hit the same symptom outside the sandbox, the cause is different — look for port-already-in-use or firewall issues.

## Threading invariant: "DB calls only on db_executor"

Never call a `db.*` function directly from an async coroutine. Always go through the `db_run()` helper in `scheduler.py`, which dispatches to `db_executor`. Two reasons:

1. **Thread-affinity** — SQLite connections are bound to the thread that opened them. Single-threaded executor = single connection = no cross-thread use.
2. **Ordering** — WAL allows concurrent reads, but write ordering matters for things like "insert task, then emit enqueued event". Funneling writes through one thread removes any race.

The handful of places that call `sqlite3.connect()` directly (tests, `db.init_schema()`) are all synchronous code paths on setup/teardown and never run concurrently with workers.

## Why stdlib HTTPServer + websockets, not FastAPI

scrap-pub has exactly two HTTP routes (`/` and `/health`) and one WebSocket endpoint. FastAPI would drag in Starlette, uvicorn, and pydantic-v2-for-routes, plus ASGI scaffolding. We already have pydantic for models and asyncio for everything else. Stdlib `http.server` is 40 lines and has zero moving parts.

If we ever grow real HTTP routes (upload endpoints, REST API), the calculus changes. For now: KISS.
