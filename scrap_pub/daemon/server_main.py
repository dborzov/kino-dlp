"""
server_main.py — Entry point for the scrap-pub-server command.

Usage:
    scrap-pub-server [--config PATH]
"""

import argparse
import asyncio
import sys
from pathlib import Path


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

    from .config import Config
    from .scheduler import main as scheduler_main

    config = Config.load(args.config)
    try:
        asyncio.run(scheduler_main(config))
    except KeyboardInterrupt:
        sys.exit(0)
