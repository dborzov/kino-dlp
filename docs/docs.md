# scrap-pub documentation index

> One-line summary: map of every doc in this directory and who it's for.
>
> Last updated: 2026-04-14

Docs live in `docs/`. Each file covers one concern. Start here; jump to what you need.

## Core docs

| File | Audience | What's in it |
|------|----------|--------------|
| [architecture.md](architecture.md) | Anyone wiring scrap-pub into something | System map, process model, SQLite schema, WebSocket topology, data flow |
| [spec.md](spec.md) | Users and integrators | Config schema, CLI reference, WebSocket protocol, download flow, error handling, output layout |
| [internals.md](internals.md) | Contributors and future-me | Why the code looks the way it does — tradeoffs, rationale, known gotchas |
| [contributing.md](contributing.md) | New contributors | Dev setup with uv, tests, lint, manual end-to-end testing |

## Reference docs (external)

| File | What's in it |
|------|--------------|
| [site_scraping_reference.md](site_scraping_reference.md) | Target-site structure conventions, HTML selectors, HLS manifest format |
| [plex_naming.md](plex_naming.md) | Plex media naming conventions (cached copy of Plex support article) |
| [local_plex_files.md](local_plex_files.md) | Plex local media assets article (cached) |

## Agent skill guides

Not in `docs/` but worth knowing:

- [`skills/scrappub_skill.md`](../skills/scrappub_skill.md) — How an AI agent (Claude Code, OpenClaw) uses the daemon as a tool
- [`skills/scrappub_sql_skill.md`](../skills/scrappub_sql_skill.md) — Schema reference and recipes for `scrap-pub sql`, the read-only-by-default SQL escape hatch

## Architecture decision records

`docs/decisions/` is reserved for ADRs. None yet — add one when a tradeoff isn't obvious from the code and would surprise a reader six months from now.
