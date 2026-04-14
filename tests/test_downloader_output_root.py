"""Tests for task_output_root + TaskFSError in scrap_pub.daemon.downloader."""

from pathlib import Path

import pytest

from scrap_pub.daemon.config import Config
from scrap_pub.daemon.downloader import TaskFSError, task_output_root


def _config_with_output(tmp_path: Path) -> Config:
    cfg = Config(output_dir=tmp_path / "default-output")
    return cfg


def test_task_without_output_dir_falls_back_to_config(tmp_path):
    cfg = _config_with_output(tmp_path)
    task = {"id": 1, "output_dir": None}
    assert task_output_root(task, cfg) == cfg.output_dir


def test_task_with_output_dir_wins(tmp_path):
    cfg = _config_with_output(tmp_path)
    custom = tmp_path / "plex" / "TV Shows"
    task = {"id": 1, "output_dir": str(custom)}
    assert task_output_root(task, cfg) == custom


def test_task_output_dir_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _config_with_output(tmp_path)
    task = {"id": 1, "output_dir": "~/plex"}
    assert task_output_root(task, cfg) == Path(tmp_path / "plex")


def test_task_empty_string_output_dir_falls_back(tmp_path):
    cfg = _config_with_output(tmp_path)
    task = {"id": 1, "output_dir": ""}
    assert task_output_root(task, cfg) == cfg.output_dir


def test_task_fs_error_carries_path_and_op():
    cause = OSError(28, "No space left on device")
    err = TaskFSError("writing merged MKV to", Path("/mnt/plex/foo.mkv"), cause)
    assert err.op == "writing merged MKV to"
    assert err.path == Path("/mnt/plex/foo.mkv")
    assert err.cause is cause
    msg = str(err)
    assert "writing merged MKV to" in msg
    assert "/mnt/plex/foo.mkv" in msg
    assert "No space left on device" in msg


def test_task_fs_error_is_raised_and_caught_like_runtime_error():
    with pytest.raises(RuntimeError):
        raise TaskFSError("creating work dir", Path("/tmp/foo"),
                          OSError(13, "Permission denied"))
