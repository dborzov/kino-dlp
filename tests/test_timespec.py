"""
Tests for scrap_pub.daemon.timespec.parse_since.

The function is shared between the CLI (`scrap-pub list --since`) and the
WebSocket server (so the web UI can send literal specs like `today`/`week`).
Two things matter:

  1. Output is ALWAYS a UTC ISO-8601 string. SQLite compares `enqueued_at`
     lexically; a non-UTC threshold against a UTC-stored column would fail
     silently. (This was the root cause of the Today filter bug in the web
     UI — the raw string `today` was being compared to ISO timestamps,
     which lexically always evaluated false.)

  2. `today` and `yesterday` resolve to the user's local calendar day, not
     UTC's. A user in PDT expecting "today" to mean 00:00–24:00 local would
     otherwise see tasks from late the previous evening show up as "today"
     (or vice versa).
"""

from datetime import datetime, timedelta, timezone

import pytest

from scrap_pub.daemon.timespec import parse_since


def test_parse_since_none_empty():
    assert parse_since(None) is None
    assert parse_since("") is None


def test_parse_since_today_uses_local_midnight():
    """`today` is local midnight of the current calendar day, rendered as UTC."""
    result = parse_since("today")
    assert result is not None

    # Round-trip through datetime and compare to the independently computed
    # local midnight → UTC conversion.
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo == timezone.utc

    expected_local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert parsed == expected_local_midnight.astimezone(timezone.utc)


def test_parse_since_yesterday_uses_local_midnight():
    result = parse_since("yesterday")
    parsed = datetime.fromisoformat(result)
    expected = (
        datetime.now().astimezone() - timedelta(days=1)
    ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    assert parsed == expected


def test_parse_since_week_and_month_are_relative_offsets():
    """week/month are rolling offsets from now, not calendar boundaries."""
    before = datetime.now(timezone.utc)
    result = parse_since("week")
    after = datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(result)
    lo = before - timedelta(days=7, seconds=1)
    hi = after - timedelta(days=7) + timedelta(seconds=1)
    assert lo <= parsed <= hi


@pytest.mark.parametrize("spec, delta", [
    ("7d",  timedelta(days=7)),
    ("24h", timedelta(hours=24)),
    ("30m", timedelta(minutes=30)),
])
def test_parse_since_relative_offsets(spec, delta):
    parsed = datetime.fromisoformat(parse_since(spec))
    now = datetime.now(timezone.utc)
    assert now - delta - timedelta(seconds=2) <= parsed <= now - delta + timedelta(seconds=2)


def test_parse_since_iso_naive_assumed_utc():
    """A naive ISO input is treated as UTC."""
    parsed = datetime.fromisoformat(parse_since("2026-04-14T10:00:00"))
    assert parsed == datetime(2026, 4, 14, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_since_iso_aware_normalised_to_utc():
    """An ISO input with a tz offset is converted to UTC for consistent comparison."""
    parsed = datetime.fromisoformat(parse_since("2026-04-14T10:00:00-07:00"))
    assert parsed == datetime(2026, 4, 14, 17, 0, 0, tzinfo=timezone.utc)


def test_parse_since_invalid():
    with pytest.raises(ValueError):
        parse_since("tomorrow")
