# scrap-pub SQL Agent Skill

Use `scrap-pub sql` to ask structured questions against the daemon's SQLite
queue database without round-tripping through several CLI commands. The query
runs inside the running daemon process (single SQLite owner, WAL mode — your
long-running `SELECT` will never block an active worker), and the daemon
enforces a read-only safety gate by default.

The companion skill is [`scrappub_skill.md`](scrappub_skill.md), which covers
the general daemon workflow (enqueue, list, show, logs, cookies).

---

## Invocation

```bash
scrap-pub sql "QUERY"                          # read-only, table output
scrap-pub sql "QUERY" --json                   # JSON rows
scrap-pub sql "QUERY" --csv                    # CSV output
scrap-pub sql "QUERY" --limit 500              # cap result set (default 1000)
scrap-pub sql -f query.sql                     # read query from file
scrap-pub sql -f - < query.sql                 # read query from stdin
scrap-pub sql "UPDATE ..." --write             # escape hatch for DML/DDL
```

The daemon must be running — SQL goes over the same WebSocket channel as every
other command.

If the daemon is **not** running and you need to inspect the queue DB
directly (read-only), use the local path-lookup CLI, which works without a
daemon:

```bash
sqlite3 -readonly "$(scrap-pub paths db)" \
    "SELECT id, status, plex_stem FROM tasks ORDER BY id DESC LIMIT 10;"
```

Note that the daemon holds a WAL writer lock while running; reading the DB
file directly while the daemon is up is safe (WAL mode) but writes from
outside the daemon are not. Prefer `scrap-pub sql` whenever the daemon is
available.

### Safety gate

By default the server strips SQL comments, looks at the first statement token,
and rejects anything that isn't `SELECT`, `WITH`, `PRAGMA`, or `EXPLAIN`.
Rejections return exit code **2** from the CLI. To run `INSERT`/`UPDATE`/
`DELETE`/`DDL`, pass `--write` explicitly — the CLI prints a warning and the
server bypasses the gate.

### Row cap

`--limit N` (default 1000) maps to the server-side `max_rows`. If more rows
matched, the reply has `truncated: true` and the CLI prints a footer. Raise
the limit if you genuinely need more rows; otherwise add `LIMIT` to the query.

### Timestamps

All `*_at` columns are ISO-8601 UTC strings (e.g.
`2026-04-13T14:03:22.123456+00:00`). They sort and compare correctly as text,
so filters like `WHERE enqueued_at >= '2026-04-01'` work without casting.
SQLite date functions (`date('now','-7 days')`, `datetime('now','-2 hours')`)
also work and are often more convenient.

---

## Schema reference

Lifted from `scrap_pub/daemon/db.py → SCHEMA`. Types follow SQLite's loose
affinity rules — treat them as hints.

### `items` — scraped metadata about the source (one row per movie or series)

| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | Site-assigned item id (e.g. `"121639"`) |
| `kind` | TEXT | `movie` \| `series` |
| `title_orig` | TEXT | Original-language title (used for output paths) |
| `title_ru` | TEXT | Localized (Russian) title when present |
| `year` | TEXT | Release year, nullable |
| `url` | TEXT | Canonical item URL on the target site |
| `poster_url` | TEXT | Poster image URL |
| `meta_json` | TEXT | Full scrape payload as JSON |
| `scraped_at` | TEXT | ISO-8601 UTC |

### `tasks` — one row per downloadable unit (movie, or one episode)

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | **Use this as the task reference everywhere** |
| `item_id` | TEXT → items(id) | Parent item |
| `kind` | TEXT | `movie` \| `episode` |
| `season` | INTEGER | `0` for movies, `1+` for episodes |
| `episode` | INTEGER | `1+`; movies use `1` as NOT-NULL sentinel |
| `episode_title` | TEXT | Per-episode title, nullable |
| `media_id` | TEXT | Stream manifest id on the target site |
| `plex_stem` | TEXT | Plex-ready filename stem (e.g. `Hoppers(2026)/Hoppers(2026)`) |
| `status` | TEXT | `pending` \| `active` \| `done` \| `failed` \| `skipped` |
| `attempts` | INTEGER | Incremented on retry |
| `last_error` | TEXT | Most recent error message; cleared on retry |
| `enqueued_at` | TEXT | ISO-8601 UTC (set at insert) |
| `started_at` | TEXT | ISO-8601 UTC (rewritten on every claim; `NULL` after retry until re-claimed) |
| `completed_at` | TEXT | ISO-8601 UTC for `done`/`failed`/`skipped`; cleared on retry |
| `mkv_path` | TEXT | Absolute output path once the task finishes |

Unique constraint: `(item_id, season, episode)` — retrying is idempotent.
Indexes: `status`, `item_id`, `enqueued_at`, `completed_at`.

### `streams` — per-stream children of a task (video, each audio track, each subtitle)

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `task_id` | INTEGER → tasks(id) ON DELETE CASCADE | |
| `stream_type` | TEXT | `video` \| `audio` \| `subtitle` |
| `label` | TEXT | Human label (language or track name); part of the unique key |
| `lang` | TEXT | ISO-639-2 code when known |
| `forced` | INTEGER | 0/1, for forced subtitles |
| `source_url` | TEXT | HLS manifest URL |
| `resolved_at` | TEXT | ISO-8601 UTC when the URL was resolved |
| `tmp_path` | TEXT | Working file during download |
| `out_path` | TEXT | Final location (sidecars) |
| `status` | TEXT | `pending` \| `downloading` \| `done` \| `failed` |
| `progress_pct` | REAL | Last flushed % (0–100); live % lives in memory, not DB |
| `size_bytes` | INTEGER | Size at last flush |
| `attempts`, `last_error` | | Same semantics as on tasks |
| `started_at`, `completed_at` | TEXT | ISO-8601 UTC |

Unique key: `(task_id, stream_type, COALESCE(label, ''))`.

### `logs` — append-only event log (task-scoped or global)

| Column | Notes |
|--------|-------|
| `id` | PK |
| `task_id` | nullable (global lines have `NULL`) |
| `ts` | ISO-8601 UTC |
| `level` | `INFO` \| `WARN` \| `ERROR` |
| `msg` | Text |

### `kv` — flat key/value store for daemon runtime state

Notable keys: `paused` (`"true"`/`"false"`), `cookie_error` (`"true"`/`"false"`).

---

## Recipes

### Count tasks by status

```sql
SELECT status, COUNT(*) AS n
FROM tasks
GROUP BY status
ORDER BY n DESC;
```

### Failed tasks from the last 24h with their error messages

```sql
SELECT id, plex_stem, attempts, completed_at, last_error
FROM tasks
WHERE status = 'failed'
  AND completed_at >= datetime('now', '-1 day')
ORDER BY completed_at DESC;
```

### Top-10 largest finished tasks

`tasks` doesn't persist the MKV byte count; `streams.size_bytes` is the best
proxy (it's the sum of what ffmpeg wrote per stream before remux).

```sql
SELECT t.id,
       t.plex_stem,
       t.completed_at,
       SUM(s.size_bytes) AS total_bytes
FROM tasks t
JOIN streams s ON s.task_id = t.id
WHERE t.status = 'done'
GROUP BY t.id
ORDER BY total_bytes DESC
LIMIT 10;
```

### Show all streams for a given task

```sql
SELECT id, stream_type, label, lang, status, progress_pct, size_bytes
FROM streams
WHERE task_id = 42
ORDER BY stream_type, id;
```

### Tasks where the download probably stalled

Active tasks that started more than two hours ago and haven't finished.

```sql
SELECT id, plex_stem, started_at
FROM tasks
WHERE status = 'active'
  AND started_at < datetime('now', '-2 hours')
ORDER BY started_at;
```

### Enqueue volume per day, last week

```sql
SELECT substr(enqueued_at, 1, 10) AS day,
       COUNT(*) AS tasks,
       SUM(status = 'done')   AS done,
       SUM(status = 'failed') AS failed
FROM tasks
WHERE enqueued_at >= date('now', '-7 days')
GROUP BY day
ORDER BY day DESC;
```

### Most recent 5 error log lines

```sql
SELECT ts, task_id, msg
FROM logs
WHERE level = 'ERROR'
ORDER BY id DESC
LIMIT 5;
```

### Find duplicated in-progress streams (diagnostic)

```sql
SELECT task_id, stream_type, COALESCE(label,'') AS label, COUNT(*) AS n
FROM streams
GROUP BY task_id, stream_type, label
HAVING n > 1;
```

Expected: zero rows — the unique index prevents it. Non-zero would mean the
index was dropped or the daemon is running against an older schema.

### Re-enqueue a failed task with `--write`

The idiomatic way is `scrap-pub retry ID`, but if you need to do it via SQL
(batch, conditional criteria, etc.):

```sql
UPDATE tasks
SET status = 'pending',
    attempts = attempts + 1,
    started_at = NULL,
    completed_at = NULL,
    last_error = NULL
WHERE id = 42;
```

Run with `--write`. Note that this bypasses the normal retry codepath, which
also broadcasts a `task_update` event — the web UI won't notice until its
next poll.

---

## Gotchas

- **Timestamps are strings.** String comparison works because ISO-8601 sorts
  lexicographically. SQLite `date()`/`datetime()` functions accept them.
- **`status` is a free-form TEXT**, not an enum — stick to the five known
  values (`pending`, `active`, `done`, `failed`, `skipped`). Typos return
  zero rows, not an error.
- **Live progress is in memory, not the DB.** `streams.progress_pct` is
  updated only at settled points (status transitions). For live %, ETA, and
  speed during a download, use `scrap-pub show ID` or `scrap-pub list -v` —
  the daemon overlays the in-memory `stream_progress` cache onto the stream
  rows before replying.
- **The safety gate is server-side.** `scrap-pub sql --write` is the only
  way to run DML/DDL regardless of how you reformat the query (comments,
  leading whitespace, WITH clauses that embed DELETE — the gate strips
  comments and checks the first token).
- **WAL mode.** Long-running `SELECT`s don't block writes. Workers can still
  claim tasks and update streams while your report query runs.
