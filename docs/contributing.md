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
uv run pytest                          # all tests
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
| `test_ws_integration.py` | Full WS round-trip: real server, real DB, command scenarios |
| `test_models.py` | Pydantic model round-trip for `Movie` / `TVSeries` / `Episode` / `Person` |
| `test_cli_paths.py` | `scrap-pub paths` offline subcommand |
| `test_downloader_output_root.py` | `task_output_root()` helper + per-task override resolution |
| `test_output_dir_validation.py` | `validate_task_output_dir`: parent missing, unwritable, low free space |
| `test_scheduler_db_run.py` | `db_run` / `net_run` kwargs forwarding via `functools.partial` |
| `test_scaffold_only.py` | Per-episode `scaffold(only=…)` scope + show-level short-circuit + description in `show.info.json` |
| `test_timespec.py` | `parse_since` local-midnight semantics, rolling offsets, ISO round-trip, invalid specs |

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
# 1. start the daemon — validate-fail cases should exit 2 cleanly, no restart loop
scrap-pub config --set website=""   # bad config
scrap-pub-server                    # expect: "website is not set ..." and exit 2
scrap-pub config --set website="https://example.com"

mv ~/.config/scrap-pub/cookies.txt ~/.config/scrap-pub/cookies.txt.bak  # missing cookies
scrap-pub-server                    # expect: "cookies file not found ..." and exit 2
mv ~/.config/scrap-pub/cookies.txt.bak ~/.config/scrap-pub/cookies.txt  # restore

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

### Per-task `--output-dir` smoke test

Covers the filesystem validation surface added for the "drop into a Plex
library directly" feature. Meant to fail loudly, not silently.

```bash
# 0. start the daemon as above

# 1. happy path: custom output directory honored end-to-end
mkdir -p /tmp/fake-plex/TV\ Shows
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir "/tmp/fake-plex/TV Shows"
# Wait for the task to finish, then confirm the Plex-ready tree was built
# under the custom path — NOT under ~/output.
tree /tmp/fake-plex/TV\ Shows
scrap-pub show <ID>           # 'output_dir' line should show the custom path

# 2. typo: parent missing → immediate error, no task row
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir "/mtn/plex/nope"
# expect: "Error: ... output directory parent does not exist: /mtn (typo?)"
scrap-pub list --since today | grep -c "/mtn"    # should be 0

# 3. unwritable: chmod 500, then enqueue into it
mkdir /tmp/locked-out
chmod 500 /tmp/locked-out
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir "/tmp/locked-out"
# expect: "Error: ... not writable: /tmp/locked-out"
chmod 700 /tmp/locked-out
rmdir /tmp/locked-out

# 4. low free space: temporarily crank min_free_space_gb very high
scrap-pub config --set min_free_space_gb=9999
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir /tmp/fake-plex
# expect: "Error: ... insufficient free space at /tmp/fake-plex: X.X GB free,
#          need 9999 GB"
scrap-pub config --set min_free_space_gb=10    # restore

# 5. re-enqueue conflict: same item, different --output-dir → loud failure
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir "/tmp/fake-plex/TV Shows"
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir "/tmp/fake-plex/Movies"
# expect: "Error: ... task N already exists with a different output_dir ...
#          Delete it first"

# 6. disappearing directory between enqueue and worker claim
scrap-pub pause
scrap-pub enqueue "https://example.com/item/view/121639/s1e1" \
  --output-dir /tmp/fake-plex/vanish
rm -rf /tmp/fake-plex/vanish/..   # remove the parent so the dir is gone
scrap-pub resume
# expect: task transitions to failed with a readable last_error;
#         `scrap-pub show <ID>` + web UI both display it.

# 7. add-sub on a custom-dir task drops the sidecar in the custom dir
scrap-pub add-sub <custom_dir_task_id> "https://example.com/path/to/sub.vtt" \
  --lang eng
find /tmp/fake-plex -name "*.eng.srt"   # should appear under the custom root
```

Web UI check: the enqueue form on the **Queue** tab has a second text field
for the optional output directory. Each task card should display a
`→ /path` tag under the title when the task was enqueued with one. Trigger
each of the errors above from the form to verify the toast surfaces the
daemon's error message verbatim.

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
