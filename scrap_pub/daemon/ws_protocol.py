"""
ws_protocol.py — WebSocket message types and encode/decode helpers.

Shared between the server (ws_server.py) and the CLI client (cli_main.py).
All messages are JSON dicts.

Server → client (push events):
  daemon_status   — current daemon state (sent on connect + on change)
  stream_progress — per-stream download progress during active task
  task_update     — task status changed
  stream_update   — stream status changed
  task_error      — error during task processing
  cookie_error    — session cookies expired, workers paused
  log             — log line (task-scoped or global)

Client → server (commands):
  enqueue         — add item by URL
  list            — list tasks (supports status/kind/since/verbose filters)
  get             — fetch one task (with its streams + live progress overlay)
  sql             — run a SELECT/WITH/PRAGMA/EXPLAIN against the DB (read-only
                    by default; write=true required for DML)
  logs            — fetch log lines
  status          — get daemon status
  retry           — retry failed task
  skip            — skip pending task
  pause / resume  — pause or resume all workers
  cookies         — update auth cookies
  add_audio       — download extra audio track + remux
  add_sub         — download extra subtitle sidecar
  config_get      — get current config
  config_set      — update a config value

Server → client (reply to command):
  reply           — always includes {type:"reply", cmd:"...", ok:bool, ...}
"""

import json

# ── Event types ────────────────────────────────────────────────────────────────

EVT_DAEMON_STATUS   = "daemon_status"
EVT_STREAM_PROGRESS = "stream_progress"
EVT_TASK_UPDATE     = "task_update"
EVT_STREAM_UPDATE   = "stream_update"
EVT_TASK_ERROR      = "task_error"
EVT_COOKIE_ERROR    = "cookie_error"
EVT_LOG             = "log"
EVT_REPLY           = "reply"

# ── Command names ──────────────────────────────────────────────────────────────

CMD_ENQUEUE    = "enqueue"
CMD_LIST       = "list"
CMD_GET        = "get"
CMD_SQL        = "sql"
CMD_LOGS       = "logs"
CMD_STATUS     = "status"
CMD_RETRY      = "retry"
CMD_SKIP       = "skip"
CMD_PAUSE      = "pause"
CMD_RESUME     = "resume"
CMD_COOKIES    = "cookies"
CMD_ADD_AUDIO  = "add_audio"
CMD_ADD_SUB    = "add_sub"
CMD_CONFIG_GET          = "config_get"
CMD_CONFIG_SET          = "config_set"
CMD_OUTPUT_DIR_HISTORY  = "output_dir_history"


# ── Encode / decode ────────────────────────────────────────────────────────────

def encode(msg: dict) -> str:
    return json.dumps(msg, ensure_ascii=False)


def decode(raw: str | bytes) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


def reply_ok(cmd: str, **kwargs) -> dict:
    return {"type": EVT_REPLY, "cmd": cmd, "ok": True, **kwargs}


def reply_err(cmd: str, error: str) -> dict:
    return {"type": EVT_REPLY, "cmd": cmd, "ok": False, "error": error}
