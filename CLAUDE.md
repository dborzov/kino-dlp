# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`scrap-pub` is a generic download pipeline for kino-style, on-demand video websites. It
scrapes movie and TV series metadata, downloads HLS streams via ffmpeg, and assembles
Plex-ready MKV files. Target sites are kino-style: cookie-based session auth, no public
API, behind Cloudflare.

The repo ships **with no default target site**. Users configure the `website` key in
`~/.config/scrap-pub/config.json` to point at whatever on-demand site they want to use,
and bear sole responsibility for that choice and their compliance with the site's terms
of service.

## Commands

Use **uv** for all Python tasks. `uv sync` creates `.venv` and installs all deps including
the package itself in editable mode (entry points available as `.venv/bin/scrap-pub*`).

On NTFS mounts the `.venv` scripts can't be executed directly — always invoke via `uv run`:

```bash
# Install / sync dependencies
uv sync

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_ws_integration.py -v

# Lint
uv run ruff check scrap_pub/ tests/

# Start the daemon
uv run python -m scrap_pub.daemon.server_main

# Use the CLI (daemon must be running)
uv run python -m scrap_pub.daemon.cli_main status
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/..."
uv run python -m scrap_pub.daemon.cli_main list

# Install a new package
uv add <package>          # adds to pyproject.toml + syncs
uv add --dev <package>    # dev dependency
```

## Session cookies

Kino-style sites typically have no public API — the scraper reuses cookies from a
logged-in browser session on the target site. Cookies live in a Netscape `cookies.txt`
file (same format as yt-dlp/curl/wget) at `~/.config/scrap-pub/cookies.txt` by default.
Override via `cookies_path` in config.

Required cookies: `_identity`, `token`, `_csrf`, `PHPSESSID`, `cf_clearance`.

The file can be produced with a browser extension ("Get cookies.txt LOCALLY" is the
one we document). To reload cookies into a running daemon:

```bash
uv run python -m scrap_pub.daemon.cli_main cookies /path/to/cookies.txt
```

## Architecture

```
scrap_pub/
  models.py           — Pydantic models: MediaBase, Movie, TVSeries, Person, Episode
  scrapers/           — placeholder package for future site-specific scraper modules
  daemon/
    config.py         — Config dataclass, load/save/update (~/.config/scrap-pub/config.json)
    db.py             — SQLite schema + all CRUD functions (WAL mode)
    session.py        — curl-cffi session backed by ~/.config/scrap-pub/cookies.txt
    scraper.py        — scrape(), get_manifest_url(), parse_manifest(), scaffold(), select_streams()
    ffmpeg.py         — run_ffmpeg(), _parse_progress_line(), StallError, merge/remux helpers
    downloader.py     — download_task(), add_audio_to_task(), add_sub_to_task()
    scheduler.py      — AppState, db_run/net_run, scheduler_loop, worker_task, broadcaster, main()
    ws_protocol.py    — CMD_*/EVT_* constants, encode/decode/reply helpers
    ws_server.py      — serve_ws(), ws_handler(), broadcast(), command dispatch
    server_http.py    — stdlib HTTPServer: GET / → web UI, GET /health → {"ok":true}
    ui.py             — HTML_UI: single-file web UI (5 tabs, per-stream progress bars)
    server_main.py    — scrap-pub-server entry point
    cli_main.py       — scrap-pub CLI: all subcommands via WebSocket
tests/
docs/
  docs.md             — index of all docs (start here)
  architecture.md     — system map, process model, SQLite schema, WS topology
  spec.md             — config, CLI, WebSocket protocol, download flow, output layout
  internals.md        — implementation rationale, tradeoffs, gotchas
  contributing.md     — dev setup, tests, lint, manual E2E
  site_scraping_reference.md — target-site structure conventions, HTML selectors, HLS manifest format
skills/
  scrappub_skill.md   — agent skill guide for the daemon
output/               — gitignored: downloaded MKVs
tmp/                  — gitignored: ffmpeg working directory
raw/                  — gitignored: captured browser requests, reference material
```

## Key implementation details

- **`db_run` kwargs**: `run_in_executor` can't forward `**kwargs`. Downloader uses
  `functools.partial(fn, **kwargs)` to bind them before passing to the executor.
- **Config save path**: `Config._cfg_path` tracks which file was loaded. `save()` and
  `update()` write to that path, not the global default. Tests must use `Config.load(tmp_path)`.
- **Stream resume**: Downloader checks stream `status` — only `pending` streams are re-downloaded.
  `done` streams are skipped. A task can be re-run safely.
- **SQLite UNIQUE index**: `CREATE UNIQUE INDEX ON streams(task_id, stream_type, COALESCE(label, ''))`.
  Inline UNIQUE with COALESCE is rejected by SQLite — must be a separate CREATE INDEX.
- **Movie tasks**: use `season=0, episode=1` as NOT-NULL sentinels for the UNIQUE constraint.
- **WS server port**: In Claude Code sandbox, `websockets.asyncio.server.serve` hangs on
  fixed ports. Tests use `port=0` (random). This is a sandbox network restriction, not a bug.

## Output naming conventions

All output paths use the **original-language title** (French for French films, English for English
shows, Russian for Russian shows) from the target site's `og:title`. The helpers live inline in
`scrap_pub/daemon/scraper.py`.

```
# Movie
output/Hoppers(2026)/Hoppers(2026).mkv

# TV episode
output/Big Mistakes(2026)/Season 01/Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.mkv

# Sidecar subtitles (same dir as MKV)
output/Big Mistakes(2026)/Season 01/Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.rus.srt
```

See `docs/docs.md` for the docs index.
See `docs/architecture.md` for system map and SQLite schema.
See `docs/spec.md` for config, CLI reference, and WebSocket protocol.
See `docs/internals.md` for implementation rationale and gotchas.
See `docs/contributing.md` for dev setup and manual end-to-end testing.
See `docs/site_scraping_reference.md` for target-site structure conventions and HTML/HLS reference.
See `skills/scrappub_skill.md` for the agent-facing daemon workflow.
