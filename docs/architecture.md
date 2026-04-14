# Architecture

> One-line summary: process model, module map, SQLite schema, and WebSocket topology of the scrap-pub daemon.
>
> Last updated: 2026-04-13

For CLI usage, config keys, and the WebSocket command set see [spec.md](spec.md). For why
this design was chosen see [internals.md](internals.md).

---

## System map

```
            ┌──────────────────┐         ┌────────────┐
 browser ──▶│ http://:8765  /  │         │ ws://:8766 │◀── scrap-pub CLI
            │ stdlib HTTPServer│◀────────│ websockets │◀── Web UI (embedded HTML)
            └──────────────────┘    WS   └────────────┘
                                           │
                                           ▼
                         ┌───────────────────────────────────┐
                         │     asyncio event loop (main)      │
                         │                                    │
                         │  scheduler_loop → work_queue       │
                         │  worker_task 0..N                  │
                         │  broadcaster  ← progress_queue     │
                         │  serve_ws                          │
                         └───────────┬───────────────────────┘
                                     │
                      ┌──────────────┼──────────────┐
                      ▼              ▼              ▼
              db_executor      net_executor    subprocess
              (1 thread,       (4 threads,     (ffmpeg,
               sqlite3)         curl-cffi)      one per stream)
                      │              │              │
                      ▼              ▼              ▼
                 ~/.local/      target site    ./tmp/*.mp4
                 queue.db       HTTPS           ./tmp/*.m4a
                                                ./output/*.mkv
```

## Process model

A single Python process hosts everything:

| Component | Runs in | Purpose |
|-----------|---------|---------|
| `asyncio` main loop | Main thread | Scheduler, workers, WebSocket server, broadcaster |
| `db_executor` | `ThreadPoolExecutor(max_workers=1)` | All `sqlite3` calls — keeps SQLite single-threaded |
| `net_executor` | `ThreadPoolExecutor(max_workers=4)` | Blocking `curl_cffi` HTTP calls (scrape, manifest resolve) |
| `HTTPServer` | `threading.Thread(daemon=True)` | Stdlib HTTP: `GET /` → UI, `GET /health` → `{"ok":true}` |
| `ffmpeg` | `asyncio.subprocess` | One per stream download; stderr parsed async |

Workers coordinate via asyncio primitives:

- `work_queue: asyncio.Queue(maxsize=concurrency)` — pending tasks claimed from DB
- `progress_queue: asyncio.Queue()` — progress events buffered for broadcast
- `pause_event: asyncio.Event` — set = running, clear = paused
- `shutdown_event: asyncio.Event` — set on SIGTERM/SIGINT

All shared state lives in `AppState` (`scheduler.py`), passed by reference to every handler. This includes `stream_progress: dict[int, dict]` — a live per-stream cache keyed by `stream_id` with `pct`, `speed`, `eta_sec`, `elapsed_sec`, `size_bytes`. The downloader updates it on every ffmpeg progress tick, and `CMD_LIST` / `CMD_GET` overlay it onto stream dicts in their replies so the UI and CLI see live numbers without a DB round-trip. Entries are popped when the stream reaches a terminal status.

## Startup validation gate

`server_main.py` calls `Config.validate()` immediately after `Config.load()`. It returns `(errors, warnings)`; any error (unset/bad `website`, unwritable `output_dir` / `tmp_dir` / `db_path.parent`, bad concurrency, port collision, out-of-range ports) prints to stderr and exits with code **2** before any thread or socket is created. Warnings (e.g. missing `cookies_path`) print to stderr and the daemon continues — the first download will fail clearly with a cookie error.

## Module map

```
scrap_pub/
  models.py                  Pydantic models: MediaBase, Movie, TVSeries, Person, Episode
  scrapers/                  placeholder package for future site-specific scraper modules
  daemon/
    config.py                Config dataclass · load/save/update · tracks loaded path
    db.py                    SQLite schema · all CRUD · WAL mode
    session.py               curl-cffi session · cookies from Netscape cookies.txt file
    scraper.py               scrape() · get_manifest_url() · parse_manifest() · scaffold()
    ffmpeg.py                run_ffmpeg() · _parse_progress_line() · StallError · merge/remux
    downloader.py            download_task() · add_audio_to_task() · add_sub_to_task()
    scheduler.py             AppState · db_run/net_run · scheduler_loop · worker_task · broadcaster · main()
    ws_protocol.py           CMD_*/EVT_* constants · encode/decode/reply helpers
    ws_server.py             serve_ws() · ws_handler() · broadcast() · command dispatch
    server_http.py           stdlib HTTPServer: GET / → web UI, GET /health
    ui.py                    HTML_UI: single-file web UI (5 tabs, per-stream progress bars)
    server_main.py           scrap-pub-server entry point
    cli_main.py              scrap-pub CLI: all subcommands via WebSocket
```

## SQLite schema

WAL mode is always on: WebSocket handler reads can overlap worker writes.

```sql
PRAGMA journal_mode = WAL;

-- One row per target-site item (movie or series)
CREATE TABLE items (
    id          TEXT PRIMARY KEY,     -- target-site numeric id
    kind        TEXT NOT NULL,        -- 'movie' | 'series'
    title_orig  TEXT NOT NULL,        -- original-language title from og:title
    title_ru    TEXT,
    year        INTEGER,
    url         TEXT NOT NULL,
    poster_url  TEXT,
    meta_json   TEXT,                 -- full scraped metadata JSON
    scraped_at  TEXT
);

-- One row per downloadable unit. Movie = 1; series = 1 per episode.
CREATE TABLE tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       TEXT NOT NULL REFERENCES items(id),
    kind          TEXT NOT NULL,      -- 'movie' | 'episode'
    season        INTEGER NOT NULL,   -- movies use sentinel 0
    episode       INTEGER NOT NULL,   -- movies use sentinel 1
    episode_title TEXT,
    media_id      TEXT,
    plex_stem     TEXT,               -- relative path stem under output_dir
    status        TEXT NOT NULL DEFAULT 'pending',
                                      -- pending | active | done | failed | skipped
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    enqueued_at   TEXT NOT NULL,
    started_at    TEXT,
    completed_at  TEXT,
    mkv_path      TEXT,
    UNIQUE(item_id, season, episode)
);
CREATE INDEX idx_tasks_status       ON tasks(status);
CREATE INDEX idx_tasks_item         ON tasks(item_id);
CREATE INDEX idx_tasks_enqueued_at  ON tasks(enqueued_at);
CREATE INDEX idx_tasks_completed_at ON tasks(completed_at);

-- One row per stream track. Enables granular resume and post-hoc additions.
CREATE TABLE streams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stream_type  TEXT NOT NULL,       -- 'video' | 'audio' | 'subtitle'
    label        TEXT,
    lang         TEXT,                -- ISO-639-2: rus, eng, fra, …
    forced       INTEGER DEFAULT 0,
    source_url   TEXT,                -- signed HLS URL (~24h TTL)
    resolved_at  TEXT,
    tmp_path     TEXT,
    out_path     TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
                                      -- pending | downloading | done | failed
    progress_pct REAL,
    size_bytes   INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    started_at   TEXT,
    completed_at TEXT
);
CREATE INDEX idx_streams_task   ON streams(task_id);
CREATE INDEX idx_streams_status ON streams(status);
CREATE UNIQUE INDEX idx_streams_task_type_label
    ON streams(task_id, stream_type, COALESCE(label, ''));

-- Global and per-task event log
CREATE TABLE logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,            -- INFO | WARN | ERROR
    msg     TEXT NOT NULL
);
CREATE INDEX idx_logs_task ON logs(task_id);
CREATE INDEX idx_logs_ts   ON logs(ts);

-- Runtime state: pause flag, cookie_error flag
CREATE TABLE kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Session cookies are **not** stored in SQLite — they live in a Netscape
`cookies.txt` file at `~/.config/scrap-pub/cookies.txt` (see [spec.md § Session
cookies](spec.md#session-cookies)). The daemon reads the file at startup and
rewrites it when the user uploads fresh cookies via `scrap-pub cookies FILE` or
the Web UI.

### Why stream-level tracking

One `streams` row per track (video, each audio, each subtitle) unlocks:

- **Granular resume** — if video is done but audio was mid-download when the daemon died, only audio restarts
- **Post-hoc additions** — `scrap-pub add-audio TASK_ID URL` inserts a new `streams` row; the worker downloads and remuxes it into the existing MKV without re-encoding
- **Per-stream progress bars** in the UI
- **Audit trail** — which CDN URL was used, when resolved, how many bytes

## Data flow: enqueue → MKV on disk

```
CLI/UI ─WS "enqueue"─▶ ws_server.dispatch ─▶ scraper.scrape(url)
                                                │
                                                ▼
                                         items + tasks rows
                                                │
                                                ▼
                            scheduler_loop claims pending task
                                                │
                                                ▼
                            worker_task → downloader.download_task(task)
                                                │
         ┌──────────────────────────────────────┼──────────────────────────────────────┐
         ▼                                      ▼                                      ▼
  scraper.get_manifest_url          scraper.parse_manifest              scraper.select_streams
  (net_executor, curl_cffi)          (net_executor, curl_cffi)          (pure fn, uses Config)
                                                │
                                                ▼
                                    upsert streams rows (pending)
                                                │
                                                ▼
             ┌──────── for each pending stream ────────┐
             ▼                                         ▼
   subtitles first                           video then audios
   ffmpeg -i HLS -c copy                     ffmpeg with stall watchdog
   → tmp/*.srt                                → tmp/*.mp4 / *.m4a
             │                                         │
             └──────────────────┬──────────────────────┘
                                ▼
                   ffmpeg merge (stream-copy, no re-encode)
                                ▼
                      output/{plex_stem}.mkv
                                ▼
                    task.status = done, cleanup tmp
```

Every database write emits a `progress_queue` event → `broadcaster` → WebSocket clients.

## WebSocket topology

One WebSocket server on `ws_port` (default `8766`). Single JSON protocol used by both:

- **Web UI** — persistent connection; receives push events, sends occasional commands
- **`scrap-pub` CLI** — short-lived: connect → send one command → read reply → close

Server pushes to all connected clients on every state change. Dead client writes are caught with `return_exceptions=True` so one stuck client can't block the others.

See [spec.md § WebSocket protocol](spec.md#websocket-protocol) for the full command and event list.

## Filesystem layout at runtime

```
{output_dir}/                       final MKVs + sidecars (see spec.md)
{tmp_dir}/                          ffmpeg working files, cleaned on task done
~/.config/scrap-pub/config.json     daemon config (overridable with --config)
~/.config/scrap-pub/cookies.txt     Netscape cookies.txt (overridable via cookies_path)
~/.local/share/scrap-pub/queue.db   SQLite queue (overridable via config)
```

All three paths support `~` expansion and are created on first run.
