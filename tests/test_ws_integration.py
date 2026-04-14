"""
Integration tests for the WebSocket server + command dispatch.

We start a real in-process WS server using an in-memory SQLite DB, connect a
real WebSocket client, and exercise the full request/reply round-trip for all
stateless commands (status, list, logs, pause, resume, retry, skip, etc.).

Network-dependent commands (enqueue, add_audio, add_sub) are NOT tested here.
"""

import asyncio
import json

import pytest
import websockets.asyncio.client as wscli

from scrap_pub.daemon.config import Config
from scrap_pub.daemon.db import (
    db_insert_task,
    db_set_task_status,
    db_upsert_item,
    open_db,
)
from scrap_pub.daemon.scheduler import AppState
from scrap_pub.daemon.ws_protocol import CMD_LIST, CMD_LOGS, CMD_PAUSE, CMD_RESUME, CMD_STATUS

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    conn = open_db(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
async def state(db, tmp_path):
    """Minimal AppState with a real DB and isolated config (no global config pollution)."""
    import json
    from concurrent.futures import ThreadPoolExecutor

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "output_dir":   str(tmp_path / "output"),
        "tmp_dir":      str(tmp_path / "tmp"),
        "db_path":      str(tmp_path / "test.db"),
        "cookies_path": str(tmp_path / "cookies.txt"),
        "ws_port":      0,
    }))
    cfg = Config.load(cfg_file)

    loop = asyncio.get_event_loop()
    st = AppState(
        config         = cfg,
        conn           = db,
        loop           = loop,
        db_executor    = ThreadPoolExecutor(max_workers=1),
        net_executor   = ThreadPoolExecutor(max_workers=1),
        work_queue     = asyncio.Queue(maxsize=2),
        progress_queue = asyncio.Queue(),
        pause_event    = asyncio.Event(),
        shutdown_event = asyncio.Event(),
    )
    st.pause_event.set()  # start unpaused
    yield st
    st.db_executor.shutdown(wait=False)
    st.net_executor.shutdown(wait=False)


@pytest.fixture
async def ws_server(state):
    """Start a real WS server on a free port; yield (state, url); shutdown after test."""
    import websockets.asyncio.server as wsserver

    ready = asyncio.Event()
    port_holder = [None]

    async def _handler(ws):
        from scrap_pub.daemon.ws_server import ws_handler
        await ws_handler(ws, state)

    async def _serve():
        async with wsserver.serve(_handler, "localhost", 0) as server:
            port_holder[0] = server.sockets[0].getsockname()[1]
            ready.set()
            await state.shutdown_event.wait()

    task = asyncio.create_task(_serve())
    await ready.wait()
    url = f"ws://localhost:{port_holder[0]}"
    yield state, url
    state.shutdown_event.set()
    await task


# ── helpers ───────────────────────────────────────────────────────────────────


async def _cmd(url: str, cmd: dict) -> dict:
    """Send one command; wait for the matching reply."""
    async with wscli.connect(url) as ws:
        await ws.send(json.dumps(cmd))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "reply" and msg.get("cmd") == cmd.get("cmd"):
                return msg
    pytest.fail("connection closed before reply")


def _seed_task(db, item_id="1", kind="movie", season=0, episode=1, status="pending"):
    db_upsert_item(db, {
        "id": item_id, "kind": kind,
        "title_orig": "Test", "title_ru": None,
        "year": 2026, "url": f"https://example.com/item/view/{item_id}",
        "poster_url": None, "meta_json": "{}",
    })
    tid = db_insert_task(db,
        item_id=item_id, kind=kind,
        season=season, episode=episode,
        episode_title=None, media_id="m1",
        plex_stem="Test(2026)/Test(2026)",
    )
    if status != "pending" and tid:
        db_set_task_status(db, tid, status)
    return tid


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": CMD_STATUS})
    assert reply["ok"] is True
    assert "paused" in reply
    assert "active_workers" in reply
    assert "queue_depth" in reply
    assert "counts" in reply


@pytest.mark.asyncio
async def test_list_empty(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": CMD_LIST})
    assert reply["ok"] is True
    assert reply["tasks"] == []


@pytest.mark.asyncio
async def test_list_with_tasks(ws_server):
    state, url = ws_server
    _seed_task(state.conn, item_id="10")
    _seed_task(state.conn, item_id="11")
    reply = await _cmd(url, {"cmd": CMD_LIST})
    assert reply["ok"] is True
    assert len(reply["tasks"]) == 2


@pytest.mark.asyncio
async def test_list_filter_by_status(ws_server):
    state, url = ws_server
    _seed_task(state.conn, item_id="10", status="pending")
    _seed_task(state.conn, item_id="11", status="done")
    reply = await _cmd(url, {"cmd": CMD_LIST, "status": "done"})
    assert reply["ok"] is True
    assert len(reply["tasks"]) == 1
    assert reply["tasks"][0]["status"] == "done"


@pytest.mark.asyncio
async def test_logs_empty(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": CMD_LOGS})
    assert reply["ok"] is True
    assert isinstance(reply["logs"], list)


@pytest.mark.asyncio
async def test_pause_and_resume(ws_server):
    state, url = ws_server
    # Pause
    reply = await _cmd(url, {"cmd": CMD_PAUSE})
    assert reply["ok"] is True
    assert state.pause_event.is_set() is False

    # Resume
    reply = await _cmd(url, {"cmd": CMD_RESUME})
    assert reply["ok"] is True
    assert state.pause_event.is_set() is True


@pytest.mark.asyncio
async def test_retry_resets_task(ws_server):
    state, url = ws_server
    tid = _seed_task(state.conn, item_id="20", status="failed")
    reply = await _cmd(url, {"cmd": "retry", "task_id": tid})
    assert reply["ok"] is True
    row = state.conn.execute(
        "SELECT status, attempts FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    assert row[0] == "pending"
    assert row[1] == 0


@pytest.mark.asyncio
async def test_retry_unknown_task(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "retry", "task_id": 9999})
    assert reply["ok"] is False


@pytest.mark.asyncio
async def test_skip_task(ws_server):
    state, url = ws_server
    tid = _seed_task(state.conn, item_id="30")
    reply = await _cmd(url, {"cmd": "skip", "task_id": tid})
    assert reply["ok"] is True
    row = state.conn.execute(
        "SELECT status FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    assert row[0] == "skipped"


@pytest.mark.asyncio
async def test_config_get(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "config_get"})
    assert reply["ok"] is True
    assert "concurrency" in reply["config"]
    assert "output_dir" in reply["config"]


@pytest.mark.asyncio
async def test_config_set(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "config_set", "key": "concurrency", "value": 5})
    assert reply["ok"] is True
    assert state.config.concurrency == 5


@pytest.mark.asyncio
async def test_config_set_unknown_key(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "config_set", "key": "nonexistent", "value": 1})
    assert reply["ok"] is False


def _make_cookies_txt(**pairs: str) -> str:
    """Build a minimal valid Netscape cookies.txt body from name=value pairs."""
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in pairs.items():
        lines.append(f".example.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
    return "\n".join(lines) + "\n"


@pytest.mark.asyncio
async def test_cookies_valid(ws_server):
    state, url = ws_server
    raw = _make_cookies_txt(
        _identity="a",
        token="b",
        _csrf="c",
        PHPSESSID="d",
        cf_clearance="e",
    )
    reply = await _cmd(url, {"cmd": "cookies", "cookies_txt": raw})
    assert reply["ok"] is True
    assert reply["count"] == 5
    # File should have been written to the isolated cookies_path
    assert state.config.cookies_path.exists()


@pytest.mark.asyncio
async def test_cookies_missing_required(ws_server):
    state, url = ws_server
    raw = _make_cookies_txt(_identity="a", token="b")  # missing _csrf, PHPSESSID, cf_clearance
    reply = await _cmd(url, {"cmd": "cookies", "cookies_txt": raw})
    assert reply["ok"] is False
    assert "missing" in reply["error"].lower()


@pytest.mark.asyncio
async def test_cookies_not_string(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "cookies", "cookies_txt": ["not", "a", "string"]})
    assert reply["ok"] is False


@pytest.mark.asyncio
async def test_unknown_command(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "totally_unknown_command"})
    assert reply["ok"] is False


@pytest.mark.asyncio
async def test_enqueue_missing_url(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "enqueue", "url": ""})
    assert reply["ok"] is False
    assert "url" in reply["error"]


# ── CMD_GET ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_task_returns_task_and_streams(ws_server):
    from scrap_pub.daemon.db import db_upsert_stream

    state, url = ws_server
    tid = _seed_task(state.conn, item_id="80")
    db_upsert_stream(
        state.conn, task_id=tid, stream_type="video", label=None, lang=None,
        source_url="https://cdn/video.m3u8",
    )
    reply = await _cmd(url, {"cmd": "get", "task_id": tid})
    assert reply["ok"] is True
    assert reply["task"]["id"] == tid
    assert isinstance(reply["streams"], list)
    assert len(reply["streams"]) == 1
    assert reply["streams"][0]["stream_type"] == "video"


@pytest.mark.asyncio
async def test_get_task_not_found(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "get", "task_id": 99999})
    assert reply["ok"] is False


@pytest.mark.asyncio
async def test_get_task_overlays_live_progress(ws_server):
    from scrap_pub.daemon.db import db_upsert_stream

    state, url = ws_server
    tid = _seed_task(state.conn, item_id="81")
    sid = db_upsert_stream(
        state.conn, task_id=tid, stream_type="video", label=None, lang=None,
        source_url="https://cdn/video.m3u8",
    )
    state.stream_progress[sid] = {
        "pct": 42.0, "speed": 1.5, "eta_sec": 60,
        "elapsed_sec": 30, "size_bytes": 12345,
    }
    reply = await _cmd(url, {"cmd": "get", "task_id": tid})
    assert reply["ok"] is True
    s = reply["streams"][0]
    assert s["pct"] == 42.0
    assert s["eta_sec"] == 60
    assert s["size_bytes"] == 12345


# ── CMD_LIST extensions ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_verbose_has_streams_by_task(ws_server):
    from scrap_pub.daemon.db import db_upsert_stream

    state, url = ws_server
    tid = _seed_task(state.conn, item_id="90")
    db_upsert_stream(
        state.conn, task_id=tid, stream_type="video", label=None, lang=None,
        source_url="https://cdn/v.m3u8",
    )
    reply = await _cmd(url, {"cmd": "list", "verbose": True})
    assert reply["ok"] is True
    assert "streams_by_task" in reply
    # JSON dict keys become strings over the wire
    assert str(tid) in reply["streams_by_task"]


@pytest.mark.asyncio
async def test_list_has_output_size_bytes(ws_server):
    state, url = ws_server
    _seed_task(state.conn, item_id="91")
    reply = await _cmd(url, {"cmd": "list"})
    assert reply["ok"] is True
    assert all("output_size_bytes" in t for t in reply["tasks"])


@pytest.mark.asyncio
async def test_list_kind_filter(ws_server):
    state, url = ws_server
    _seed_task(state.conn, item_id="92", kind="movie", season=0, episode=1)
    _seed_task(state.conn, item_id="93", kind="episode", season=1, episode=1)
    reply = await _cmd(url, {"cmd": "list", "kind": "episode"})
    assert reply["ok"] is True
    assert len(reply["tasks"]) == 1
    assert reply["tasks"][0]["kind"] == "episode"


# ── CMD_SQL ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sql_select_ok(ws_server):
    state, url = ws_server
    _seed_task(state.conn, item_id="100")
    reply = await _cmd(url, {
        "cmd": "sql",
        "query": "SELECT id, status FROM tasks ORDER BY id",
    })
    assert reply["ok"] is True
    assert reply["columns"] == ["id", "status"]
    assert len(reply["rows"]) == 1
    assert reply["rows"][0][1] == "pending"


@pytest.mark.asyncio
async def test_sql_select_with_params(ws_server):
    state, url = ws_server
    tid = _seed_task(state.conn, item_id="101")
    reply = await _cmd(url, {
        "cmd": "sql",
        "query": "SELECT id FROM tasks WHERE id = ?",
        "params": [tid],
    })
    assert reply["ok"] is True
    assert reply["rows"] == [[tid]]


@pytest.mark.asyncio
async def test_sql_rejects_write_by_default(ws_server):
    state, url = ws_server
    _seed_task(state.conn, item_id="102")
    reply = await _cmd(url, {
        "cmd": "sql",
        "query": "DELETE FROM tasks",
    })
    assert reply["ok"] is False
    # Safety gate should have refused before execution.
    row = state.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_sql_rejects_drop_even_with_comment(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {
        "cmd": "sql",
        "query": "/* SELECT */ DROP TABLE tasks",
    })
    assert reply["ok"] is False


@pytest.mark.asyncio
async def test_sql_write_allowed_with_flag(ws_server):
    state, url = ws_server
    tid = _seed_task(state.conn, item_id="103")
    reply = await _cmd(url, {
        "cmd":   "sql",
        "query": "UPDATE tasks SET status='skipped' WHERE id=?",
        "params": [tid],
        "write": True,
    })
    assert reply["ok"] is True
    assert reply["rowcount"] == 1
    row = state.conn.execute(
        "SELECT status FROM tasks WHERE id=?", (tid,)
    ).fetchone()
    assert row[0] == "skipped"


@pytest.mark.asyncio
async def test_sql_row_cap_truncates(ws_server):
    state, url = ws_server
    for i in range(5):
        _seed_task(state.conn, item_id=f"2{i:02d}")
    reply = await _cmd(url, {
        "cmd":      "sql",
        "query":    "SELECT id FROM tasks",
        "max_rows": 2,
    })
    assert reply["ok"] is True
    assert len(reply["rows"]) == 2
    assert reply["truncated"] is True


@pytest.mark.asyncio
async def test_sql_rejects_empty_query(ws_server):
    state, url = ws_server
    reply = await _cmd(url, {"cmd": "sql", "query": "   "})
    assert reply["ok"] is False
