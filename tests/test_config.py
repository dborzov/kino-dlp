"""Tests for scrap_pub.daemon.config."""

import json
from pathlib import Path

import pytest

from scrap_pub.daemon.config import Config


def test_defaults():
    cfg = Config()
    assert cfg.concurrency == 2
    assert cfg.stall_timeout_sec == 300
    assert cfg.http_port == 8765
    assert cfg.ws_port == 8766
    assert cfg.video_quality == "lowest"
    assert "RUS" in cfg.audio_langs
    assert "rus" in cfg.sub_langs


def test_load_from_file(tmp_path: Path):
    data = {
        "output_dir": str(tmp_path / "output"),
        "tmp_dir":    str(tmp_path / "tmp"),
        "db_path":    str(tmp_path / "queue.db"),
        "concurrency": 4,
        "http_port": 9000,
        "ws_port":   9001,
        "video_quality": "highest",
        "audio_langs": ["RUS"],
        "sub_langs":   ["rus"],
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(data))

    cfg = Config.load(cfg_file)
    assert cfg.concurrency == 4
    assert cfg.http_port == 9000
    assert cfg.ws_port == 9001
    assert cfg.video_quality == "highest"
    assert cfg.audio_langs == ["RUS"]
    assert cfg.sub_langs == ["rus"]


def test_load_creates_defaults_if_missing(tmp_path: Path):
    cfg_file = tmp_path / "nonexistent" / "config.json"
    cfg = Config.load(cfg_file)
    assert cfg.concurrency == 2
    # File should have been created with defaults
    assert cfg_file.exists()


def test_save_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "config.json"
    cfg = Config(concurrency=3, http_port=9999)
    cfg.save(cfg_file)

    loaded = Config.load(cfg_file)
    assert loaded.concurrency == 3
    assert loaded.http_port == 9999


def test_update_valid_key():
    cfg = Config()
    cfg.update("concurrency", 5)
    assert cfg.concurrency == 5


def test_update_invalid_key():
    cfg = Config()
    with pytest.raises(KeyError):
        cfg.update("nonexistent_key", 42)


def test_to_dict():
    cfg = Config(concurrency=3)
    d = cfg.to_dict()
    assert d["concurrency"] == 3
    assert "output_dir" in d
    assert "audio_langs" in d
    # _path should not appear in the dict
    assert "_path" not in d


def test_tilde_expansion(tmp_path: Path):
    data = {"output_dir": "~/plex/output"}
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(data))
    cfg = Config.load(cfg_file)
    # output_dir is a Path; after expanduser() it should be absolute
    assert "~" not in str(cfg.output_dir)
    assert str(cfg.output_dir).startswith("/")
