"""
Tests for scrap_pub.daemon.ffmpeg — progress parsing and stall detection.

We don't run actual ffmpeg in unit tests. Instead we exercise the
progress-line parser and stall watchdog by feeding fake stderr output.
"""

import pytest

from scrap_pub.daemon.ffmpeg import (
    StallError,
    _parse_progress_line,
    run_ffmpeg,
)

# ── _parse_progress_line ─────────────────────────────────────────────────────


@pytest.mark.parametrize("line, elapsed, speed, size_kb", [
    # Typical ffmpeg progress line
    ("size=    1234kB time=00:01:30.00 bitrate= 112.0kbits/s speed= 2.5x", 90, 2.5, 1234),
    # Fractional seconds — only HH:MM:SS parsed, .xx ignored
    ("size=      500kB time=00:00:45.50 bitrate= 90.0kbits/s speed= 1.0x",  45, 1.0, 500),
    # Only time, no speed/size fields
    ("time=00:02:00",                                                        120, None, None),
])
def test_parse_progress_line_fields(line, elapsed, speed, size_kb):
    result = _parse_progress_line(line, duration_sec=None)
    assert result is not None
    assert result["elapsed_sec"] == elapsed
    assert result["speed"] == speed
    if size_kb is None:
        assert result["size_bytes"] is None
    else:
        assert result["size_bytes"] == size_kb * 1024


def test_parse_progress_line_returns_none_for_non_progress():
    line = "Input #0, hls, from 'https://cdn/playlist.m3u8':"
    assert _parse_progress_line(line, duration_sec=None) is None


def test_parse_progress_line_with_duration():
    # 90s elapsed out of 300s total → 30%
    result = _parse_progress_line("size=1kB time=00:01:30 speed=1.0x", duration_sec=300)
    assert result is not None
    assert abs(result["pct"] - 30.0) < 0.1


def test_parse_progress_line_clamps_pct_at_99():
    # elapsed > duration should not give pct > 99
    result = _parse_progress_line("size=1kB time=00:10:00 speed=1.0x", duration_sec=60)
    assert result is not None
    assert result["pct"] <= 99.0


def test_parse_progress_line_no_duration_gives_none_pct():
    result = _parse_progress_line("time=00:01:00", duration_sec=None)
    assert result is not None
    assert result["pct"] is None


# ── ETA ───────────────────────────────────────────────────────────────────────


def test_eta_computed_when_duration_and_speed_present():
    # 60s elapsed of 300s, at 2.0x → (300-60)/2 = 120s remaining
    result = _parse_progress_line(
        "size=1kB time=00:01:00 speed=2.0x", duration_sec=300
    )
    assert result is not None
    assert result["eta_sec"] == 120


def test_eta_none_without_speed():
    result = _parse_progress_line("time=00:01:00", duration_sec=300)
    assert result is not None
    assert result["eta_sec"] is None


def test_eta_none_without_duration():
    result = _parse_progress_line(
        "size=1kB time=00:01:00 speed=1.0x", duration_sec=None
    )
    assert result is not None
    assert result["eta_sec"] is None


def test_eta_none_past_end_of_stream():
    # elapsed >= duration → eta not computed (stream is effectively done)
    result = _parse_progress_line(
        "size=1kB time=00:10:00 speed=1.0x", duration_sec=60
    )
    assert result is not None
    assert result["eta_sec"] is None


def test_eta_non_negative():
    # fractional division should still clamp to non-negative int
    result = _parse_progress_line(
        "size=1kB time=00:00:59 speed=10.0x", duration_sec=60
    )
    assert result is not None
    assert result["eta_sec"] is not None
    assert result["eta_sec"] >= 0


# ── run_ffmpeg ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_ffmpeg_success():
    """Run a trivial shell command that exits 0 immediately."""
    progress_calls = []

    rc, stalled = await run_ffmpeg(
        ["true"],
        duration_sec=None,
        on_progress=lambda info: progress_calls.append(info),
        stall_timeout=10,
    )
    assert rc == 0
    assert stalled is False


@pytest.mark.asyncio
async def test_run_ffmpeg_nonzero_exit():
    """A command that exits with code 1 is reported as failure (not stall)."""
    rc, stalled = await run_ffmpeg(
        ["false"],  # exits with code 1
        duration_sec=None,
        on_progress=lambda _: None,
        stall_timeout=10,
    )
    assert rc != 0
    assert stalled is False


@pytest.mark.asyncio
async def test_run_ffmpeg_stall_detection():
    """
    A process that writes nothing to stderr triggers the stall watchdog,
    which raises StallError.
    """
    with pytest.raises(StallError):
        await run_ffmpeg(
            ["sleep", "60"],
            duration_sec=None,
            on_progress=lambda _: None,
            stall_timeout=1,
        )
