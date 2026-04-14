"""Tests for `scrap-pub paths` — the local path-lookup subcommand.

The point of these tests is to guarantee the command works **without** the
daemon: it should resolve everything from the config file on disk and never
open a WebSocket. Agents rely on that for recipes like
`cd $(scrap-pub paths output)`.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scrap_pub.daemon.cli_main import _PATH_KEYS, _build_parser, cmd_paths
from scrap_pub.daemon.config import Config


def _make_config(tmp_path: Path) -> Path:
    """Write a minimal config.json under tmp_path and return its path."""
    data = {
        "website":      "https://example.com",
        "output_dir":   str(tmp_path / "out"),
        "tmp_dir":      str(tmp_path / "tmp"),
        "db_path":      str(tmp_path / "queue.db"),
        "cookies_path": str(tmp_path / "cookies.txt"),
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(data))
    return cfg_file


def test_path_keys_cover_expected_values():
    # Guard against silent drift if someone adds/removes a key.
    assert set(_PATH_KEYS) == {
        "output", "tmp", "db", "cookies", "config", "website",
    }


def test_cmd_paths_all(tmp_path: Path, capsys):
    cfg = Config.load(_make_config(tmp_path))
    parser = _build_parser()
    args = parser.parse_args(["paths"])
    cmd_paths(args, cfg)
    out = capsys.readouterr().out
    for key in _PATH_KEYS:
        assert key in out, f"key {key!r} missing from `paths` output"
    assert str(tmp_path / "out") in out
    assert str(tmp_path / "tmp") in out
    assert "https://example.com" in out


@pytest.mark.parametrize("key,attr", list(_PATH_KEYS.items()))
def test_cmd_paths_single_key(tmp_path: Path, capsys, key: str, attr: str):
    cfg = Config.load(_make_config(tmp_path))
    parser = _build_parser()
    args = parser.parse_args(["paths", key])
    cmd_paths(args, cfg)
    out = capsys.readouterr().out.strip()
    assert out == str(getattr(cfg, attr)), (
        f"paths {key!r} should echo exactly the config value (got {out!r})"
    )


def test_cli_paths_runs_without_daemon(tmp_path: Path):
    """End-to-end: spawn the CLI and verify it doesn't need the daemon.

    We point `--config` at a throwaway file and use a non-routable ws port so
    any accidental connection attempt would fail fast. The command must still
    exit 0 and print the configured output dir.
    """
    cfg_file = _make_config(tmp_path)
    # Also pin the ports to something unusable so a regression that tries to
    # open a WS connection would surface as a ConnectionRefusedError.
    data = json.loads(cfg_file.read_text())
    data["ws_port"] = 1  # privileged port — connect will be refused
    data["http_port"] = 2
    cfg_file.write_text(json.dumps(data))

    # Invoke through `python -c` so we don't depend on the console-script
    # entry point being on $PATH in the test environment.
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from scrap_pub.daemon.cli_main import main; main()",
            "--config", str(cfg_file),
            "paths", "output",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert result.stdout.strip() == str(tmp_path / "out")
