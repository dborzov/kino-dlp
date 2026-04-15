"""
server_main.py — Entry point for the scrap-pub-server command.

Usage:
    scrap-pub-server [--config PATH]
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="scrap-pub-server",
        description="Start the scrap-pub download daemon (HTTP + WebSocket + worker pool).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to config.json (default: ~/.config/scrap-pub/config.json)",
    )
    args = parser.parse_args()

    # Configure logging once, here, before any module emits a message.
    # When stdout/stderr are connected to the systemd journal (JOURNAL_STREAM
    # is set by systemd for services with StandardOutput/StandardError=journal),
    # journald adds its own timestamp, so we omit one. In every other context
    # (terminal, file redirect, etc.) we include it.
    under_journald = bool(os.environ.get("JOURNAL_STREAM"))
    fmt = (
        "%(levelname)-8s [%(name)s] %(message)s"
        if under_journald
        else "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    )
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=fmt)

    from .config import Config
    from .scheduler import main as scheduler_main

    config = Config.load(args.config)

    errors, warnings = config.validate()
    for w in warnings:
        log.warning("config: %s", w)
    if errors:
        log.critical("config: refusing to start — fix the following and retry:")
        for e in errors:
            log.critical("  %s", e)
        log.critical("config file: %s", config._cfg_path)
        sys.exit(2)

    # Cookies are required at startup: without them every download fails immediately.
    # Fail here with a clear message so systemd (or the operator) sees the problem
    # at launch rather than after the first task is claimed.
    from .session import check_cookies_file

    cookie_errors = check_cookies_file(config.cookies_path)
    if cookie_errors:
        log.critical("cookies: refusing to start — fix the following and retry:")
        for e in cookie_errors:
            log.critical("  %s", e)
        log.critical("config file: %s", config._cfg_path)
        sys.exit(2)

    try:
        asyncio.run(scheduler_main(config))
    except KeyboardInterrupt:
        sys.exit(0)
