"""
cli_main.py — Entry point for the scrap-pub CLI client.

All subcommands open a short-lived WebSocket connection, send one JSON command,
wait for the {"type": "reply"} response, print the result, and exit.

Exception: `scrap-pub logs --follow` keeps the connection open and streams
{"type": "log"} events until Ctrl-C.

Usage:
    scrap-pub status
    scrap-pub enqueue URL [URL ...]
    scrap-pub list [--status pending|active|done|failed|skipped] [--limit N]
    scrap-pub logs [--task ID] [--limit N] [--follow]
    scrap-pub retry ID
    scrap-pub skip ID
    scrap-pub pause
    scrap-pub resume
    scrap-pub cookies FILE          # Netscape cookies.txt (yt-dlp format)
    scrap-pub add-audio TASK_ID URL [--label LABEL]
    scrap-pub add-sub   TASK_ID URL [--lang LANG]
    scrap-pub config [--set KEY=VALUE]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# ── helpers ──────────────────────────────────────────────────────────────────


def _ws_url(config) -> str:
    return f"ws://localhost:{config.ws_port}"


async def _send_recv(ws_url: str, cmd: dict) -> dict:
    """Open a WS connection, send cmd, wait for matching reply, return it."""
    import websockets.asyncio.client as wscli

    async with wscli.connect(ws_url) as ws:
        await ws.send(json.dumps(cmd))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "reply" and msg.get("cmd") == cmd.get("cmd"):
                return msg
    return {"ok": False, "error": "connection closed before reply"}


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _ok_or_die(reply: dict) -> dict:
    if not reply.get("ok"):
        _die(reply.get("error", "unknown error"))
    return reply


# ── formatters ───────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "pending":  "·",
    "active":   "▶",
    "done":     "✓",
    "failed":   "✗",
    "skipped":  "–",
}


def _fmt_task(t: dict) -> str:
    icon = _STATUS_ICON.get(t.get("status", ""), "?")
    parts = [f"#{t['id']}", icon]
    if t.get("plex_stem"):
        parts.append(t["plex_stem"].split("/")[-1])
    elif t.get("kind") == "episode":
        parts.append(f"s{t.get('season', 0):02d}e{t.get('episode', 0):02d}")
    st = t.get("status", "")
    parts.append(st)
    if t.get("last_error"):
        parts.append(f"[{t['last_error'][:60]}]")
    return "  ".join(parts)


def _fmt_log(entry: dict) -> str:
    ts  = entry.get("ts", "")[:19].replace("T", " ")
    lvl = entry.get("level", "INFO")[:5].ljust(5)
    tid = f"[task {entry['task_id']}] " if entry.get("task_id") else ""
    return f"{ts}  {lvl}  {tid}{entry.get('msg', '')}"


# ── subcommand handlers ───────────────────────────────────────────────────────


async def cmd_status(args, config) -> None:
    reply = _ok_or_die(await _send_recv(_ws_url(config), {"cmd": "status"}))
    counts = reply.get("counts", {})
    print("Daemon status:")
    print(f"  paused:         {reply.get('paused')}")
    print(f"  active_workers: {reply.get('active_workers')}")
    print(f"  cookie_ok:      {reply.get('cookie_ok')}")
    print("  queue:")
    for k, v in counts.items():
        print(f"    {k:12s}: {v}")
    cfg = reply.get("config", {})
    if cfg:
        print(f"  concurrency:    {cfg.get('concurrency')}")
        print(f"  output_dir:     {cfg.get('output_dir')}")


async def cmd_enqueue(args, config) -> None:
    for url in args.url:
        reply = await _send_recv(_ws_url(config), {"cmd": "enqueue", "url": url})
        if reply.get("ok"):
            ids = reply.get("task_ids", [])
            print(f"Enqueued {reply.get('enqueued')} task(s): {ids}  ← {url}")
        else:
            print(f"Error for {url}: {reply.get('error')}", file=sys.stderr)


async def cmd_list(args, config) -> None:
    payload: dict[str, Any] = {"cmd": "list", "limit": args.limit}
    if args.status:
        payload["status"] = args.status
    reply = _ok_or_die(await _send_recv(_ws_url(config), payload))
    tasks = reply.get("tasks", [])
    if not tasks:
        print("(no tasks)")
        return
    for t in tasks:
        print(_fmt_task(t))


async def cmd_logs(args, config) -> None:
    import websockets.asyncio.client as wscli

    from .ws_protocol import EVT_LOG

    if args.follow:
        # Keep connection open, stream log events
        try:
            async with wscli.connect(_ws_url(config)) as ws:
                # Send logs query so server sends back history first
                payload: dict[str, Any] = {"cmd": "logs", "limit": args.limit}
                if args.task:
                    payload["task_id"] = args.task
                await ws.send(json.dumps(payload))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == EVT_LOG:
                        # Filter by task_id if requested
                        if args.task and msg.get("task_id") != args.task:
                            continue
                        print(_fmt_log(msg), flush=True)
                    # Swallow other message types (reply, daemon_status, etc.)
        except KeyboardInterrupt:
            pass
        return

    payload = {"cmd": "logs", "limit": args.limit}
    if args.task:
        payload["task_id"] = args.task
    reply = _ok_or_die(await _send_recv(_ws_url(config), payload))
    for entry in reply.get("logs", []):
        print(_fmt_log(entry))


async def cmd_retry(args, config) -> None:
    reply = _ok_or_die(await _send_recv(
        _ws_url(config), {"cmd": "retry", "task_id": args.id}
    ))
    print(f"Task {reply.get('task_id')} reset to pending.")


async def cmd_skip(args, config) -> None:
    reply = _ok_or_die(await _send_recv(
        _ws_url(config), {"cmd": "skip", "task_id": args.id}
    ))
    print(f"Task {reply.get('task_id')} skipped.")


async def cmd_pause(args, config) -> None:
    _ok_or_die(await _send_recv(_ws_url(config), {"cmd": "pause"}))
    print("Daemon paused.")


async def cmd_resume(args, config) -> None:
    _ok_or_die(await _send_recv(_ws_url(config), {"cmd": "resume"}))
    print("Daemon resumed.")


async def cmd_cookies(args, config) -> None:
    path = Path(args.file)
    if not path.exists():
        _die(f"file not found: {path}")
    raw = path.read_text()
    if not raw.strip():
        _die(f"file is empty: {path}")
    reply = _ok_or_die(await _send_recv(
        _ws_url(config), {"cmd": "cookies", "cookies_txt": raw}
    ))
    print(f"Updated {reply.get('count')} cookie(s). Daemon resumed.")


async def cmd_add_audio(args, config) -> None:
    payload: dict[str, Any] = {
        "cmd":     "add_audio",
        "task_id": args.task_id,
        "url":     args.url,
    }
    if args.label:
        payload["label"] = args.label
    reply = _ok_or_die(await _send_recv(_ws_url(config), payload))
    print(f"Audio stream {reply.get('stream_id')} queued for task {args.task_id}.")


async def cmd_add_sub(args, config) -> None:
    payload: dict[str, Any] = {
        "cmd":     "add_sub",
        "task_id": args.task_id,
        "url":     args.url,
    }
    if args.lang:
        payload["lang"] = args.lang
    reply = _ok_or_die(await _send_recv(_ws_url(config), payload))
    print(f"Subtitle stream {reply.get('stream_id')} queued for task {args.task_id}.")


async def cmd_config(args, config) -> None:
    if args.set:
        # Parse KEY=VALUE pairs
        for kv in args.set:
            if "=" not in kv:
                _die(f"--set expects KEY=VALUE, got: {kv!r}")
            key, _, value = kv.partition("=")
            # Try to parse value as JSON (handles int, bool, list, etc.)
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = value  # treat as plain string
            reply = _ok_or_die(await _send_recv(
                _ws_url(config), {"cmd": "config_set", "key": key.strip(), "value": parsed}
            ))
            print(f"  {reply.get('key')} = {reply.get('value')!r}")
    else:
        reply = _ok_or_die(await _send_recv(_ws_url(config), {"cmd": "config_get"}))
        cfg = reply.get("config", {})
        for k, v in cfg.items():
            print(f"  {k:24s}: {v!r}")


# ── arg parser ────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrap-pub",
        description="Control the scrap-pub daemon via WebSocket.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to config.json (default: ~/.config/scrap-pub/config.json)",
    )

    sub = parser.add_subparsers(dest="subcmd", metavar="COMMAND", required=True)

    # status
    sub.add_parser("status", help="Show daemon status and queue counts.")

    # enqueue
    p = sub.add_parser("enqueue", help="Enqueue one or more item URLs from the target site.")
    p.add_argument("url", nargs="+", help="Item URL(s) on the configured target site.")

    # list
    p = sub.add_parser("list", help="List tasks in the queue.")
    p.add_argument("--status", choices=["pending", "active", "done", "failed", "skipped"],
                   default=None, help="Filter by status.")
    p.add_argument("--limit", type=int, default=50, help="Max rows to return (default 50).")

    # logs
    p = sub.add_parser("logs", help="Show or stream log entries.")
    p.add_argument("--task", type=int, default=None, metavar="ID",
                   help="Filter logs for a specific task.")
    p.add_argument("--limit", type=int, default=100, help="Lines to fetch (default 100).")
    p.add_argument("--follow", "-f", action="store_true",
                   help="Stay connected and stream new log lines.")

    # retry
    p = sub.add_parser("retry", help="Reset a failed task back to pending.")
    p.add_argument("id", type=int, metavar="TASK_ID")

    # skip
    p = sub.add_parser("skip", help="Mark a task as skipped (won't be downloaded).")
    p.add_argument("id", type=int, metavar="TASK_ID")

    # pause / resume
    sub.add_parser("pause",  help="Pause the download workers.")
    sub.add_parser("resume", help="Resume paused workers.")

    # cookies
    p = sub.add_parser(
        "cookies",
        help="Load session cookies from a Netscape cookies.txt file (yt-dlp format).",
    )
    p.add_argument(
        "file",
        metavar="FILE",
        help="Path to a Netscape/Mozilla cookies.txt exported from your browser.",
    )

    # add-audio
    p = sub.add_parser("add-audio", help="Download an extra audio track and remux into MKV.")
    p.add_argument("task_id", type=int, metavar="TASK_ID")
    p.add_argument("url", metavar="URL", help="HLS audio track URL.")
    p.add_argument("--label", default=None, help="Human-readable label for the track.")

    # add-sub
    p = sub.add_parser("add-sub", help="Download a subtitle sidecar.")
    p.add_argument("task_id", type=int, metavar="TASK_ID")
    p.add_argument("url", metavar="URL", help="Subtitle URL (.vtt or .srt).")
    p.add_argument("--lang", default=None, help="ISO-639-2 language code (e.g. 'rus', 'eng').")

    # config
    p = sub.add_parser("config", help="Show or update daemon config.")
    p.add_argument("--set", nargs="+", metavar="KEY=VALUE",
                   help="Set one or more config keys (value parsed as JSON).")

    return parser


_HANDLERS = {
    "status":    cmd_status,
    "enqueue":   cmd_enqueue,
    "list":      cmd_list,
    "logs":      cmd_logs,
    "retry":     cmd_retry,
    "skip":      cmd_skip,
    "pause":     cmd_pause,
    "resume":    cmd_resume,
    "cookies":   cmd_cookies,
    "add-audio": cmd_add_audio,
    "add-sub":   cmd_add_sub,
    "config":    cmd_config,
}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    from .config import Config
    config = Config.load(args.config)

    handler = _HANDLERS.get(args.subcmd)
    if handler is None:
        _die(f"unknown subcommand: {args.subcmd!r}")

    try:
        asyncio.run(handler(args, config))
    except ConnectionRefusedError:
        _die(
            f"cannot connect to ws://localhost:{config.ws_port} — "
            "is scrap-pub-server running?"
        )
    except KeyboardInterrupt:
        pass
