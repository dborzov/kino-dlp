"""
cli_main.py — Entry point for the scrap-pub CLI client.

All subcommands open a short-lived WebSocket connection, send one JSON command,
wait for the {"type": "reply"} response, print the result, and exit.

Exception: `scrap-pub logs --follow` keeps the connection open and streams
{"type": "log"} events until Ctrl-C.

Usage:
    scrap-pub status
    scrap-pub enqueue URL [URL ...]
    scrap-pub list [--status S] [--kind K] [--since SPEC] [--verbose] [--json]
    scrap-pub show TASK_ID [--json]
    scrap-pub sql "SELECT ..." [--write] [--json|--csv] [--limit N]
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
from datetime import datetime, timedelta, timezone
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


def _parse_since(spec: str | None) -> str | None:
    """Convert a human-friendly time spec into an ISO-8601 UTC string.

    Accepts: today, yesterday, week, month, 7d / 24h / 30m relative offsets,
    or an ISO date/datetime. Returns None for None/empty input.
    Raises ValueError on anything unparseable.
    """
    if not spec:
        return None
    s = spec.strip().lower()
    now = datetime.now(timezone.utc)
    if s == "today":
        t = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif s == "yesterday":
        t = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif s == "week":
        t = now - timedelta(days=7)
    elif s == "month":
        t = now - timedelta(days=30)
    elif s.endswith("d") and s[:-1].isdigit():
        t = now - timedelta(days=int(s[:-1]))
    elif s.endswith("h") and s[:-1].isdigit():
        t = now - timedelta(hours=int(s[:-1]))
    elif s.endswith("m") and s[:-1].isdigit():
        t = now - timedelta(minutes=int(s[:-1]))
    else:
        try:
            t = datetime.fromisoformat(spec)
        except ValueError as e:
            raise ValueError(
                f"invalid time spec {spec!r}: expected today/yesterday/week/month, "
                "N{d,h,m}, or an ISO date"
            ) from e
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
    return t.isoformat()


def _fmt_eta(seconds: Any) -> str:
    if seconds is None:
        return ""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return "<1m"
    if s < 3600:
        return f"{s // 60}m"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h{m}m" if m else f"{h}h"


def _fmt_bytes(b: Any) -> str:
    if not b:
        return ""
    try:
        n = float(b)
    except (TypeError, ValueError):
        return ""
    kb = 1024.0
    if n < kb * kb:
        return f"{n / kb:.0f} KB"
    if n < kb * kb * kb:
        return f"{n / (kb * kb):.1f} MB"
    return f"{n / (kb * kb * kb):.2f} GB"


def _fmt_rel_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - t
    s = int(delta.total_seconds())
    if s < 0:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _fmt_progress_bar(pct: Any, width: int = 10) -> str:
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return "·" * width
    filled = int(max(0.0, min(100.0, p)) / 100.0 * width)
    return "█" * filled + "░" * (width - filled)


def _print_table(columns: list[str], rows: list[list]) -> None:
    if not columns:
        return
    str_rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [len(c) for c in columns]
    for r in str_rows:
        for i, v in enumerate(r):
            if i < len(widths) and len(v) > widths[i]:
                widths[i] = len(v)
    print("  ".join(c.ljust(widths[i]) for i, c in enumerate(columns)))
    print("  ".join("-" * widths[i] for i in range(len(columns))))
    for r in str_rows:
        print("  ".join(v.ljust(widths[i]) for i, v in enumerate(r) if i < len(widths)))


def _fmt_task(t: dict) -> str:
    icon = _STATUS_ICON.get(t.get("status", ""), "?")
    parts = [f"#{t['id']}", icon]
    if t.get("plex_stem"):
        parts.append(t["plex_stem"].split("/")[-1])
    elif t.get("kind") == "episode":
        parts.append(f"s{t.get('season', 0):02d}e{t.get('episode', 0):02d}")
    parts.append(t.get("status", ""))
    rel = _fmt_rel_time(t.get("enqueued_at"))
    if rel:
        parts.append(rel)
    size = _fmt_bytes(t.get("output_size_bytes"))
    if size:
        parts.append(f"({size})")
    if t.get("last_error"):
        parts.append(f"[{t['last_error'][:60]}]")
    return "  ".join(parts)


def _fmt_stream(s: dict) -> str:
    stype = (s.get("stream_type") or "").ljust(5)[:5]
    label = (s.get("label") or "").ljust(14)[:14]
    pct = s.get("pct")
    if pct is None:
        pct = s.get("progress_pct")
    bar = _fmt_progress_bar(pct)
    pct_s = f"{int(float(pct)):3d}%" if pct is not None else "  - "
    done = s.get("status") == "done"
    eta = "✓" if done else (_fmt_eta(s.get("eta_sec")) or "")
    eta = eta.ljust(6)
    speed = s.get("speed")
    speed_s = f"{float(speed):.1f}x" if (speed and not done) else ""
    size = _fmt_bytes(s.get("size_bytes"))
    extras = ", ".join(x for x in (size, speed_s) if x)
    extras_s = f"({extras})" if extras else ""
    return f"{stype}  {label}  {bar} {pct_s}  {eta}  {extras_s}".rstrip()


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
    try:
        since           = _parse_since(args.since)
        until           = _parse_since(args.until)
        completed_since = _parse_since(args.completed_since)
    except ValueError as e:
        _die(str(e))

    payload: dict[str, Any] = {
        "cmd":     "list",
        "limit":   args.limit,
        "offset":  args.offset,
        "verbose": args.verbose,
    }
    if args.status:
        payload["status"] = args.status
    if args.kind:
        payload["kind"] = args.kind
    if since:
        payload["since"] = since
    if until:
        payload["until"] = until
    if completed_since:
        payload["completed_since"] = completed_since

    reply = _ok_or_die(await _send_recv(_ws_url(config), payload))

    if args.json:
        print(json.dumps(reply, indent=2, default=str, ensure_ascii=False))
        return

    tasks = reply.get("tasks", [])
    if not tasks:
        print("(no tasks)")
        return

    # WS JSON replies deliver dict keys as strings; coerce back to int for lookup.
    streams_by_task_raw = reply.get("streams_by_task") or {}
    streams_by_task = {int(k): v for k, v in streams_by_task_raw.items()}

    for t in tasks:
        print(_fmt_task(t))
        if args.verbose:
            for s in streams_by_task.get(t["id"], []):
                print("    " + _fmt_stream(s))


async def cmd_show(args, config) -> None:
    reply = _ok_or_die(await _send_recv(
        _ws_url(config), {"cmd": "get", "task_id": args.id}
    ))
    if args.json:
        print(json.dumps(reply, indent=2, default=str, ensure_ascii=False))
        return

    t = reply.get("task") or {}
    streams = reply.get("streams") or []
    icon = _STATUS_ICON.get(t.get("status", ""), "?")
    print(f"#{t.get('id')}  {icon}  {t.get('status','')}  ({t.get('kind','')})")
    stem = t.get("plex_stem") or "—"
    print(f"  title       : {stem}")
    if t.get("kind") == "episode":
        print(
            f"  s/e         : s{t.get('season',0):02d}e{t.get('episode',0):02d}  "
            f"{t.get('episode_title') or ''}"
        )
    for label, key in (("enqueued", "enqueued_at"),
                       ("started",  "started_at"),
                       ("completed","completed_at")):
        v = t.get(key)
        rel = _fmt_rel_time(v)
        pretty = f"{v}  ({rel})" if v and rel else (v or "—")
        print(f"  {label:11s} : {pretty}")
    print(f"  attempts    : {t.get('attempts', 0)}")
    print(f"  output size : {_fmt_bytes(t.get('output_size_bytes')) or '—'}")
    if t.get("mkv_path"):
        print(f"  mkv         : {t['mkv_path']}")
    if t.get("last_error"):
        print(f"  last_error  : {t['last_error']}")
    if streams:
        print("  streams:")
        for s in streams:
            print("    " + _fmt_stream(s))


async def cmd_sql(args, config) -> None:
    if args.file:
        if args.file == "-":
            query = sys.stdin.read()
        else:
            try:
                query = Path(args.file).read_text()
            except OSError as e:
                _die(f"could not read {args.file}: {e}")
    elif args.query is not None:
        query = args.query
    else:
        _die("provide a query as positional arg, -f FILE, or -f -")

    payload: dict[str, Any] = {
        "cmd":      "sql",
        "query":    query,
        "max_rows": args.limit,
    }
    if args.write:
        payload["write"] = True
        print("warning: running SQL with --write — this can modify data", file=sys.stderr)

    reply = await _send_recv(_ws_url(config), payload)
    if not reply.get("ok"):
        err = reply.get("error", "unknown error")
        print(f"error: {err}", file=sys.stderr)
        # Safety-gate rejection gets its own exit code so scripts can tell it apart.
        sys.exit(2 if "refusing" in err else 1)

    columns  = reply.get("columns") or []
    rows     = reply.get("rows") or []
    rowcount = reply.get("rowcount", 0)
    truncated = reply.get("truncated", False)

    if args.json:
        print(json.dumps({
            "columns":   columns,
            "rows":      rows,
            "rowcount":  rowcount,
            "truncated": truncated,
        }, indent=2, default=str, ensure_ascii=False))
        return

    if args.csv:
        import csv
        w = csv.writer(sys.stdout)
        if columns:
            w.writerow(columns)
        for r in rows:
            w.writerow(r)
        if truncated:
            print(f"(truncated at {len(rows)} rows — raise --limit for more)", file=sys.stderr)
        return

    if not columns:
        # DML / DDL path — no result set, just a rowcount.
        print(f"{rowcount} row(s) affected.")
        return

    _print_table(columns, rows)
    if truncated:
        print(f"(truncated at {len(rows)} rows — raise --limit for more)")


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
    p.add_argument("--kind", choices=["movie", "episode"], default=None,
                   help="Filter by task kind.")
    p.add_argument("--since", metavar="SPEC", default=None,
                   help="Only tasks enqueued after SPEC "
                        "(today, yesterday, week, month, 7d, 24h, 30m, or ISO date).")
    p.add_argument("--until", metavar="SPEC", default=None,
                   help="Upper bound for --since, same SPEC format.")
    p.add_argument("--completed-since", dest="completed_since", metavar="SPEC", default=None,
                   help="Only tasks that completed after SPEC.")
    p.add_argument("--limit", type=int, default=50, help="Max rows to return (default 50).")
    p.add_argument("--offset", type=int, default=0, help="Skip N rows (default 0).")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show per-stream progress rows under each task.")
    p.add_argument("--json", action="store_true", help="Emit the raw reply as JSON.")

    # show
    p = sub.add_parser("show", help="Show a single task by id with full detail.")
    p.add_argument("id", type=int, metavar="TASK_ID")
    p.add_argument("--json", action="store_true", help="Emit the raw reply as JSON.")

    # sql
    p = sub.add_parser(
        "sql",
        help="Run a SQL query against the daemon DB (read-only by default).",
    )
    p.add_argument("query", nargs="?", default=None,
                   help="SQL query string. Omit to read from -f/--file.")
    p.add_argument("-f", "--file", default=None, metavar="FILE",
                   help="Read query from FILE, or '-' for stdin.")
    p.add_argument("--write", action="store_true",
                   help="Allow DML/DDL (not SELECT/WITH/PRAGMA/EXPLAIN). Use with care.")
    p.add_argument("--json", action="store_true", help="Emit result as JSON.")
    p.add_argument("--csv", action="store_true", help="Emit result as CSV.")
    p.add_argument("--limit", type=int, default=1000,
                   help="Max rows to return (default 1000).")

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
    "show":      cmd_show,
    "sql":       cmd_sql,
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
