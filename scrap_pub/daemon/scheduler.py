"""
scheduler.py — asyncio event loop, AppState, worker pool, broadcaster.

AppState is the single shared-state object passed to all async components.
"""

import asyncio
import functools
import signal
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .config import Config


@dataclass
class AppState:
    config:        Config
    conn:          sqlite3.Connection
    loop:          asyncio.AbstractEventLoop
    db_executor:   ThreadPoolExecutor
    net_executor:  ThreadPoolExecutor
    work_queue:    asyncio.Queue
    progress_queue: asyncio.Queue
    pause_event:   asyncio.Event           # set=running, clear=paused
    shutdown_event: asyncio.Event
    ws_clients:    set = field(default_factory=set)
    active_tasks:  dict = field(default_factory=dict)  # task_id → worker_id
    worker_count:  int = 0
    # stream_id → {pct, speed, eta_sec, elapsed_sec, size_bytes}; live, not persisted.
    stream_progress: dict = field(default_factory=dict)


async def db_run(state: AppState, fn, *args, **kwargs):
    """Run a synchronous DB function in the dedicated DB executor.

    Supports both positional and keyword arguments — kwargs are bound via
    functools.partial so they survive the run_in_executor boundary.
    """
    if kwargs:
        fn = functools.partial(fn, **kwargs)
    return await state.loop.run_in_executor(state.db_executor, fn, *args)


async def net_run(state: AppState, fn, *args):
    """Run a synchronous network function in the net executor."""
    return await state.loop.run_in_executor(state.net_executor, fn, *args)


async def scheduler_loop(state: AppState) -> None:
    """Poll DB for pending tasks and put them on the work queue."""
    from .db import db_claim_next_task

    while not state.shutdown_event.is_set():
        try:
            await asyncio.wait_for(state.pause_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        if state.work_queue.full():
            await asyncio.sleep(2)
            continue

        task = await db_run(state, db_claim_next_task, state.conn)
        if task:
            await state.work_queue.put(task)
        else:
            await asyncio.sleep(5)  # Nothing pending, poll every 5s


async def worker_task(worker_id: int, state: AppState) -> None:
    """Pull tasks from work_queue and download them."""
    from .downloader import download_task

    while not state.shutdown_event.is_set():
        try:
            await asyncio.wait_for(state.pause_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        try:
            task = await asyncio.wait_for(state.work_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue

        state.active_tasks[task["id"]] = worker_id
        state.worker_count = len(state.active_tasks)
        try:
            await download_task(task, state)
        except Exception as e:
            from .db import db_log, db_set_task_status
            await db_run(state, db_set_task_status, state.conn, task["id"], "failed", error=str(e))
            await db_run(state, db_log, state.conn, "ERROR", f"Worker {worker_id} crash: {e}", task["id"])
        finally:
            state.active_tasks.pop(task["id"], None)
            state.worker_count = len(state.active_tasks)
            state.work_queue.task_done()
            await _broadcast_daemon_status(state)


async def broadcaster(state: AppState) -> None:
    """Drain progress_queue and broadcast to all WebSocket clients."""
    from .ws_server import broadcast

    while not state.shutdown_event.is_set():
        try:
            msg = await asyncio.wait_for(state.progress_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        await broadcast(state, msg)


async def _broadcast_daemon_status(state: AppState) -> None:
    from .db import db_is_cookie_error, db_is_paused, db_queue_summary
    from .ws_protocol import EVT_DAEMON_STATUS
    from .ws_server import broadcast

    summary = await db_run(state, db_queue_summary, state.conn)
    paused       = await db_run(state, db_is_paused,       state.conn)
    cookie_error = await db_run(state, db_is_cookie_error, state.conn)

    await broadcast(state, {
        "type":           EVT_DAEMON_STATUS,
        "paused":         paused,
        "active_workers": state.worker_count,
        "queue_depth":    summary["pending"],
        "cookie_ok":      not cookie_error,
        "counts":         summary,
    })


async def main(config: Config) -> None:
    """Start the full daemon: DB, session, HTTP server, WS server, workers."""
    import threading

    from .db import db_is_paused, open_db
    from .ffmpeg import set_origin
    from .scraper import set_website
    from .server_http import start_http_server
    from .session import init_session
    from .ws_server import serve_ws

    # Init target site (URL construction in scraper.py, ffmpeg origin header).
    # server_main has already called Config.validate(), so `website` is non-empty here.
    set_website(config.website)
    set_origin(config.website)

    # Init DB
    conn = open_db(config.db_path)

    # Init session cookies from Netscape cookies.txt
    init_session(config.cookies_path)

    # Create shared state
    loop = asyncio.get_event_loop()
    pause_event = asyncio.Event()
    # Start unpaused unless DB says paused
    if not db_is_paused(conn):
        pause_event.set()

    state = AppState(
        config         = config,
        conn           = conn,
        loop           = loop,
        db_executor    = ThreadPoolExecutor(max_workers=1,  thread_name_prefix="db"),
        net_executor   = ThreadPoolExecutor(max_workers=4,  thread_name_prefix="net"),
        work_queue     = asyncio.Queue(maxsize=config.concurrency),
        progress_queue = asyncio.Queue(),
        pause_event    = pause_event,
        shutdown_event = asyncio.Event(),
    )

    # Start HTTP server in daemon thread
    threading.Thread(
        target=start_http_server,
        args=(config,),
        daemon=True,
        name="http",
    ).start()

    # Signal handlers
    def _shutdown():
        state.shutdown_event.set()
        pause_event.set()  # Unblock waiting workers

    loop.add_signal_handler(signal.SIGTERM, _shutdown)
    loop.add_signal_handler(signal.SIGINT,  _shutdown)

    print(f"[daemon] Starting — http://localhost:{config.http_port}  ws://localhost:{config.ws_port}")
    print(f"[daemon] Output: {config.output_dir}")
    print(f"[daemon] DB:     {config.db_path}")

    # Launch async tasks
    tasks = [
        asyncio.create_task(scheduler_loop(state), name="scheduler"),
        asyncio.create_task(broadcaster(state),    name="broadcaster"),
        asyncio.create_task(serve_ws(state),       name="ws_server"),
    ]
    tasks += [
        asyncio.create_task(worker_task(i, state), name=f"worker_{i}")
        for i in range(config.concurrency)
    ]

    await state.shutdown_event.wait()

    print("[daemon] Shutting down...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    state.db_executor.shutdown(wait=False)
    state.net_executor.shutdown(wait=False)
    conn.close()
    print("[daemon] Done.")
