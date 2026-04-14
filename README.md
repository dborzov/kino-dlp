# scrap-pub

Download daemon and CLI for kino-style, on-demand video websites. Add links to its queue
from the target site; the daemon then scrapes metadata, downloads HLS streams via ffmpeg,
and assembles Plex-ready MKV files with embedded audio tracks and sidecar subtitles in a
way that simulates live user traffic and doesn't raise any flags (Cloudflare bot check or
otherwise).

> **Note:** this project ships with no default target site. It is a generic toolkit for the
> category of kino-style, on-demand video websites. Whether you point it at a particular
> site, and your compliance with that site's terms of service, is entirely your own
> responsibility. See [Configuration](#configuration) — the `website` config key is
> empty by default and you must set it yourself.


---

## Features

- Persistent SQLite queue — survives restarts, resumes interrupted downloads at stream level
- Parallel workers — configurable concurrency (default: 2)
- Plex-ready output — correct directory structure, episode naming, poster art, sidecar `.srt` subtitles
- Web UI — live progress bars per stream, queue management, cookie upload
- CLI — all operations via short-lived WebSocket commands
- Cookie expiry handling — automatic pause + resume when session cookies are refreshed
- Post-hoc audio/subtitle addition — remux extra tracks into existing MKVs without re-encoding

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — Python package manager
- [ffmpeg](https://ffmpeg.org/) — HLS download and MKV merge
- An account on your chosen target site with valid session cookies

---

## Installation

```bash
git clone https://github.com/yourusername/scrap-pub.git
cd scrap-pub
uv sync                 # creates .venv, installs all dependencies
uv pip install -e .     # registers scrap-pub / scrap-pub-server on $PATH
```

After the editable install the console scripts `scrap-pub` and
`scrap-pub-server` resolve via `.venv/bin/`. On NTFS mounts where `.venv`
shebangs can't be executed, fall back to `uv run scrap-pub …`.

### Get your cookies

Kino-style, on-demand video sites typically have no public API and sit behind
Cloudflare, so scrap-pub reuses the cookies from a logged-in browser session. It
reads them from a **Netscape `cookies.txt`** file — the same format used by
[yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp),
`curl`, and `wget` — at `~/.config/scrap-pub/cookies.txt`.

1. **Log into your target site** in Chrome or Firefox with the account you want
   scrap-pub to use.
2. **Install a cookies.txt exporter** — we recommend
   **Get cookies.txt LOCALLY**:
   - [Chrome Web Store](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt-one-click/)
3. **Export**: with the target site open in the active tab, click the extension
   icon → **Export** (Netscape format, current site). It saves a
   `*_cookies.txt` file to your Downloads folder.
4. **Install** the file one of two ways:

   ```bash
   # Option A — drop it at the default path (daemon reads it on startup)
   mkdir -p ~/.config/scrap-pub
   mv ~/Downloads/site_cookies.txt ~/.config/scrap-pub/cookies.txt

   # Option B — hot-reload into a running daemon (validates + auto-resumes)
   scrap-pub cookies ~/Downloads/site_cookies.txt
   ```

The file **must** contain all five of these cookies (the daemon rejects uploads
that are missing any):

| Cookie | What it is |
|--------|------------|
| `_identity` | long-lived identity token on the target site |
| `token` | session token paired with `_identity` |
| `_csrf` | Yii2 CSRF protection token |
| `PHPSESSID` | PHP session id |
| `cf_clearance` | Cloudflare "passed the JS challenge" clearance cookie |

`cf_clearance` expires after a few days; the session cookies last longer but
get invalidated by password changes or manual logout. When that happens the
daemon detects the 403, pauses workers, and shows a `cookie_error` banner —
just re-export and run `scrap-pub cookies FILE` again.

**The cookies file is as sensitive as a password.** `cookies.txt` and
`*.cookies` are in `.gitignore`. Never commit it.

---

## Usage — Daemon

```bash
# Start the daemon (runs until Ctrl-C)
scrap-pub-server

# Or with a custom config file
scrap-pub-server --config /path/to/config.json
```

On first start, a config file is created at `~/.config/scrap-pub/config.json`
with defaults. The daemon validates the config at boot and **exits with code
2** if anything critical is missing or malformed (unset `website`, bad URL
scheme, unwritable `output_dir`/`tmp_dir`/`db_path`, port collisions, …). The
missing-cookies case is a warning, not an error — the daemon still starts and
the UI/CLI prompt you to upload.

If you already dropped `cookies.txt` at `~/.config/scrap-pub/cookies.txt` (see
[§ Get your cookies](#get-your-cookies)) the daemon is ready to go; otherwise
open the **web UI** at `http://localhost:8765` → Settings and paste your
`cookies.txt` contents there.

---

## Usage — CLI

Most CLI commands connect to the running daemon via WebSocket. A few (`paths`, `lookup`) run entirely client-side and work without the daemon.

```bash
# Status
scrap-pub status

# Inspect a URL BEFORE enqueueing — title, year, movie-vs-TV-show.
# Runs locally (no daemon); reads cookies from ~/.config/scrap-pub/cookies.txt.
scrap-pub lookup "https://example.com/item/view/121639/s0e1"
scrap-pub lookup "https://example.com/item/view/122266/s1e1" --episodes  # full breakdown

# Enqueue downloads (URLs on the target site you configured via `website`)
scrap-pub enqueue "https://example.com/item/view/121639/s0e1"   # movie
scrap-pub enqueue "https://example.com/item/view/122266/s1e1"   # one episode
scrap-pub enqueue "https://example.com/item/view/122266"        # all episodes

# Monitor
scrap-pub list                                 # most recent 50
scrap-pub list --since week --kind movie -v    # filtered + per-stream progress
scrap-pub list --status failed --json          # machine-readable
scrap-pub show 42                              # full detail for a single task
scrap-pub logs --follow
scrap-pub logs --task 42 --follow

# Control
scrap-pub pause
scrap-pub resume
scrap-pub retry 42
scrap-pub skip 42

# Cookies — Netscape cookies.txt (yt-dlp format)
scrap-pub cookies ~/Downloads/site_cookies.txt

# Ad-hoc SQL against the queue DB (read-only by default)
scrap-pub sql "SELECT id, status, enqueued_at FROM tasks ORDER BY id DESC LIMIT 10"
scrap-pub sql "SELECT COUNT(*) FROM tasks GROUP BY status" --json
scrap-pub sql "UPDATE tasks SET status='pending' WHERE id=42" --write

# Config
scrap-pub config
scrap-pub config --set concurrency=4
scrap-pub config --set video_quality=highest
scrap-pub config --set output_dir="/mnt/plex/movies"

# Paths — echo resolved paths from config (works without the daemon)
scrap-pub paths                  # list output/tmp/db/cookies/config/website
scrap-pub paths output           # just the value, for shell substitution
cd "$(scrap-pub paths output)"   # jump to the download directory
ls  "$(scrap-pub paths tmp)"     # peek at ffmpeg's working files
```

`scrap-pub list` accepts `--status`, `--kind`, `--since`, `--until`,
`--completed-since`, `--limit`, `--offset`, `-v/--verbose`, and `--json`. Time
SPECs are human-friendly: `today`, `yesterday`, `week`, `month`, `7d`, `24h`,
`30m`, or an ISO timestamp. `scrap-pub show` takes a positional task id and
prints timestamps, attempts, output size, and (for active tasks) live
per-stream progress with ETA.

`scrap-pub sql` rejects anything that isn't `SELECT` / `WITH` / `PRAGMA` /
`EXPLAIN` unless `--write` is passed, and caps results at `--limit` rows
(default 1000). See [`skills/scrappub_sql_skill.md`](skills/scrappub_sql_skill.md)
for the schema and recipe collection.

After `uv pip install -e .` the entry points are on `$PATH`. On NTFS mounts
(`.venv` on Windows drives from Linux), the `.venv` shebangs can't be executed —
use `uv run scrap-pub …` as a fallback.

---

## Configuration

Config file: `~/.config/scrap-pub/config.json` (created with defaults on first run).

| Key | Default | Description |
|-----|---------|-------------|
| `website` | `""` (unset) | Base URL of the target site (e.g. `https://example.com`). **You must set this** — nothing scrapes until it's configured. |
| `output_dir` | `<project>/output` | Where finished MKVs are written |
| `tmp_dir` | `<project>/tmp` | Working dir for in-progress downloads |
| `db_path` | `~/.local/share/scrap-pub/queue.db` | SQLite queue database |
| `cookies_path` | `~/.config/scrap-pub/cookies.txt` | Netscape cookies.txt (yt-dlp format) |
| `concurrency` | `2` | Parallel download workers |
| `video_quality` | `lowest` | `lowest` \| `highest` \| `720p` \| `1080p` |
| `audio_langs` | `["RUS","ENG","FRE"]` | Audio tracks to embed in MKV |
| `sub_langs` | `["rus","eng","fra"]` | Subtitle languages to download as sidecar `.srt` |
| `stall_timeout_sec` | `300` | Seconds without ffmpeg progress before retry |
| `http_port` | `8765` | Web UI port |
| `ws_port` | `8766` | WebSocket control port |

---

## Output Structure

```
{output_dir}/
  Hoppers(2026)/
    Hoppers(2026).mkv          ← video + embedded RUS/ENG audio
    Hoppers(2026).rus.srt      ← sidecar subtitle (if available)
    Hoppers(2026).jpg          ← poster
    Hoppers(2026).info.json    ← scraped metadata

  Big Mistakes(2026)/
    Season 01/
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.mkv
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.rus.srt
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.eng.srt
    poster.jpg
    show.info.json
    thumbnails/
      s01e01.jpg
```

Naming follows [Plex media naming conventions](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/).
Titles use the **original language** (English for English shows, Russian for Russian shows, French for
French films, etc.) from the target site's `og:title` metadata.

---

## Development

```bash
uv sync              # install all deps including dev extras
uv run pytest        # run tests (101 tests, ~31s)
uv run ruff check scrap_pub/ tests/   # lint
```

Test suite: config, SQLite CRUD, WebSocket protocol, scraper name helpers, ffmpeg progress
parsing and stall detection, full WS round-trip with real in-process server.

---

## Documentation

Docs live in [`docs/`](docs/docs.md). Start with the index, or jump directly:

- [`docs/architecture.md`](docs/architecture.md) — system map, process model, SQLite schema, WebSocket topology
- [`docs/spec.md`](docs/spec.md) — config, CLI reference, WebSocket protocol, download flow, output layout
- [`docs/internals.md`](docs/internals.md) — implementation rationale, tradeoffs, gotchas
- [`docs/contributing.md`](docs/contributing.md) — dev setup, tests, lint, manual end-to-end testing

See [`skills/scrappub_skill.md`](skills/scrappub_skill.md) for an AI agent skill guide — how
to use scrap-pub as a tool from Claude Code or other AI assistants.

---

## License

MIT
