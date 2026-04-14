# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working rules

- **Never overwrite `~/.config/scrap-pub/config.json`.** It holds the user's
  real, hand-tuned values (`website`, paths, language lists, concurrency, etc.).
  When a task requires changing a config value, use `scrap-pub config --set
  KEY=VALUE` so only that one key is updated and everything else in the file
  is preserved. Do **not** `Write` the file, do not `cp` a fresh default over
  it, and do not regenerate it from `Config()` defaults — that silently
  clobbers custom values and they have to be re-entered by hand.
- **Default `concurrency` is `2` and must stay `2`.** Higher values are not
  faster: total throughput is bounded by physical bandwidth, and more parallel
  sockets make the traffic pattern look obviously non-human to Cloudflare and
  trigger blocking / challenges. If you find yourself tempted to raise the
  default because "more is faster", stop — it isn't, and it's actively
  harmful. Only the user bumps it locally when they know what they're doing.

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

Use **uv** for all Python tasks. `uv sync` creates `.venv` and installs all deps
including the package itself in editable mode, which registers the `scrap-pub` and
`scrap-pub-server` console scripts. After a one-time `uv pip install -e .` (or
`uv tool install .` for a global install) the commands are on `$PATH` and can be
called directly. On NTFS mounts the `.venv` shebangs can't be executed — fall back
to `uv run scrap-pub …` instead.

```bash
# Install / sync dependencies
uv sync
uv pip install -e .          # makes scrap-pub / scrap-pub-server available

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_ws_integration.py -v

# Lint
uv run ruff check scrap_pub/ tests/

# Start the daemon (exits non-zero if the config fails validation)
scrap-pub-server

# Use the CLI (daemon must be running)
scrap-pub status
scrap-pub enqueue "https://example.com/item/view/..."
scrap-pub list --since week --verbose
scrap-pub show 42
scrap-pub sql "SELECT id, status FROM tasks ORDER BY id DESC LIMIT 10"

# Local path lookup (no daemon required)
scrap-pub paths               # prints output/tmp/db/cookies/config/website
scrap-pub paths output        # just the output dir — for `cd $(scrap-pub paths output)`

# Inspect an item page BEFORE enqueueing (no daemon required)
scrap-pub lookup "https://example.com/item/view/121936/s0e1"             # title/year/kind
scrap-pub lookup "https://example.com/item/view/30658/s10e4" --episodes  # + full episode list

# Install a new package
uv add <package>          # adds to pyproject.toml + syncs
uv add --dev <package>    # dev dependency
```

`scrap-pub list` accepts `--status`, `--kind`, `--since`, `--until`,
`--completed-since`, `--offset`, `-v/--verbose`, and `--json`.
`scrap-pub sql` is read-only by default (only `SELECT`/`WITH`/`PRAGMA`/`EXPLAIN`);
pass `--write` to run DML/DDL. The daemon refuses to start if `website` is
missing or malformed — see `Config.validate()` in `scrap_pub/daemon/config.py`.

## Session cookies

Kino-style sites typically have no public API — the scraper reuses cookies from a
logged-in browser session on the target site. Cookies live in a Netscape `cookies.txt`
file (same format as yt-dlp/curl/wget) at `~/.config/scrap-pub/cookies.txt` by default.
Override via `cookies_path` in config.

Required cookies: `_identity`, `token`, `_csrf`, `PHPSESSID`, `cf_clearance`.

The file can be produced with a browser extension ("Get cookies.txt LOCALLY" is the
one we document). To reload cookies into a running daemon:

```bash
scrap-pub cookies /path/to/cookies.txt
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

The output **root** is `config.output_dir` by default, but every task can
override it with `scrap-pub enqueue URL --output-dir PATH`. The inside-the-
root layout is identical either way — the override only swaps the top level.
Use this to drop content directly into a Plex library directory (or any other
media-server watched folder) without mutating the daemon-wide config. The
per-task override is stored on `tasks.output_dir` and persists across daemon
restarts. **Never** `config --set output_dir=...` as a workaround for a
single task — that changes the default for every other queued task too.

```
# Movie (default root)
output/Hoppers(2026)/Hoppers(2026).mkv

# TV episode (default root)
output/Big Mistakes(2026)/Season 01/Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.mkv

# Sidecar subtitles (same dir as MKV)
output/Big Mistakes(2026)/Season 01/Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.rus.srt

# Per-task override: scrap-pub enqueue URL --output-dir "/mnt/plex/TV Shows"
/mnt/plex/TV Shows/Big Mistakes(2026)/Season 01/...
```

See `docs/docs.md` for the docs index.
See `docs/architecture.md` for system map and SQLite schema.
See `docs/spec.md` for config, CLI reference, and WebSocket protocol.
See `docs/internals.md` for implementation rationale and gotchas.
See `docs/contributing.md` for dev setup and manual end-to-end testing.
See `docs/site_scraping_reference.md` for target-site structure conventions and HTML/HLS reference.
See `skills/scrappub_skill.md` for the agent-facing daemon workflow.
