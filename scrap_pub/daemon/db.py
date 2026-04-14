"""
db.py — SQLite schema, migrations, and all typed query functions.

All functions are synchronous (sqlite3 is sync).
Async callers use: await loop.run_in_executor(db_executor, fn, conn, ...)

WAL journal mode lets the WebSocket server thread read while workers write.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title_orig  TEXT NOT NULL,
    title_ru    TEXT,
    year        TEXT,
    url         TEXT NOT NULL,
    poster_url  TEXT,
    meta_json   TEXT,
    scraped_at  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT NOT NULL REFERENCES items(id),
    kind            TEXT NOT NULL,
    season          INTEGER NOT NULL DEFAULT 0,
    episode         INTEGER NOT NULL DEFAULT 0,
    episode_title   TEXT,
    media_id        TEXT,
    plex_stem       TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    enqueued_at     TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    mkv_path        TEXT,
    UNIQUE(item_id, season, episode)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status      ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_item        ON tasks(item_id);
CREATE INDEX IF NOT EXISTS idx_tasks_enqueued_at ON tasks(enqueued_at);
CREATE INDEX IF NOT EXISTS idx_tasks_completed_at ON tasks(completed_at);

CREATE TABLE IF NOT EXISTS streams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stream_type  TEXT NOT NULL,
    label        TEXT,
    lang         TEXT,
    forced       INTEGER NOT NULL DEFAULT 0,
    source_url   TEXT,
    resolved_at  TEXT,
    tmp_path     TEXT,
    out_path     TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    progress_pct REAL,
    size_bytes   INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    started_at   TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_streams_task   ON streams(task_id);
CREATE INDEX IF NOT EXISTS idx_streams_status ON streams(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_streams_unique
    ON streams(task_id, stream_type, COALESCE(label, ''));

CREATE TABLE IF NOT EXISTS logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL,
    msg      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_task ON logs(task_id);
CREATE INDEX IF NOT EXISTS idx_logs_ts   ON logs(ts);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO kv VALUES ('paused',        'false');
INSERT OR IGNORE INTO kv VALUES ('cookie_error',  'false');
"""


# ── Connection ─────────────────────────────────────────────────────────────────

def open_db(db_path: Path) -> sqlite3.Connection:
    """Open (and initialise) the SQLite database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for statement in SCHEMA.split(";"):
        s = statement.strip()
        if s:
            conn.execute(s)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Items ──────────────────────────────────────────────────────────────────────

def db_upsert_item(conn: sqlite3.Connection, info: dict) -> None:
    conn.execute("""
        INSERT INTO items (id, kind, title_orig, title_ru, year, url, poster_url, meta_json, scraped_at)
        VALUES (:id, :kind, :title_orig, :title_ru, :year, :url, :poster_url, :meta_json, :scraped_at)
        ON CONFLICT(id) DO UPDATE SET
            title_orig = excluded.title_orig,
            title_ru   = excluded.title_ru,
            year       = excluded.year,
            poster_url = excluded.poster_url,
            meta_json  = excluded.meta_json,
            scraped_at = excluded.scraped_at
    """, {
        "id":         info["id"],
        "kind":       info["kind"],
        "title_orig": info.get("title_orig") or info.get("title_ru") or "Unknown",
        "title_ru":   info.get("title_ru"),
        "year":       str(info.get("year", "")) or None,
        "url":        info["url"],
        "poster_url": info.get("poster_url"),
        "meta_json":  json.dumps(info, ensure_ascii=False),
        "scraped_at": _now(),
    })
    conn.commit()


def db_get_item(conn: sqlite3.Connection, item_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


# ── Tasks ──────────────────────────────────────────────────────────────────────

def db_insert_task(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    kind: str,
    season: int,
    episode: int,
    episode_title: str | None,
    media_id: str | None,
    plex_stem: str | None,
) -> int | None:
    """Insert a task row. Returns new id, or None if it already existed."""
    cur = conn.execute("""
        INSERT OR IGNORE INTO tasks
            (item_id, kind, season, episode, episode_title, media_id, plex_stem, enqueued_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_id, kind, season, episode, episode_title, media_id, plex_stem, _now()))
    conn.commit()
    if cur.lastrowid and cur.rowcount > 0:
        return cur.lastrowid
    return None  # None signals "already existed, not newly created"


def db_claim_next_task(conn: sqlite3.Connection) -> dict | None:
    """Atomically claim the next pending task. Returns task dict or None."""
    conn.execute("BEGIN EXCLUSIVE")
    row = conn.execute("""
        SELECT * FROM tasks WHERE status='pending' ORDER BY id LIMIT 1
    """).fetchone()
    if not row:
        conn.execute("COMMIT")
        return None
    task_id = row["id"]
    conn.execute("""
        UPDATE tasks SET status='active', started_at=? WHERE id=?
    """, (_now(), task_id))
    conn.execute("COMMIT")
    # Re-fetch to return the updated row
    updated = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(updated)


def db_set_task_status(
    conn: sqlite3.Connection,
    task_id: int,
    status: str,
    *,
    error: str | None = None,
    mkv_path: str | None = None,
) -> None:
    # Terminal states all stamp completed_at so the task row alone tells the story
    # of when it most recently finished, without the caller needing to consult logs.
    completed_at = _now() if status in ("done", "failed", "skipped") else None
    conn.execute("""
        UPDATE tasks SET status=:status, last_error=:last_error,
            completed_at=COALESCE(:completed_at, completed_at),
            mkv_path=COALESCE(:mkv_path, mkv_path)
        WHERE id=:id
    """, {
        "status": status,
        "last_error": error,
        "completed_at": completed_at,
        "mkv_path": mkv_path,
        "id": task_id,
    })
    conn.commit()


def db_increment_attempts(conn: sqlite3.Connection, task_id: int) -> int:
    """Increment task attempts and set status back to pending. Returns new attempt count."""
    # Retry resets started_at and completed_at so the next claim gives a fresh
    # "last time started" and the old completion timestamp doesn't linger.
    conn.execute("""
        UPDATE tasks
        SET status='pending',
            attempts=attempts+1,
            started_at=NULL,
            completed_at=NULL,
            last_error=NULL
        WHERE id=?
    """, (task_id,))
    conn.commit()
    row = conn.execute("SELECT attempts FROM tasks WHERE id=?", (task_id,)).fetchone()
    return row["attempts"] if row else 0


def db_list_tasks(
    conn: sqlite3.Connection,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    *,
    kind: str | None = None,
    enqueued_after: str | None = None,
    enqueued_before: str | None = None,
    completed_after: str | None = None,
    include_unfinished: bool = False,
) -> list[dict]:
    clauses: list[str] = []
    params: dict = {}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if kind:
        clauses.append("kind = :kind")
        params["kind"] = kind

    time_clauses: list[str] = []
    if enqueued_after:
        time_clauses.append("enqueued_at >= :enqueued_after")
        params["enqueued_after"] = enqueued_after
    if enqueued_before:
        time_clauses.append("enqueued_at < :enqueued_before")
        params["enqueued_before"] = enqueued_before
    if completed_after:
        time_clauses.append("completed_at >= :completed_after")
        params["completed_after"] = completed_after

    if time_clauses:
        # `include_unfinished`: always keep pending/active/failed in the result so a
        # time-windowed web UI view never hides an in-flight download just because it
        # was enqueued outside the window.
        if include_unfinished:
            combined = (
                "((" + " AND ".join(time_clauses) + ")"
                " OR status IN ('pending','active','failed'))"
            )
            clauses.append(combined)
        else:
            clauses.extend(time_clauses)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = limit
    params["offset"] = offset
    rows = conn.execute(
        f"SELECT * FROM tasks{where} ORDER BY id DESC LIMIT :limit OFFSET :offset",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def db_get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def db_count_tasks(conn: sqlite3.Connection, status: str | None = None) -> int:
    if status:
        row = conn.execute("SELECT COUNT(*) as n FROM tasks WHERE status=?", (status,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) as n FROM tasks").fetchone()
    return row["n"] if row else 0


# ── Streams ────────────────────────────────────────────────────────────────────

def db_upsert_stream(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    stream_type: str,
    label: str | None,
    lang: str | None,
    forced: bool = False,
    source_url: str | None,
    tmp_path: str | None = None,
    out_path: str | None = None,
) -> int:
    """Insert stream if not exists (keyed on task_id + stream_type + label). Returns id."""
    cur = conn.execute("""
        INSERT OR IGNORE INTO streams
            (task_id, stream_type, label, lang, forced, source_url, resolved_at, tmp_path, out_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (task_id, stream_type, label, lang, int(forced), source_url, _now(), tmp_path, out_path))
    conn.commit()
    if cur.lastrowid and cur.rowcount > 0:
        return cur.lastrowid
    # Already existed
    row = conn.execute(
        "SELECT id FROM streams WHERE task_id=? AND stream_type=? AND label IS ?",
        (task_id, stream_type, label)
    ).fetchone()
    return row["id"] if row else cur.lastrowid


def db_update_stream(
    conn: sqlite3.Connection,
    stream_id: int,
    *,
    status: str | None = None,
    progress_pct: float | None = None,
    size_bytes: int | None = None,
    source_url: str | None = None,
    tmp_path: str | None = None,
    out_path: str | None = None,
    error: str | None = None,
) -> None:
    fields = []
    params: dict = {"id": stream_id}
    if status is not None:
        fields.append("status=:status")
        params["status"] = status
        if status == "downloading":
            fields.append("started_at=:started_at")
            params["started_at"] = _now()
        elif status in ("done", "failed"):
            fields.append("completed_at=:completed_at")
            params["completed_at"] = _now()
    if progress_pct is not None:
        fields.append("progress_pct=:progress_pct")
        params["progress_pct"] = progress_pct
    if size_bytes is not None:
        fields.append("size_bytes=:size_bytes")
        params["size_bytes"] = size_bytes
    if source_url is not None:
        fields.append("source_url=:source_url, resolved_at=:resolved_at")
        params["source_url"] = source_url
        params["resolved_at"] = _now()
    if tmp_path is not None:
        fields.append("tmp_path=:tmp_path")
        params["tmp_path"] = tmp_path
    if out_path is not None:
        fields.append("out_path=:out_path")
        params["out_path"] = out_path
    if error is not None:
        fields.append("last_error=:error")
        params["error"] = error
    if not fields:
        return
    conn.execute(f"UPDATE streams SET {', '.join(fields)} WHERE id=:id", params)
    conn.commit()


def db_get_streams(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM streams WHERE task_id=? ORDER BY stream_type, id",
        (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def db_get_stream(conn: sqlite3.Connection, stream_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM streams WHERE id=?", (stream_id,)).fetchone()
    return dict(row) if row else None


# ── Logs ───────────────────────────────────────────────────────────────────────

def db_log(conn: sqlite3.Connection, level: str, msg: str, task_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO logs (task_id, ts, level, msg) VALUES (?, ?, ?, ?)",
        (task_id, _now(), level, msg)
    )
    conn.commit()


def db_get_logs(
    conn: sqlite3.Connection,
    task_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    if task_id is not None:
        rows = conn.execute(
            "SELECT * FROM logs WHERE task_id=? ORDER BY id DESC LIMIT ?",
            (task_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── KV store ───────────────────────────────────────────────────────────────────

def db_kv_get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def db_kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def db_is_paused(conn: sqlite3.Connection) -> bool:
    return db_kv_get(conn, "paused", "false") == "true"


def db_set_paused(conn: sqlite3.Connection, paused: bool) -> None:
    db_kv_set(conn, "paused", "true" if paused else "false")


def db_is_cookie_error(conn: sqlite3.Connection) -> bool:
    return db_kv_get(conn, "cookie_error", "false") == "true"


def db_set_cookie_error(conn: sqlite3.Connection, error: bool) -> None:
    db_kv_set(conn, "cookie_error", "true" if error else "false")


# ── Queue status summary ───────────────────────────────────────────────────────

def db_queue_summary(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("""
        SELECT status, COUNT(*) as n FROM tasks GROUP BY status
    """).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    return {
        "pending":  counts.get("pending",  0),
        "active":   counts.get("active",   0),
        "done":     counts.get("done",     0),
        "failed":   counts.get("failed",   0),
        "skipped":  counts.get("skipped",  0),
    }
