"""
ws_server.py — WebSocket server: connection registry, command dispatch, broadcast.

serve_ws(state)      — asyncio task: starts the WebSocket server
broadcast(state, msg) — send a JSON message to all connected clients
"""

import asyncio
import json
import logging
import re
from pathlib import Path

import websockets
import websockets.asyncio.server

log = logging.getLogger(__name__)

from .ws_protocol import (
    CMD_ADD_AUDIO,
    CMD_ADD_SUB,
    CMD_CONFIG_GET,
    CMD_CONFIG_SET,
    CMD_COOKIES,
    CMD_ENQUEUE,
    CMD_GET,
    CMD_LIST,
    CMD_LOGS,
    CMD_OUTPUT_DIR_HISTORY,
    CMD_PAUSE,
    CMD_RESUME,
    CMD_RETRY,
    CMD_SKIP,
    CMD_SQL,
    CMD_STATUS,
    EVT_DAEMON_STATUS,
    EVT_LOG,
    decode,
    encode,
    reply_err,
    reply_ok,
)

# SQL safety gate: these are the only leading keywords allowed when `write=false`.
# Comments are stripped before the first-token check.
_SQL_READONLY_PREFIXES = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
_SQL_MAX_ROWS_DEFAULT = 1000
_SQL_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


def _sql_is_readonly(query: str) -> bool:
    """Return True iff `query`'s first non-whitespace keyword is in the allow-list."""
    stripped = _SQL_COMMENT_RE.sub(" ", query).lstrip()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0].upper()
    return first in _SQL_READONLY_PREFIXES


def _compute_output_size(state, task: dict, streams: list[dict]) -> int:
    """Best-effort disk footprint for a task, in bytes.

    For `done` tasks the final MKV is ground truth — ephemeral per-stream files
    may have been cleaned up during remux. For everything else, sum the per-stream
    sizes, preferring the live in-memory ticker over the DB's last-settled value.
    """
    if task.get("status") == "done" and task.get("mkv_path"):
        try:
            p = Path(task["mkv_path"])
            if p.exists():
                return p.stat().st_size
        except OSError:
            pass
    total = 0
    for s in streams:
        sid = s.get("id")
        live = state.stream_progress.get(sid, {}).get("size_bytes") if sid is not None else None
        total += live if live is not None else (s.get("size_bytes") or 0)
    return total


def _overlay_stream_progress(state, stream: dict) -> dict:
    """Return a copy of `stream` with any live progress fields merged on top."""
    sid = stream.get("id")
    live = state.stream_progress.get(sid) if sid is not None else None
    if not live:
        return stream
    merged = dict(stream)
    merged.update(live)
    return merged


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
        db_get_streams,
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
        from .timespec import parse_since

        status          = msg.get("status")
        limit           = int(msg.get("limit", 50))
        offset          = int(msg.get("offset", 0))
        kind            = msg.get("kind")
        # Accept either a pre-parsed UTC ISO timestamp (CLI) or a human spec
        # like "today"/"week" (web UI). `parse_since` is a no-op on already
        # parsed ISO strings (it round-trips them through UTC).
        try:
            since           = parse_since(msg.get("since"))
            until           = parse_since(msg.get("until"))
            completed_since = parse_since(msg.get("completed_since"))
        except ValueError as e:
            return reply_err(cmd, str(e))
        include_unfinished = bool(msg.get("include_unfinished", False))
        verbose         = bool(msg.get("verbose", False))

        tasks = await db_run(
            state, db_list_tasks, state.conn, status, limit, offset,
            kind=kind,
            enqueued_after=since,
            enqueued_before=until,
            completed_after=completed_since,
            include_unfinished=include_unfinished,
        )

        # Attach per-task stream rollup so the UI and CLI can show output size
        # without a second round-trip. Verbose callers also get the stream list.
        streams_by_task: dict[int, list[dict]] = {}
        for t in tasks:
            streams = await db_run(state, db_get_streams, state.conn, t["id"])
            streams = [_overlay_stream_progress(state, s) for s in streams]
            t["output_size_bytes"] = _compute_output_size(state, t, streams)
            if verbose:
                streams_by_task[t["id"]] = streams

        reply_kwargs: dict = {"tasks": tasks}
        if verbose:
            reply_kwargs["streams_by_task"] = streams_by_task
        return reply_ok(cmd, **reply_kwargs)

    # ── get (single task + streams + output size) ─────────────────────────────
    elif cmd == CMD_GET:
        task_id = msg.get("task_id")
        if task_id is None:
            return reply_err(cmd, "missing 'task_id'")
        try:
            tid = int(task_id)
        except (TypeError, ValueError):
            return reply_err(cmd, f"task_id must be int (got {task_id!r})")
        task = await db_run(state, db_get_task, state.conn, tid)
        if not task:
            return reply_err(cmd, f"task {tid} not found")
        streams = await db_run(state, db_get_streams, state.conn, tid)
        streams = [_overlay_stream_progress(state, s) for s in streams]
        task["output_size_bytes"] = _compute_output_size(state, task, streams)
        return reply_ok(cmd, task=task, streams=streams)

    # ── sql (read-only by default) ────────────────────────────────────────────
    elif cmd == CMD_SQL:
        query = msg.get("query")
        if not isinstance(query, str) or not query.strip():
            return reply_err(cmd, "missing 'query'")
        params = msg.get("params") or []
        if not isinstance(params, (list, tuple)):
            return reply_err(cmd, "'params' must be a list")
        write = bool(msg.get("write", False))
        max_rows = int(msg.get("max_rows", _SQL_MAX_ROWS_DEFAULT))
        if max_rows < 1:
            max_rows = _SQL_MAX_ROWS_DEFAULT

        if not write and not _sql_is_readonly(query):
            return reply_err(
                cmd,
                "refusing to run a non-SELECT/WITH/PRAGMA/EXPLAIN statement without "
                "--write. Pass write=true (CLI: --write) to allow DML/DDL.",
            )

        def _run_sql(conn):
            cur = conn.execute(query, tuple(params))
            if cur.description is None:
                # No result set (INSERT/UPDATE/DELETE/DDL)
                conn.commit()
                return {
                    "columns": [],
                    "rows":    [],
                    "rowcount": cur.rowcount,
                    "truncated": False,
                }
            cols = [d[0] for d in cur.description]
            rows: list[list] = []
            truncated = False
            for i, r in enumerate(cur):
                if i >= max_rows:
                    truncated = True
                    break
                rows.append(list(r))
            return {
                "columns": cols,
                "rows":    rows,
                "rowcount": len(rows),
                "truncated": truncated,
            }

        try:
            result = await db_run(state, _run_sql, state.conn)
        except Exception as e:
            return reply_err(cmd, f"sqlite error: {e}")
        return reply_ok(cmd, **result)

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
        raw_output_dir = msg.get("output_dir")
        if isinstance(raw_output_dir, str):
            raw_output_dir = raw_output_dir.strip() or None
        elif raw_output_dir is not None:
            return reply_err(cmd, "'output_dir' must be a string")

        resolved_output_dir: str | None = None
        if raw_output_dir:
            from .config import OutputDirError, validate_task_output_dir
            try:
                resolved = validate_task_output_dir(
                    raw_output_dir, state.config.min_free_space_gb
                )
            except OutputDirError as e:
                return reply_err(cmd, str(e))
            resolved_output_dir = str(resolved)

        try:
            task_ids = await _enqueue_url(url, state, output_dir=resolved_output_dir)
            if resolved_output_dir:
                from .db import db_record_output_dir_usage
                await db_run(state, db_record_output_dir_usage, state.conn, resolved_output_dir)
            return reply_ok(cmd, enqueued=len(task_ids), task_ids=task_ids,
                            output_dir=resolved_output_dir)
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

    # ── output_dir_history ────────────────────────────────────────────────────
    elif cmd == CMD_OUTPUT_DIR_HISTORY:
        from .db import db_get_output_dir_history
        paths = await db_run(state, db_get_output_dir_history, state.conn)
        return reply_ok(cmd, paths=paths)

    else:
        return reply_err(cmd or "?", f"unknown command: {cmd!r}")


async def _enqueue_url(
    url: str,
    state,
    *,
    output_dir: str | None = None,
) -> list[int]:
    """
    Scrape an item URL on the configured target site and create task rows.
    Returns list of newly created task IDs.
    Runs the synchronous scraper in net_executor.

    If `output_dir` is provided it is used as the per-task output root in
    place of `state.config.output_dir` — including the scaffold() call that
    writes poster / thumbnail / metadata files before any task row exists.
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

    # Scrape (slow — runs in net executor). When the user named a specific
    # episode URL, restrict per-season walks to just that one season. A
    # non-zero season > 0 is the signal; `/s0e1` is the movie sentinel and
    # should fall through to a normal scrape.
    import functools
    only_season_for_scrape = req_season if req_season and req_season > 0 else None
    info = await net_run(
        state,
        functools.partial(scrape, url_full, only_season=only_season_for_scrape),
    )

    # Store item
    await db_run(state, db_upsert_item, state.conn, info)

    # Effective output root for scaffolding + every task row we're about to
    # create. Poster/thumbnail/metadata must land in the same place the final
    # MKV will, so the Plex library sees one coherent tree.
    effective_root = Path(output_dir).expanduser() if output_dir else Path(state.config.output_dir)

    # Guard against re-enqueue with a conflicting output_dir. INSERT OR IGNORE
    # would silently keep the original, so a Plex agent would have no idea
    # the new --output-dir was ignored.
    conflict = await db_run(state, _find_conflicting_task, state.conn,
                            item_id, req_season, req_episode, output_dir)
    if conflict is not None:
        raise ValueError(
            f"task {conflict['id']} already exists for this item with a different "
            f"output_dir ({conflict['output_dir'] or '<default>'}). "
            "Delete it first, then re-enqueue."
        )

    # Scaffold dirs (poster, thumbnails, info.json) into the EFFECTIVE root.
    # If a specific episode was requested, narrow per-episode file creation
    # to just that one — no point writing s02e08.info.json when the user only
    # enqueued s03e05.
    scaffold_only: tuple[int, int] | None = None
    if req_season is not None and req_episode is not None and req_season > 0:
        scaffold_only = (req_season, req_episode)
    await net_run(
        state,
        functools.partial(scaffold, info, effective_root, only=scaffold_only),
    )

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
            output_dir=output_dir,
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
                    output_dir=output_dir,
                )
                if tid:
                    task_ids.append(tid)

    return task_ids


def _find_conflicting_task(
    conn,
    item_id: str,
    req_season: int | None,
    req_episode: int | None,
    new_output_dir: str | None,
) -> dict | None:
    """Return the first existing task row whose output_dir differs from new_output_dir.

    Covers both movies (single row) and the general series case (all matching
    episodes). Returns None when no conflict exists — either no row, or all
    matching rows already carry the same output_dir so INSERT OR IGNORE is
    the correct no-op.
    """
    clauses = ["item_id = ?"]
    params: list = [item_id]
    if req_season is not None:
        clauses.append("season = ?")
        params.append(req_season)
    if req_episode is not None:
        clauses.append("episode = ?")
        params.append(req_episode)
    rows = conn.execute(
        f"SELECT id, output_dir FROM tasks WHERE {' AND '.join(clauses)}",
        params,
    ).fetchall()
    for r in rows:
        existing = r["output_dir"]
        if (existing or None) != (new_output_dir or None):
            return {"id": r["id"], "output_dir": existing}
    return None


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
        log.info("Listening on ws://localhost:%d", config.ws_port)
        await state.shutdown_event.wait()
