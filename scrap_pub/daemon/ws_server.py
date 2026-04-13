"""
ws_server.py — WebSocket server: connection registry, command dispatch, broadcast.

serve_ws(state)      — asyncio task: starts the WebSocket server
broadcast(state, msg) — send a JSON message to all connected clients
"""

import asyncio
import json

import websockets
import websockets.asyncio.server

from .ws_protocol import (
    CMD_ADD_AUDIO,
    CMD_ADD_SUB,
    CMD_CONFIG_GET,
    CMD_CONFIG_SET,
    CMD_COOKIES,
    CMD_ENQUEUE,
    CMD_LIST,
    CMD_LOGS,
    CMD_PAUSE,
    CMD_RESUME,
    CMD_RETRY,
    CMD_SKIP,
    CMD_STATUS,
    EVT_DAEMON_STATUS,
    EVT_LOG,
    decode,
    encode,
    reply_err,
    reply_ok,
)


async def broadcast(state, msg: dict) -> None:
    """Send a JSON message to all connected WebSocket clients."""
    if not state.ws_clients:
        return
    data = encode(msg)
    await asyncio.gather(
        *(c.send(data) for c in list(state.ws_clients)),
        return_exceptions=True,
    )
    # Silently ignore send errors (client disconnected)


async def _send_initial_state(websocket, state) -> None:
    """Send current daemon state to a newly connected client."""
    from .db import (
        db_get_logs,
        db_is_cookie_error,
        db_is_paused,
        db_queue_summary,
    )
    from .scheduler import db_run

    # Daemon status
    summary      = await db_run(state, db_queue_summary,    state.conn)
    paused       = await db_run(state, db_is_paused,        state.conn)
    cookie_error = await db_run(state, db_is_cookie_error,  state.conn)

    await websocket.send(encode({
        "type":           EVT_DAEMON_STATUS,
        "paused":         paused,
        "active_workers": state.worker_count,
        "queue_depth":    summary["pending"],
        "cookie_ok":      not cookie_error,
        "counts":         summary,
    }))

    # Recent logs
    logs = await db_run(state, db_get_logs, state.conn, None, 50)
    for entry in logs:
        await websocket.send(encode({
            "type":    EVT_LOG,
            "task_id": entry.get("task_id"),
            "ts":      entry["ts"],
            "level":   entry["level"],
            "msg":     entry["msg"],
        }))


async def ws_handler(websocket, state) -> None:
    """Handle a single WebSocket connection."""
    state.ws_clients.add(websocket)
    try:
        await _send_initial_state(websocket, state)
        async for raw in websocket:
            try:
                msg = decode(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                await websocket.send(encode(reply_err("?", f"invalid JSON: {e}")))
                continue
            cmd = msg.get("cmd", "")
            try:
                reply = await dispatch(msg, state)
            except Exception as e:
                reply = reply_err(cmd, str(e))
            await websocket.send(encode(reply))
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        state.ws_clients.discard(websocket)


async def dispatch(msg: dict, state) -> dict:
    """Route a client command to the appropriate handler."""
    from .db import (
        db_get_logs,
        db_get_task,
        db_is_cookie_error,
        db_is_paused,
        db_list_tasks,
        db_queue_summary,
        db_set_cookie_error,
        db_set_paused,
        db_set_task_status,
    )
    from .scheduler import db_run
    from .session import REQUIRED_COOKIE_KEYS, write_cookies_file

    cmd = msg.get("cmd", "")

    # ── status ─────────────────────────────────────────────────────────────────
    if cmd == CMD_STATUS:
        summary      = await db_run(state, db_queue_summary,   state.conn)
        paused       = await db_run(state, db_is_paused,       state.conn)
        cookie_error = await db_run(state, db_is_cookie_error, state.conn)
        return reply_ok(cmd,
            paused         = paused,
            active_workers = state.worker_count,
            queue_depth    = summary["pending"],
            cookie_ok      = not cookie_error,
            counts         = summary,
            config         = state.config.to_dict(),
        )

    # ── list ──────────────────────────────────────────────────────────────────
    elif cmd == CMD_LIST:
        status = msg.get("status")
        limit  = int(msg.get("limit", 50))
        offset = int(msg.get("offset", 0))
        tasks  = await db_run(state, db_list_tasks, state.conn, status, limit, offset)
        return reply_ok(cmd, tasks=tasks)

    # ── logs ──────────────────────────────────────────────────────────────────
    elif cmd == CMD_LOGS:
        task_id = msg.get("task_id")
        limit   = int(msg.get("limit", 100))
        logs    = await db_run(state, db_get_logs, state.conn, task_id, limit)
        return reply_ok(cmd, logs=logs)

    # ── enqueue ───────────────────────────────────────────────────────────────
    elif cmd == CMD_ENQUEUE:
        url = msg.get("url", "").strip()
        if not url:
            return reply_err(cmd, "missing 'url'")
        try:
            task_ids = await _enqueue_url(url, state)
            return reply_ok(cmd, enqueued=len(task_ids), task_ids=task_ids)
        except Exception as e:
            return reply_err(cmd, str(e))

    # ── retry ─────────────────────────────────────────────────────────────────
    elif cmd == CMD_RETRY:
        task_id = msg.get("task_id")
        if not task_id:
            return reply_err(cmd, "missing 'task_id'")
        task = await db_run(state, db_get_task, state.conn, int(task_id))
        if not task:
            return reply_err(cmd, f"task {task_id} not found")
        state.conn.execute(
            "UPDATE tasks SET status='pending', attempts=0, last_error=NULL, "
            "started_at=NULL, completed_at=NULL WHERE id=?",
            (int(task_id),)
        )
        state.conn.commit()
        return reply_ok(cmd, task_id=int(task_id))

    # ── skip ──────────────────────────────────────────────────────────────────
    elif cmd == CMD_SKIP:
        task_id = msg.get("task_id")
        if not task_id:
            return reply_err(cmd, "missing 'task_id'")
        await db_run(state, db_set_task_status, state.conn, int(task_id), "skipped")
        return reply_ok(cmd, task_id=int(task_id))

    # ── pause ─────────────────────────────────────────────────────────────────
    elif cmd == CMD_PAUSE:
        state.pause_event.clear()
        await db_run(state, db_set_paused, state.conn, True)
        return reply_ok(cmd)

    # ── resume ────────────────────────────────────────────────────────────────
    elif cmd == CMD_RESUME:
        await db_run(state, db_set_paused, state.conn, False)
        await db_run(state, db_set_cookie_error, state.conn, False)
        state.pause_event.set()
        return reply_ok(cmd)

    # ── cookies ───────────────────────────────────────────────────────────────
    elif cmd == CMD_COOKIES:
        raw = msg.get("cookies_txt")
        if not isinstance(raw, str) or not raw.strip():
            return reply_err(
                cmd,
                "'cookies_txt' must be the contents of a Netscape cookies.txt file. "
                f"Required cookies: {sorted(REQUIRED_COOKIE_KEYS)}",
            )
        try:
            cookies = write_cookies_file(state.config.cookies_path, raw)
        except ValueError as e:
            return reply_err(cmd, str(e))
        await db_run(state, db_set_cookie_error, state.conn, False)
        await db_run(state, db_set_paused, state.conn, False)
        state.pause_event.set()
        await broadcast(state, {
            "type": EVT_DAEMON_STATUS,
            "cookie_ok": True,
            "paused": False,
            "active_workers": state.worker_count,
        })
        return reply_ok(cmd, count=len(cookies))

    # ── config_get ────────────────────────────────────────────────────────────
    elif cmd == CMD_CONFIG_GET:
        return reply_ok(cmd, config=state.config.to_dict())

    # ── config_set ────────────────────────────────────────────────────────────
    elif cmd == CMD_CONFIG_SET:
        key   = msg.get("key")
        value = msg.get("value")
        if not key:
            return reply_err(cmd, "missing 'key'")
        try:
            state.config.update(key, value)
        except KeyError as e:
            return reply_err(cmd, str(e))
        return reply_ok(cmd, key=key, value=getattr(state.config, key))

    # ── add_audio ─────────────────────────────────────────────────────────────
    elif cmd == CMD_ADD_AUDIO:
        task_id = msg.get("task_id")
        url     = msg.get("url", "").strip()
        label   = msg.get("label")
        if not task_id or not url:
            return reply_err(cmd, "missing 'task_id' or 'url'")
        from .downloader import add_audio_to_task
        stream_id = await add_audio_to_task(int(task_id), url, label, state)
        return reply_ok(cmd, stream_id=stream_id)

    # ── add_sub ───────────────────────────────────────────────────────────────
    elif cmd == CMD_ADD_SUB:
        task_id = msg.get("task_id")
        url     = msg.get("url", "").strip()
        lang    = msg.get("lang")
        if not task_id or not url:
            return reply_err(cmd, "missing 'task_id' or 'url'")
        from .downloader import add_sub_to_task
        stream_id = await add_sub_to_task(int(task_id), url, lang, state)
        return reply_ok(cmd, stream_id=stream_id)

    else:
        return reply_err(cmd or "?", f"unknown command: {cmd!r}")


async def _enqueue_url(url: str, state) -> list[int]:
    """
    Scrape an item URL on the configured target site and create task rows.
    Returns list of newly created task IDs.
    Runs the synchronous scraper in net_executor.
    """
    import re

    from .db import db_insert_task, db_upsert_item
    from .scheduler import db_run, net_run
    from .scraper import _dir_name, _episode_stem, canonical_title, normalise_url, scaffold, scrape

    url_full, item_id = normalise_url(url)

    # Detect requested episode/season
    se = re.search(r'/s(\d+)e(\d+)$', url_full)
    req_season  = int(se.group(1)) if se else None
    req_episode = int(se.group(2)) if se else None

    # Scrape (slow — runs in net executor)
    info = await net_run(state, scrape, url_full)

    # Store item
    await db_run(state, db_upsert_item, state.conn, info)

    # Scaffold dirs (poster, thumbnails, info.json)
    await net_run(state, scaffold, info, state.config.output_dir)

    title  = canonical_title(info)
    year   = info.get("year")
    task_ids: list[int] = []

    if info["kind"] == "movie":
        me = info.get("movie_entry", {})
        stem_name = _dir_name(title, year)
        plex_stem = f"{stem_name}/{stem_name}"
        tid = await db_run(state, db_insert_task, state.conn,
            item_id=item_id, kind="movie",
            season=me.get("season", 0), episode=me.get("episode", 1),
            episode_title=None, media_id=me.get("media_id"),
            plex_stem=plex_stem,
        )
        if tid:
            task_ids.append(tid)
    else:
        show_dir = _dir_name(title, year)
        for season_num, sd in info.get("seasons_data", {}).items():
            if req_season is not None and season_num != req_season:
                continue
            for ep in sd["episodes"]:
                if req_season is not None and req_episode is not None:
                    if ep["episode"] != req_episode:
                        continue
                stem_leaf = _episode_stem(show_dir, ep["season"], ep["episode"], ep.get("title"))
                plex_stem = f"{show_dir}/Season {season_num:02d}/{stem_leaf}"
                tid = await db_run(state, db_insert_task, state.conn,
                    item_id=item_id, kind="episode",
                    season=ep["season"], episode=ep["episode"],
                    episode_title=ep.get("title"),
                    media_id=ep.get("media_id"),
                    plex_stem=plex_stem,
                )
                if tid:
                    task_ids.append(tid)

    return task_ids


async def serve_ws(state) -> None:
    """Start the WebSocket server (runs until shutdown_event is set)."""
    config = state.config

    async def _handler(websocket):
        await ws_handler(websocket, state)

    async with websockets.asyncio.server.serve(
        _handler,
        "localhost",
        config.ws_port,
        ping_interval=30,
        ping_timeout=10,
    ):
        print(f"[ws] Listening on ws://localhost:{config.ws_port}")
        await state.shutdown_event.wait()
