"""
timespec.py — Shared human-friendly time-window parser.

Used by the CLI (`scrap-pub list --since ...`) and the daemon's WebSocket
handler (so the web UI can send literal specs like `today`/`week` without
having to replicate this logic in JavaScript).

`parse_since(spec)` accepts:
  - today, yesterday   → local-time midnight of that calendar day
  - week, month        → now − 7d / 30d
  - Nd / Nh / Nm       → now − N days/hours/minutes
  - ISO date/datetime  → parsed as-is; naive values are assumed UTC

Always returns a UTC ISO-8601 string (or None for None/empty input) so that
SQLite's lexical string comparison against `enqueued_at`/`completed_at`
columns (themselves stored as UTC ISO) produces correct chronological
results.

Local-time handling for `today`/`yesterday` matters because the user expects
"today" to mean their own calendar day, not UTC's. `datetime.now().astimezone()`
captures the system timezone.
"""

from datetime import datetime, timedelta, timezone


def parse_since(spec: str | None) -> str | None:
    if not spec:
        return None
    s = spec.strip().lower()
    now_utc = datetime.now(timezone.utc)
    local_now = datetime.now().astimezone()
    if s == "today":
        t = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif s == "yesterday":
        t = (local_now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif s == "week":
        t = now_utc - timedelta(days=7)
    elif s == "month":
        t = now_utc - timedelta(days=30)
    elif s.endswith("d") and s[:-1].isdigit():
        t = now_utc - timedelta(days=int(s[:-1]))
    elif s.endswith("h") and s[:-1].isdigit():
        t = now_utc - timedelta(hours=int(s[:-1]))
    elif s.endswith("m") and s[:-1].isdigit():
        t = now_utc - timedelta(minutes=int(s[:-1]))
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
    return t.astimezone(timezone.utc).isoformat()
