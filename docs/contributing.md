# Contributing

> One-line summary: dev environment setup, how to run tests and lint, and how to verify an end-to-end change works.
>
> Last updated: 2026-04-13

For what the code does, see [spec.md](spec.md). For why it does it that way, see
[internals.md](internals.md).

---

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and ffmpeg.

```bash
git clone <repo>
cd scrap-pub
uv sync                       # creates .venv, installs deps + dev extras + editable package
uv pip install -e .           # one-time: puts scrap-pub / scrap-pub-server on $PATH
```

After the editable install the console scripts `scrap-pub` and `scrap-pub-server` resolve via `.venv/bin/` and can be invoked directly. On NTFS mounts the `.venv` shebangs can't be executed — fall back to `uv run scrap-pub …`.

### Cookies for local testing

Kino-style target sites typically have no public API — the scraper reuses cookies from
a logged-in browser session on whatever site the `website` config key points at. The
daemon reads them from a Netscape `cookies.txt` file (the same format yt-dlp, curl, and
wget use) at `~/.config/scrap-pub/cookies.txt`.

1. Set `website` in `~/.config/scrap-pub/config.json` to the base URL of your target
   site (e.g. `"https://example.com"`). Without this, nothing scrapes.
2. Log into that site in Chrome or Firefox.
3. Install a cookies.txt exporter — we recommend **Get cookies.txt LOCALLY**
   ([Chrome](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc),
   [Firefox](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt-one-click/)).
4. With the target site open, click the extension icon → **Export** → save the file.
5. Move the file into place and load it into the daemon:

   ```bash
   mv ~/Downloads/site_cookies.txt ~/.config/scrap-pub/cookies.txt
   # or, with the daemon running:
   scrap-pub cookies ~/Downloads/site_cookies.txt
   ```

The file **must** contain these cookies: `_identity`, `token`, `_csrf`, `PHPSESSID`,
`cf_clearance`. The daemon rejects uploads that are missing any of them.

**Never commit cookies.txt or any file containing real cookies.** `cookies.txt` and
`*.cookies` are gitignored.

---

## Running tests

```bash
uv run pytest                          # all tests (~31s, 101 tests)
uv run pytest tests/ -v                # verbose
uv run pytest tests/test_db.py -v      # one file
uv run pytest -k "test_enqueue"        # by name
```

Test coverage by file:

| File | Covers |
|------|--------|
| `test_config.py` | Config load/save/update, tilde expansion |
| `test_db.py` | Full SQLite schema: tasks, streams, logs, kv, queue summary, atomic claim |
| `test_ws_protocol.py` | `encode` / `decode` / `reply` helpers |
| `test_scraper_names.py` | `_sanitise`, `_dir_name`, `_episode_stem` (no network) |
| `test_ffmpeg.py` | `_parse_progress_line`, stall detection, process exit codes |
| `test_ws_integration.py` | Full WS round-trip: real server, real DB, 16 command scenarios |

Tests bind the WebSocket server to port 0 (random). See [internals.md § WebSocket server port in the sandbox](internals.md#websocket-server-port-in-the-sandbox).

## Lint

```bash
uv run ruff check scrap_pub/ tests/
uv run ruff check --fix scrap_pub/ tests/    # auto-fix
```

---

## Manual end-to-end test

Tests cover protocol and unit behavior. To verify a real download works you need real cookies.

```bash
# 1. start the daemon — validate-fail case should exit 2 cleanly
scrap-pub config --set website=""   # make it bad on purpose
scrap-pub-server                    # expect: "`website` is not set ..." and exit 2
scrap-pub config --set website="https://example.com"

# 2. start the daemon for real
scrap-pub-server

# 3. in another shell — enqueue a cheap item on your configured target site
scrap-pub enqueue "https://example.com/item/view/121639/s0e1"

# 4. watch progress — verbose list shows per-stream % and ETA for the active task
scrap-pub list --since today --verbose
scrap-pub show 1                       # full detail + timestamps + output size
scrap-pub logs --follow

# 5. sanity-check the queue with a quick SQL view
scrap-pub sql "SELECT id, status, enqueued_at, completed_at FROM tasks ORDER BY id DESC LIMIT 5"

# 6. confirm output
ls ~/output/
```

For UI work: open `http://localhost:8765` and exercise each tab while a task runs — progress bars, ETA, and per-task output size should update live via WebSocket. Enqueue an obviously-bad URL to verify the error toast appears (silent failure bug fix).

---

## Project layout

```
scrap_pub/                  package (importable)
  daemon/                   daemon code — see architecture.md for module map
  scrapers/                 placeholder for future site-specific scrapers
  models.py                 Pydantic models
tests/                      pytest suite
docs/                       you are here
skills/                     agent-facing skill guide
output/                     downloaded MKVs (gitignored)
tmp/                        ffmpeg working dir (gitignored)
raw/                        captured browser requests (gitignored)
```

---

## Conventions

- **Use the console scripts** — prefer `scrap-pub`/`scrap-pub-server` after `uv pip install -e .`. For other Python tasks (tests, scripts), use `uv run …`, never bare `python3`.
- **Add deps with uv** — `uv add <pkg>` / `uv add --dev <pkg>`, never hand-edit `pyproject.toml`
- **DB access only via `db_run`** — never call `db.*` from an async coroutine directly (see [internals.md § Threading invariant](internals.md#threading-invariant-db-calls-only-on-db_executor))
- **Forward kwargs via `functools.partial`** — `run_in_executor` drops them otherwise (see [internals.md § `db_run` and `functools.partial`](internals.md#db_run-and-functoolspartial))
- **No secrets in git** — `cookies.txt`, `*.cookies`, and `.env*` are gitignored. Double-check before committing.
- **Match existing style** — no linter config beyond ruff defaults; follow nearby code.

---

## Making a change

1. Read [architecture.md](architecture.md) and [internals.md](internals.md) for the area you're touching
2. Write or update a test if the behavior is observable
3. `uv run pytest` — all green
4. `uv run ruff check scrap_pub/ tests/` — clean
5. Manual smoke test if the change affects downloads or the UI
6. Commit with a message that explains **why**, not **what** — the diff already shows what
