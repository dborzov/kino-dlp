"""Tests for scrap_pub.daemon.db — schema, CRUD, and atomic task claim."""

import pytest

from scrap_pub.daemon.db import (
    db_claim_next_task,
    db_get_logs,
    db_get_task,
    db_increment_attempts,
    db_insert_task,
    db_is_cookie_error,
    db_is_paused,
    db_list_tasks,
    db_log,
    db_queue_summary,
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
