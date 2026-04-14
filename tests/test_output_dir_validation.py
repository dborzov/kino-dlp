"""Tests for validate_task_output_dir + estimate_min_free_gb."""

import pytest

from scrap_pub.daemon.config import (
    OutputDirError,
    estimate_min_free_gb,
    validate_task_output_dir,
)


def test_happy_path_existing_dir_returns_absolute(tmp_path):
    # min_free_gb=0 so the free-space check always passes on CI volumes.
    resolved = validate_task_output_dir(tmp_path, min_free_gb=0)
    assert resolved == tmp_path.resolve()
    assert resolved.is_absolute()


def test_creates_missing_dir_when_parent_exists(tmp_path):
    target = tmp_path / "plex-library"
    assert not target.exists()
    resolved = validate_task_output_dir(target, min_free_gb=0)
    assert resolved.exists()
    assert resolved.is_dir()


def test_fails_when_parent_missing(tmp_path):
    target = tmp_path / "nope" / "still-nope" / "plex"
    with pytest.raises(OutputDirError) as exc:
        validate_task_output_dir(target, min_free_gb=0)
    assert "parent does not exist" in str(exc.value)


def test_fails_when_path_is_a_file(tmp_path):
    f = tmp_path / "not-a-dir"
    f.write_text("stop")
    with pytest.raises(OutputDirError) as exc:
        validate_task_output_dir(f, min_free_gb=0)
    assert "not a directory" in str(exc.value)


def test_fails_when_unwritable(tmp_path):
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(0o500)  # r-x for owner, no write
    try:
        with pytest.raises(OutputDirError) as exc:
            validate_task_output_dir(target, min_free_gb=0)
        assert "not writable" in str(exc.value)
    finally:
        target.chmod(0o700)  # restore so pytest can clean up


def test_fails_on_low_free_space(tmp_path, monkeypatch):
    """Monkeypatch shutil.disk_usage so the free-space check fires deterministically."""
    from collections import namedtuple

    Usage = namedtuple("Usage", ["total", "used", "free"])
    fake_free = 2 * 1024**3  # 2 GB

    import scrap_pub.daemon.config as cfg_mod

    def fake_disk_usage(_path):
        return Usage(total=100 * 1024**3, used=98 * 1024**3, free=fake_free)

    monkeypatch.setattr(cfg_mod.shutil, "disk_usage", fake_disk_usage)
    with pytest.raises(OutputDirError) as exc:
        validate_task_output_dir(tmp_path, min_free_gb=10)
    msg = str(exc.value)
    assert "insufficient free space" in msg
    assert "2.0 GB free" in msg
    assert "need at least 10 GB" in msg


def test_expands_user(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    target = "~/plex"
    resolved = validate_task_output_dir(target, min_free_gb=0)
    assert resolved == (tmp_path / "plex").resolve()
    assert resolved.exists()


# ── estimate_min_free_gb ──────────────────────────────────────────────────────


def test_estimate_uses_base_when_duration_unknown():
    assert estimate_min_free_gb(None, base_min_gb=10) == 10
    assert estimate_min_free_gb(0, base_min_gb=7) == 7


def test_estimate_raises_floor_for_long_duration():
    # 2-hour film ≈ 2 * 3 + 2 = 8 GB → still below 10 GB floor.
    assert estimate_min_free_gb(2 * 3600, base_min_gb=10) == 10
    # 10-hour season ≈ 10 * 3 + 2 = 32 GB → above the floor.
    assert estimate_min_free_gb(10 * 3600, base_min_gb=10) == 32
