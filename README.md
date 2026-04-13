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
uv sync            # creates .venv, installs all dependencies
```

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
   uv run python -m scrap_pub.daemon.cli_main cookies ~/Downloads/site_cookies.txt
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
uv run python -m scrap_pub.daemon.server_main

# Or with a custom config file
uv run python -m scrap_pub.daemon.server_main --config /path/to/config.json
```

On first start, a config file is created at `~/.config/scrap-pub/config.json` with defaults.
If you already dropped `cookies.txt` at `~/.config/scrap-pub/cookies.txt` (see
[§ Get your cookies](#get-your-cookies)) the daemon is ready to go; otherwise
open the **web UI** at `http://localhost:8765` → Settings and paste your
`cookies.txt` contents there.

---

## Usage — CLI

All CLI commands connect to the running daemon via WebSocket.

```bash
# Status
uv run python -m scrap_pub.daemon.cli_main status

# Enqueue downloads (URLs on the target site you configured via `website`)
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/121639/s0e1"  # movie
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/122266/s1e1"  # one episode
uv run python -m scrap_pub.daemon.cli_main enqueue "https://example.com/item/view/122266"        # all episodes

# Monitor
uv run python -m scrap_pub.daemon.cli_main list
uv run python -m scrap_pub.daemon.cli_main logs --follow
uv run python -m scrap_pub.daemon.cli_main logs --task 42 --follow

# Control
uv run python -m scrap_pub.daemon.cli_main pause
uv run python -m scrap_pub.daemon.cli_main resume
uv run python -m scrap_pub.daemon.cli_main retry 42
uv run python -m scrap_pub.daemon.cli_main skip 42

# Cookies — Netscape cookies.txt (yt-dlp format)
uv run python -m scrap_pub.daemon.cli_main cookies ~/Downloads/site_cookies.txt

# Config
uv run python -m scrap_pub.daemon.cli_main config
uv run python -m scrap_pub.daemon.cli_main config --set concurrency=4
uv run python -m scrap_pub.daemon.cli_main config --set video_quality=highest
uv run python -m scrap_pub.daemon.cli_main config --set output_dir="/mnt/plex/movies"
```

After `uv sync`, entry points are also in `.venv/bin/scrap-pub` and `.venv/bin/scrap-pub-server`.
On NTFS mounts (`.venv` on Windows drives from Linux), use `uv run python -m ...` instead.

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
