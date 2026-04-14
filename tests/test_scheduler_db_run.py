"""
Tests for db_run and net_run — specifically that kwargs are forwarded correctly
through run_in_executor (the critical functools.partial fix).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scrap_pub.daemon.config import Config
from scrap_pub.daemon.db import (
    db_claim_next_task,
    db_insert_task,
    db_set_task_status,
    db_update_stream,
    db_upsert_item,
    db_upsert_stream,
    open_db,
)
from scrap_pub.daemon.scheduler import AppState, db_run, net_run


@pytest.fixture
async def state(tmp_path):
    conn = open_db(tmp_path / "test.db")
    loop = asyncio.get_event_loop()
    cfg = Config(
        output_dir=str(tmp_path / "output"),
        tmp_dir=str(tmp_path / "tmp"),
        db_path=str(tmp_path / "test.db"),
    )
    st = AppState(
        config=cfg,
        conn=conn,
        loop=loop,
        db_executor=ThreadPoolExecutor(max_workers=1),
        net_executor=ThreadPoolExecutor(max_workers=1),
        work_queue=asyncio.Queue(maxsize=2),
        progress_queue=asyncio.Queue(),
        pause_event=asyncio.Event(),
        shutdown_event=asyncio.Event(),
    )
    st.pause_event.set()
    yield st
    st.db_executor.shutdown(wait=False)
    st.net_executor.shutdown(wait=False)
    conn.close()


def _seed(conn, item_id="1"):
    db_upsert_item(conn, {
        "id": item_id, "kind": "movie",
        "title_orig": "Test", "title_ru": None, "year": 2026,
        "url": f"https://example.com/item/view/{item_id}",
        "poster_url": None, "meta_json": "{}",
    })
    return db_insert_task(conn,
        item_id=item_id, kind="movie", season=0, episode=1,
        episode_title=None, media_id="m1", plex_stem="Test(2026)/Test(2026)",
    )


# ── db_run positional args (baseline) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_run_positional_args(state):
    """db_run works with purely positional arguments (baseline)."""
    tid = _seed(state.conn)
    result = await db_run(state, db_set_task_status, state.conn, tid, "done")
    row = state.conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "done"


# ── db_run keyword args ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_run_kwargs_set_task_status_with_mkv(state):
    """db_set_task_status with mkv_path kwarg is forwarded correctly."""
    tid = _seed(state.conn)
    await db_run(state, db_set_task_status, state.conn, tid, "done",
                 mkv_path="/output/Test.mkv")
    row = state.conn.execute("SELECT status, mkv_path FROM tasks WHERE id=?", (tid,)).fetchone()
    assert row["status"] == "done"
    assert row["mkv_path"] == "/output/Test.mkv"


@pytest.mark.asyncio
async def test_db_run_kwargs_set_task_status_with_error(state):
    """db_set_task_status with error kwarg is forwarded correctly."""
    tid = _seed(state.conn)
    await db_run(state, db_set_task_status, state.conn, tid, "failed",
                 error="manifest fetch failed")
    row = state.conn.execute(
        "SELECT status, last_error FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["last_error"] == "manifest fetch failed"


@pytest.mark.asyncio
async def test_db_run_kwargs_upsert_stream_all_kwargs(state):
    """db_upsert_stream with all-kwargs call (as used in downloader) is forwarded."""
    tid = _seed(state.conn)
    sid = await db_run(state, db_upsert_stream, state.conn,
                       task_id=tid,
                       stream_type="video",
                       label="Video 720x480",
                       lang=None,
                       forced=False,
                       source_url="https://cdn/video.m3u8",
                       tmp_path="/tmp/video.mkv")
    assert isinstance(sid, int)
    row = state.conn.execute("SELECT * FROM streams WHERE id=?", (sid,)).fetchone()
    assert row["stream_type"] == "video"
    assert row["tmp_path"] == "/tmp/video.mkv"


@pytest.mark.asyncio
async def test_db_run_kwargs_update_stream_status_only(state):
    """db_update_stream with single status kwarg (the 'downloading' marker)."""
    tid = _seed(state.conn)
    sid = db_upsert_stream(state.conn,
                           task_id=tid, stream_type="audio",
                           label="RUS", lang="rus", forced=False,
                           source_url="https://cdn/aud.m3u8",
                           tmp_path="/tmp/aud.m4a")
    await db_run(state, db_update_stream, state.conn, sid, status="downloading")
    row = state.conn.execute("SELECT status FROM streams WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "downloading"


@pytest.mark.asyncio
async def test_db_run_kwargs_update_stream_done_with_size(state):
    """db_update_stream with status+size_bytes (the 'done' marker)."""
    tid = _seed(state.conn)
    sid = db_upsert_stream(state.conn,
                           task_id=tid, stream_type="subtitle",
                           label="eng", lang="eng", forced=False,
                           source_url="https://cdn/sub.vtt",
                           out_path="/output/Test.eng.srt")
    await db_run(state, db_update_stream, state.conn, sid,
                 status="done", size_bytes=12345)
    row = state.conn.execute(
        "SELECT status, size_bytes FROM streams WHERE id=?", (sid,)
    ).fetchone()
    assert row["status"] == "done"
    assert row["size_bytes"] == 12345


@pytest.mark.asyncio
async def test_db_run_kwargs_update_stream_failed_with_error(state):
    """db_update_stream with status=failed and error kwarg."""
    tid = _seed(state.conn)
    sid = db_upsert_stream(state.conn,
                           task_id=tid, stream_type="video",
                           label=None, lang=None, forced=False,
                           source_url="https://cdn/v.m3u8",
                           tmp_path="/tmp/v.mkv")
    await db_run(state, db_update_stream, state.conn, sid,
                 status="failed", error="stall after 300s")
    row = state.conn.execute(
        "SELECT status, last_error FROM streams WHERE id=?", (sid,)
    ).fetchone()
    assert row["status"] == "failed"
    assert "stall" in row["last_error"]


# ── net_run ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_net_run_simple_function(state):
    """net_run executes a sync function in the net executor."""
    def double(x):
        return x * 2

    result = await net_run(state, double, 21)
    assert result == 42


@pytest.mark.asyncio
async def test_db_run_mixed_positional_and_kwargs(state):
    """Mixing positional and keyword args works correctly end-to-end."""
    tid = _seed(state.conn)
    # status is positional, mkv_path is kwarg
    await db_run(state, db_set_task_status, state.conn, tid, "done",
                 mkv_path="/path/to/output.mkv")
    row = state.conn.execute(
        "SELECT status, mkv_path FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    assert row["status"] == "done"
    assert row["mkv_path"] == "/path/to/output.mkv"


# ── output_dir persistence ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_output_dir_survives_reclaim(state, tmp_path):
    """A task enqueued with a custom output_dir keeps it across a daemon restart.

    Simulates the daemon-restart path: insert with output_dir, close conn,
    re-open the same DB file (as open_db would on boot), then db_claim_next_task
    and assert output_dir is still attached to the row. This is what the
    worker sees when it picks up a task that was queued before the daemon
    was restarted.
    """
    custom = str(tmp_path / "plex" / "TV Shows")
    db_upsert_item(state.conn, {
        "id": "restart-1", "kind": "movie",
        "title_orig": "Test", "title_ru": None, "year": 2026,
        "url": "https://example.com/item/view/restart-1",
        "poster_url": None, "meta_json": "{}",
    })
    tid = db_insert_task(state.conn,
        item_id="restart-1", kind="movie", season=0, episode=1,
        episode_title=None, media_id="m1",
        plex_stem="Test(2026)/Test(2026)",
        output_dir=custom,
    )
    assert tid is not None

    # Simulate daemon restart: close + reopen on the same DB file.
    db_path = Path(state.config.db_path)
    state.conn.close()
    state.conn = open_db(db_path)

    claimed = db_claim_next_task(state.conn)
    assert claimed is not None
    assert claimed["id"] == tid
    assert claimed["output_dir"] == custom
