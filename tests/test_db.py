"""Tests for scrap_pub.daemon.db — schema, CRUD, and atomic task claim."""

import sqlite3

import pytest

from scrap_pub.daemon.db import (
    db_claim_next_task,
    db_get_logs,
    db_get_output_dir_history,
    db_get_task,
    db_increment_attempts,
    db_insert_task,
    db_is_cookie_error,
    db_is_paused,
    db_list_tasks,
    db_log,
    db_queue_summary,
    db_record_output_dir_usage,
    db_set_cookie_error,
    db_set_paused,
    db_set_task_status,
    db_update_stream,
    db_upsert_item,
    db_upsert_stream,
    open_db,
)


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "test.db")
    yield c
    c.close()


def _item(item_id="123"):
    return {
        "id":         item_id,
        "kind":       "movie",
        "title_orig": "Test Movie",
        "title_ru":   "Тест",
        "year":       2026,
        "url":        f"https://example.com/item/view/{item_id}",
        "poster_url": None,
        "meta_json":  "{}",
    }


# ── schema ────────────────────────────────────────────────────────────────────


def test_tables_created(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"items", "tasks", "streams", "logs", "kv"} <= tables


def test_wal_mode(conn):
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


# ── items ─────────────────────────────────────────────────────────────────────


def test_upsert_item(conn):
    db_upsert_item(conn, _item("42"))
    row = conn.execute("SELECT id, title_orig FROM items WHERE id='42'").fetchone()
    assert row is not None
    assert row[1] == "Test Movie"


def test_upsert_item_idempotent(conn):
    db_upsert_item(conn, _item("42"))
    db_upsert_item(conn, _item("42"))
    count = conn.execute("SELECT COUNT(*) FROM items WHERE id='42'").fetchone()[0]
    assert count == 1


# ── tasks ─────────────────────────────────────────────────────────────────────


def test_insert_task(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="Movie(2026)/Movie(2026)")
    assert tid is not None
    assert tid > 0


def test_insert_task_idempotent(conn):
    """Inserting the same (item_id, season, episode) twice returns None the second time."""
    db_upsert_item(conn, _item("1"))
    tid1 = db_insert_task(conn, item_id="1", kind="movie",
                          season=0, episode=1, episode_title=None,
                          media_id="m1", plex_stem="X/X")
    tid2 = db_insert_task(conn, item_id="1", kind="movie",
                          season=0, episode=1, episode_title=None,
                          media_id="m1", plex_stem="X/X")
    assert tid1 is not None
    assert tid2 is None


def test_insert_task_default_output_dir_is_null(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    task = db_get_task(conn, tid)
    assert task["output_dir"] is None


def test_insert_task_with_output_dir(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X",
                         output_dir="/mnt/plex/Movies")
    task = db_get_task(conn, tid)
    assert task["output_dir"] == "/mnt/plex/Movies"


def test_migration_adds_output_dir_to_legacy_schema(tmp_path):
    """A DB created without output_dir picks up the column when re-opened."""
    db_path = tmp_path / "legacy.db"
    # Create a pre-migration tasks table (no output_dir).
    raw = sqlite3.connect(str(db_path))
    raw.execute("""
        CREATE TABLE tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id         TEXT NOT NULL,
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
            mkv_path        TEXT
        )
    """)
    raw.execute(
        "INSERT INTO tasks (item_id, kind, enqueued_at, plex_stem) "
        "VALUES ('legacy', 'movie', '2020-01-01T00:00:00+00:00', 'Old/Old')"
    )
    raw.commit()
    raw.close()

    # Open via open_db() — should ALTER the table and preserve the legacy row.
    conn = open_db(db_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "output_dir" in cols

    row = conn.execute("SELECT item_id, output_dir FROM tasks").fetchone()
    assert row["item_id"] == "legacy"
    assert row["output_dir"] is None

    # Running the migration a second time is a no-op (no duplicate column error).
    conn.close()
    conn2 = open_db(db_path)
    assert {r["name"] for r in conn2.execute("PRAGMA table_info(tasks)")} >= {"output_dir"}
    conn2.close()


def test_claim_next_task(conn):
    db_upsert_item(conn, _item("1"))
    db_insert_task(conn, item_id="1", kind="movie",
                   season=0, episode=1, episode_title=None,
                   media_id="m1", plex_stem="X/X")
    task = db_claim_next_task(conn)
    assert task is not None
    assert task["status"] == "active"


def test_claim_returns_none_when_empty(conn):
    task = db_claim_next_task(conn)
    assert task is None


def test_claim_skips_non_pending(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    db_set_task_status(conn, tid, "done")
    task = db_claim_next_task(conn)
    assert task is None


def test_set_task_status(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    db_set_task_status(conn, tid, "done", mkv_path="/output/Movie.mkv")
    row = conn.execute("SELECT status, mkv_path FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row[0] == "done"
    assert row[1] == "/output/Movie.mkv"


def test_list_tasks(conn):
    db_upsert_item(conn, _item("1"))
    db_insert_task(conn, item_id="1", kind="movie",
                   season=0, episode=1, episode_title=None,
                   media_id="m1", plex_stem="X/X")
    tasks = db_list_tasks(conn, status=None, limit=10, offset=0)
    assert len(tasks) == 1


def test_get_task(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    task = db_get_task(conn, tid)
    assert task is not None
    assert task["id"] == tid


# ── streams ───────────────────────────────────────────────────────────────────


def test_upsert_stream(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    sid = db_upsert_stream(conn, task_id=tid, stream_type="video",
                           label=None, lang=None, forced=False,
                           source_url="https://cdn/video.m3u8",
                           tmp_path="/tmp/vid.mp4", out_path=None)
    assert sid > 0


def test_upsert_stream_idempotent(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    sid1 = db_upsert_stream(conn, task_id=tid, stream_type="audio",
                            label="RUS", lang="rus", forced=False,
                            source_url="https://cdn/audio.m3u8",
                            tmp_path="/tmp/aud.m4a", out_path=None)
    sid2 = db_upsert_stream(conn, task_id=tid, stream_type="audio",
                            label="RUS", lang="rus", forced=False,
                            source_url="https://cdn/audio.m3u8",
                            tmp_path="/tmp/aud.m4a", out_path=None)
    assert sid1 == sid2  # same row returned


def test_update_stream(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    sid = db_upsert_stream(conn, task_id=tid, stream_type="subtitle",
                           label="RUS", lang="rus", forced=False,
                           source_url="https://cdn/sub.vtt",
                           tmp_path=None, out_path="/output/Movie.rus.srt")
    db_update_stream(conn, sid, status="done", size_bytes=12345, progress_pct=100.0)
    row = conn.execute(
        "SELECT status, size_bytes, progress_pct FROM streams WHERE id=?", (sid,)
    ).fetchone()
    assert row[0] == "done"
    assert row[1] == 12345
    assert row[2] == 100.0


# ── logs ──────────────────────────────────────────────────────────────────────


def test_log_and_get_logs(conn):
    db_upsert_item(conn, _item("1"))
    tid = db_insert_task(conn, item_id="1", kind="movie",
                         season=0, episode=1, episode_title=None,
                         media_id="m1", plex_stem="X/X")
    db_log(conn, "INFO",  "Starting download", tid)
    db_log(conn, "WARN",  "Stall detected",    tid)
    db_log(conn, "ERROR", "Download failed",   tid)
    db_log(conn, "INFO",  "Global event",      None)

    task_logs   = db_get_logs(conn, task_id=tid, limit=100)
    global_logs = db_get_logs(conn, task_id=None, limit=100)

    assert len(task_logs) == 3
    assert len(global_logs) == 4  # all logs returned for task_id=None


def test_logs_limit(conn):
    for i in range(10):
        db_log(conn, "INFO", f"msg {i}", None)
    logs = db_get_logs(conn, task_id=None, limit=3)
    assert len(logs) == 3


# ── kv / pause / cookies ──────────────────────────────────────────────────────


def test_paused_default_false(conn):
    assert db_is_paused(conn) is False


def test_set_paused(conn):
    db_set_paused(conn, True)
    assert db_is_paused(conn) is True
    db_set_paused(conn, False)
    assert db_is_paused(conn) is False


def test_cookie_error_default_false(conn):
    assert db_is_cookie_error(conn) is False


def test_set_cookie_error(conn):
    db_set_cookie_error(conn, True)
    assert db_is_cookie_error(conn) is True


# ── queue summary ─────────────────────────────────────────────────────────────


def test_queue_summary_empty(conn):
    summary = db_queue_summary(conn)
    assert summary["pending"] == 0
    assert summary["active"]  == 0
    assert summary["done"]    == 0
    assert summary["failed"]  == 0


# ── list filters + timestamps ─────────────────────────────────────────────────


def _insert_named(conn, item_id: str, *, kind: str = "movie") -> int:
    db_upsert_item(conn, {**_item(item_id), "kind": kind})
    return db_insert_task(conn, item_id=item_id, kind=kind,
                          season=0 if kind == "movie" else 1,
                          episode=1,
                          episode_title=None, media_id=f"m{item_id}",
                          plex_stem=f"X{item_id}/X{item_id}")


def test_list_tasks_kind_filter(conn):
    _insert_named(conn, "1", kind="movie")
    _insert_named(conn, "2", kind="episode")
    _insert_named(conn, "3", kind="episode")
    movies = db_list_tasks(conn, kind="movie")
    episodes = db_list_tasks(conn, kind="episode")
    assert len(movies) == 1
    assert len(episodes) == 2
    assert all(t["kind"] == "episode" for t in episodes)


def test_list_tasks_since_filter(conn):
    tid = _insert_named(conn, "1")
    # Force this task's enqueued_at to the distant past.
    conn.execute("UPDATE tasks SET enqueued_at='2000-01-01T00:00:00+00:00' WHERE id=?", (tid,))
    conn.commit()
    _insert_named(conn, "2")  # "now"
    recent = db_list_tasks(conn, enqueued_after="2020-01-01T00:00:00+00:00")
    assert len(recent) == 1
    assert recent[0]["item_id"] == "2"


def test_list_tasks_include_unfinished_overrides_window(conn):
    # A pending task enqueued long ago should still appear when include_unfinished=True.
    tid = _insert_named(conn, "1")
    conn.execute("UPDATE tasks SET enqueued_at='2000-01-01T00:00:00+00:00' WHERE id=?", (tid,))
    conn.commit()
    rows = db_list_tasks(
        conn,
        enqueued_after="2020-01-01T00:00:00+00:00",
        include_unfinished=True,
    )
    assert any(t["id"] == tid for t in rows)


def test_failed_sets_completed_at(conn):
    tid = _insert_named(conn, "1")
    db_set_task_status(conn, tid, "failed", error="boom")
    task = db_get_task(conn, tid)
    assert task["status"] == "failed"
    assert task["completed_at"] is not None
    assert task["last_error"] == "boom"


def test_skipped_sets_completed_at(conn):
    tid = _insert_named(conn, "1")
    db_set_task_status(conn, tid, "skipped")
    task = db_get_task(conn, tid)
    assert task["completed_at"] is not None


def test_retry_clears_completed_at(conn):
    tid = _insert_named(conn, "1")
    db_set_task_status(conn, tid, "failed", error="boom")
    assert db_get_task(conn, tid)["completed_at"] is not None

    db_increment_attempts(conn, tid)
    task = db_get_task(conn, tid)
    assert task["status"] == "pending"
    assert task["completed_at"] is None
    assert task["started_at"] is None
    assert task["last_error"] is None
    assert task["attempts"] == 1


def test_retry_then_claim_sets_fresh_started_at(conn):
    tid = _insert_named(conn, "1")
    db_set_task_status(conn, tid, "failed", error="boom")
    db_increment_attempts(conn, tid)
    claimed = db_claim_next_task(conn)
    assert claimed is not None
    assert claimed["id"] == tid
    assert claimed["started_at"] is not None
    assert claimed["completed_at"] is None


def test_queue_summary_counts(conn):
    db_upsert_item(conn, _item("1"))
    db_upsert_item(conn, _item("2"))
    db_upsert_item(conn, _item("3"))

    db_insert_task(conn, item_id="1", kind="movie",
                   season=0, episode=1, episode_title=None,
                   media_id="m1", plex_stem="A/A")
    tid2 = db_insert_task(conn, item_id="2", kind="movie",
                          season=0, episode=1, episode_title=None,
                          media_id="m2", plex_stem="B/B")
    tid3 = db_insert_task(conn, item_id="3", kind="movie",
                          season=0, episode=1, episode_title=None,
                          media_id="m3", plex_stem="C/C")

    db_set_task_status(conn, tid2, "done")
    db_set_task_status(conn, tid3, "failed")

    summary = db_queue_summary(conn)
    assert summary["pending"] == 1
    assert summary["done"]    == 1
    assert summary["failed"]  == 1


# ── output_dir_history ────────────────────────────────────────────────────────


def test_output_dir_history_empty_initially(conn):
    assert db_get_output_dir_history(conn) == []


def test_output_dir_history_record_and_retrieve(conn):
    db_record_output_dir_usage(conn, "/mnt/plex/Movies")
    db_record_output_dir_usage(conn, "/mnt/plex/TV Shows")
    paths = db_get_output_dir_history(conn)
    assert "/mnt/plex/Movies" in paths
    assert "/mnt/plex/TV Shows" in paths


def test_output_dir_history_most_recent_first(conn):
    """The path used most recently must come first."""
    import time
    db_record_output_dir_usage(conn, "/mnt/first")
    time.sleep(0.01)
    db_record_output_dir_usage(conn, "/mnt/second")
    paths = db_get_output_dir_history(conn)
    assert paths[0] == "/mnt/second"
    assert paths[1] == "/mnt/first"


def test_output_dir_history_re_use_bubbles_to_top(conn):
    """Re-using an older path updates its timestamp and moves it to the front."""
    import time
    db_record_output_dir_usage(conn, "/mnt/first")
    time.sleep(0.01)
    db_record_output_dir_usage(conn, "/mnt/second")
    time.sleep(0.01)
    # Re-use the older path — it should now be first
    db_record_output_dir_usage(conn, "/mnt/first")
    paths = db_get_output_dir_history(conn)
    assert paths[0] == "/mnt/first"
    assert paths[1] == "/mnt/second"


def test_output_dir_history_no_duplicates(conn):
    """Recording the same path twice yields only one entry."""
    db_record_output_dir_usage(conn, "/mnt/plex")
    db_record_output_dir_usage(conn, "/mnt/plex")
    paths = db_get_output_dir_history(conn)
    assert paths.count("/mnt/plex") == 1


def test_output_dir_history_migration_seeds_from_tasks(tmp_path):
    """One-time migration seeds history from pre-existing task output_dir values."""
    import sqlite3

    # Build a DB manually with tasks that have output_dir but no history table yet.
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL,
            title_orig TEXT NOT NULL, title_ru TEXT, year TEXT,
            url TEXT NOT NULL, poster_url TEXT, meta_json TEXT, scraped_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL REFERENCES items(id),
            kind TEXT NOT NULL,
            season INTEGER NOT NULL DEFAULT 0,
            episode INTEGER NOT NULL DEFAULT 0,
            episode_title TEXT, media_id TEXT, plex_stem TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, enqueued_at TEXT NOT NULL,
            started_at TEXT, completed_at TEXT, mkv_path TEXT,
            output_dir TEXT,
            UNIQUE(item_id, season, episode)
        );
        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            stream_type TEXT NOT NULL, label TEXT, lang TEXT,
            forced INTEGER NOT NULL DEFAULT 0, source_url TEXT,
            resolved_at TEXT, tmp_path TEXT, out_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            progress_pct REAL, size_bytes INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, started_at TEXT, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
            ts TEXT NOT NULL, level TEXT NOT NULL, msg TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO items VALUES ('i1','movie','Film A',NULL,'2024','http://x/1',NULL,'{}',NULL);
        INSERT INTO items VALUES ('i2','movie','Film B',NULL,'2024','http://x/2',NULL,'{}',NULL);
        INSERT INTO tasks (item_id,kind,season,episode,enqueued_at,output_dir)
            VALUES ('i1','movie',0,1,'2024-01-01T00:00:00+00:00','/mnt/movies');
        INSERT INTO tasks (item_id,kind,season,episode,enqueued_at,output_dir)
            VALUES ('i2','movie',0,1,'2024-06-01T00:00:00+00:00','/mnt/movies');
    """)
    conn.close()

    # open_db triggers _migrate which should seed output_dir_history
    conn2 = open_db(db_path)
    paths = db_get_output_dir_history(conn2)
    conn2.close()
    assert "/mnt/movies" in paths
