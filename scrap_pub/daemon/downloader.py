"""
downloader.py — Per-task download orchestration.

download_task(task, state)
  Full flow for one task: manifest → streams → ffmpeg → merge → cleanup.
  Streams are tracked individually in the DB (granular resume support).

add_audio_to_task(task_id, url, label, state)
  Download an extra audio track and remux into the existing MKV.

add_sub_to_task(task_id, url, lang, state)
  Download an extra subtitle and save as a .srt sidecar.
"""

import logging
import shutil
from pathlib import Path

_log = logging.getLogger(__name__)

from .config import (
    Config,
    OutputDirError,
    estimate_min_free_gb,
    validate_task_output_dir,
)
from .db import (
    db_get_item,
    db_get_streams,
    db_get_task,
    db_increment_attempts,
    db_log,
    db_set_task_status,
    db_update_stream,
    db_upsert_stream,
)
from .ffmpeg import (
    StallError,
    clean_track_name,
    download_audio_stream,
    download_subtitle_stream,
    download_video_stream,
    lang_from_sub_url,
    lang_from_track_name,
    merge_into_mkv,
    remux_add_audio,
)
from .scraper import (
    CookieExpiredError,
    get_manifest_url,
    parse_manifest,
    scaffold,
    select_streams,
)
from .ws_protocol import EVT_STREAM_PROGRESS, EVT_STREAM_UPDATE, EVT_TASK_ERROR, EVT_TASK_UPDATE

MAX_ATTEMPTS = 3

# Fields captured into AppState.stream_progress so CLI list/show can read live
# telemetry without hitting the DB on every ffmpeg tick.
_LIVE_FIELDS = ("pct", "speed", "eta_sec", "elapsed_sec", "size_bytes")


class TaskFSError(RuntimeError):
    """Filesystem error during a task, tagged with path + operation.

    Wrap raw OSError with this so the worker-level `except Exception` surfaces
    a message that tells the reviewer *which operation failed and where*,
    rather than a bare `[Errno 28] No space left on device`.
    """

    def __init__(self, op: str, path: Path, cause: OSError):
        msg = f"{op} {path}: {cause.strerror or cause}"
        super().__init__(msg)
        self.op = op
        self.path = path
        self.cause = cause


def task_output_root(task: dict, config: Config) -> Path:
    """Resolve the effective output root for a task.

    If the task row carries a non-empty `output_dir`, that wins (per-task
    override). Otherwise fall back to the daemon-wide `config.output_dir`.
    """
    raw = task.get("output_dir") if isinstance(task, dict) else None
    if raw:
        return Path(raw).expanduser()
    return Path(config.output_dir)


def _safe_mkdir(path: Path, op: str) -> None:
    """mkdir(parents=True, exist_ok=True) wrapped in TaskFSError for context."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise TaskFSError(op, path, e) from e


def _emit_progress(state, msg: dict) -> None:
    """Broadcast a stream-progress event and mirror it into the live cache."""
    sid = msg.get("stream_id")
    if sid is not None:
        live = {k: msg[k] for k in _LIVE_FIELDS if k in msg}
        if live:
            state.stream_progress[sid] = live
    state.progress_queue.put_nowait(msg)


def _clear_live(state, stream_id) -> None:
    """Drop the live-progress cache entry once a stream settles."""
    if stream_id is not None:
        state.stream_progress.pop(stream_id, None)


async def download_task(task: dict, state) -> None:
    """
    Main download coroutine for one task.
    Handles resume, per-stream progress, retry logic.
    """
    from .db import db_set_cookie_error, db_set_paused
    from .scheduler import db_run, net_run
    from .ws_server import broadcast

    task_id = task["id"]
    config  = state.config
    conn    = state.conn

    # Build a short human-readable label for every log line emitted by this task.
    # plex_stem carries the full "Show(Year) - s01e03 - Title" slug when available.
    _title = task.get("plex_stem") or task.get("episode_title") or task.get("kind", "?")
    _prefix = f"[task {task_id} | {_title}]"

    def log(level: str, msg: str):
        state.loop.create_task(
            db_run(state, db_log, conn, level, msg, task_id)
        )
        full = f"{_prefix} {msg}"
        if level == "ERROR":
            _log.error(full)
        elif level == "WARN":
            _log.warning(full)
        else:
            _log.info(full)

    log("INFO", f"Starting task — {task.get('plex_stem') or task.get('episode_title') or task['kind']}")

    # ── 0a. Resolve + re-validate the effective output root ──────────────────
    # The dir may have been unmounted / chmod'd / run out of space between
    # enqueue and claim. Re-probe here so we fail fast with a clear message
    # instead of crashing mid-ffmpeg.
    output_root = task_output_root(task, config)
    try:
        output_root = validate_task_output_dir(output_root, config.min_free_space_gb)
    except OutputDirError as e:
        log("ERROR", f"Output directory unusable: {e}")
        await db_run(state, db_set_task_status, conn, task_id, "failed", error=str(e))
        await broadcast(state, {"type": EVT_TASK_UPDATE, "task_id": task_id,
                                "status": "failed", "error": str(e)})
        return

    # ── 0b. Check if output already exists ────────────────────────────────────
    mkv_path = output_root / f"{task['plex_stem']}.mkv"
    if mkv_path.exists() and mkv_path.stat().st_size > 0:
        log("INFO", f"Output already exists: {mkv_path.name}")
        await db_run(state, db_set_task_status, conn, task_id, "done",
                     mkv_path=str(mkv_path))
        await broadcast(state, {"type": EVT_TASK_UPDATE, "task_id": task_id,
                                "status": "done", "mkv_path": str(mkv_path)})
        return

    # ── 1. Get item metadata ───────────────────────────────────────────────────
    item = await db_run(state, db_get_item, conn, task["item_id"])
    if not item:
        log("ERROR", "Item not found in DB — this should not happen")
        await db_run(state, db_set_task_status, conn, task_id, "failed",
                     error="Item not in DB")
        return

    # ── 2. Get fresh manifest ─────────────────────────────────────────────────
    log("INFO", f"Resolving manifest for S{task['season']:02d}E{task['episode']:02d}...")
    try:
        manifest_url, ep_info = await net_run(
            state, get_manifest_url,
            task["item_id"], task["season"], task["episode"]
        )
        manifest = await net_run(state, parse_manifest, manifest_url)
    except CookieExpiredError as e:
        log("ERROR", f"Cookie expired: {e}")
        await db_run(state, db_set_cookie_error, conn, True)
        await db_run(state, db_set_paused, conn, True)
        state.pause_event.clear()
        attempts = await db_run(state, db_increment_attempts, conn, task_id)
        from .ws_protocol import EVT_COOKIE_ERROR
        await broadcast(state, {
            "type": EVT_COOKIE_ERROR,
            "msg": "Session cookies expired — upload new cookies to resume."
        })
        return
    except Exception as e:
        log("ERROR", f"Manifest fetch failed: {e}")
        attempts = await db_run(state, db_increment_attempts, conn, task_id)
        if attempts >= MAX_ATTEMPTS:
            await db_run(state, db_set_task_status, conn, task_id, "failed", error=str(e))
        await broadcast(state, {
            "type": EVT_TASK_ERROR, "task_id": task_id,
            "error": str(e), "attempt": attempts,
            "will_retry": attempts < MAX_ATTEMPTS,
        })
        return

    # ── 3. Select streams per config ──────────────────────────────────────────
    selected = select_streams(manifest, config)
    video    = selected["video"]
    audios   = selected["audio"]
    subs     = selected["subtitles"]

    log("INFO", f"Streams: video={'yes' if video else 'none'} "
        f"audio={len(audios)} sub={len(subs)}")

    # ── 4. Scaffold Plex dirs ─────────────────────────────────────────────────
    # For a series episode, narrow scaffold to just this episode — we don't
    # want a re-download of one episode to (re)emit info.json + thumbnails for
    # every other episode of the season, or to re-write show.info.json when
    # it already exists on disk.
    import functools as _ft
    scaffold_only: tuple[int, int] | None = None
    if task["kind"] == "episode":
        scaffold_only = (int(task["season"]), int(task["episode"]))
    try:
        await net_run(
            state,
            _ft.partial(
                scaffold, item_from_meta(item), output_root, only=scaffold_only,
            ),
        )
    except Exception as e:
        log("WARN", f"Scaffold error (non-fatal): {e}")

    # ── 5. Create/ensure stream rows in DB ───────────────────────────────────
    work_dir = Path(config.tmp_dir) / Path(task["plex_stem"]).name
    try:
        _safe_mkdir(work_dir, "creating work dir")
    except TaskFSError as e:
        log("ERROR", str(e))
        await db_run(state, db_set_task_status, conn, task_id, "failed", error=str(e))
        return

    duration_sec = ep_info.get("duration") or ep_info.get("duration_sec")

    # Duration-based refinement of the free-space floor — best-effort advisory.
    # Skipped when duration is unknown. Uses the *already-validated* root, so
    # we can assume the path exists and is writable.
    refined_min_gb = estimate_min_free_gb(duration_sec, config.min_free_space_gb)
    if refined_min_gb > config.min_free_space_gb:
        try:
            validate_task_output_dir(output_root, refined_min_gb)
        except OutputDirError as e:
            log("ERROR", f"Output directory is too small for this task: {e}")
            await db_run(state, db_set_task_status, conn, task_id, "failed", error=str(e))
            return

    # Register streams in DB (INSERT OR IGNORE preserves existing done/downloading ones)
    stream_ids: dict[str, int] = {}

    if video:
        sid = await db_run(state, db_upsert_stream, conn,
            task_id=task_id, stream_type="video",
            label=f"Video {video.get('resolution', '')}",
            lang=None, forced=False,
            source_url=video["uri"],
            tmp_path=str(work_dir / "video.mkv"),
        )
        stream_ids["video"] = sid

    for i, at in enumerate(audios):
        label = at.get("name", f"Audio {i+1}")
        sid = await db_run(state, db_upsert_stream, conn,
            task_id=task_id, stream_type="audio",
            label=label,
            lang=lang_from_track_name(label) or at.get("language"),
            forced=False,
            source_url=at["uri"],
            tmp_path=str(work_dir / f"audio_{i+1}.m4a"),
        )
        stream_ids[f"audio_{i}"] = sid

    for i, st in enumerate(subs):
        lang = st.get("language", "und")
        label = st.get("name", lang)
        # Sub sidecars go directly to output dir
        out_stem = output_root / task["plex_stem"]
        lang_idx = i + 1
        out_path = str(out_stem.parent / f"{out_stem.name}.{lang}.srt") if lang_idx == 1 \
                   else str(out_stem.parent / f"{out_stem.name}.{lang}.{lang_idx}.srt")
        sid = await db_run(state, db_upsert_stream, conn,
            task_id=task_id, stream_type="subtitle",
            label=label,
            lang=lang,
            forced=st.get("forced", False),
            source_url=st["uri"],
            out_path=out_path,
        )
        stream_ids[f"sub_{i}"] = sid

    # ── 6. Download subtitle sidecars (fast, do these first) ─────────────────
    for i, st in enumerate(subs):
        sid = stream_ids.get(f"sub_{i}")
        if not sid:
            continue

        stream = await db_run(state, lambda c, s: c.execute(
            "SELECT * FROM streams WHERE id=?", (s,)).fetchone(), conn, sid)
        if stream and stream["status"] == "done":
            log("INFO", f"Sub {i+1} already done — skip")
            continue

        out_path_str = stream["out_path"] if stream else None
        if not out_path_str:
            continue
        out_path = Path(out_path_str)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        lang = st.get("language", "und")
        log("INFO", f"Downloading subtitle [{lang.upper()}]...")
        await db_run(state, db_update_stream, conn, sid, status="downloading")

        try:
            ok = await download_subtitle_stream(st["uri"], out_path, stall_timeout=120)
            if ok:
                await db_run(state, db_update_stream, conn, sid,
                             status="done",
                             size_bytes=out_path.stat().st_size if out_path.exists() else None)
                await broadcast(state, {
                    "type": EVT_STREAM_UPDATE, "stream_id": sid,
                    "task_id": task_id, "status": "done",
                })
                log("INFO", f"Sub [{lang.upper()}] done → {out_path.name}")
            else:
                await db_run(state, db_update_stream, conn, sid, status="failed",
                             error="ffmpeg returned non-zero")
                log("WARN", f"Sub [{lang.upper()}] download failed")
        except StallError as e:
            await db_run(state, db_update_stream, conn, sid, status="failed", error=str(e))
            log("WARN", f"Sub [{lang.upper()}] stalled: {e}")
        except Exception as e:
            await db_run(state, db_update_stream, conn, sid, status="failed", error=str(e))
            log("ERROR", f"Sub [{lang.upper()}] error: {e}")

    # ── 7. Download video ─────────────────────────────────────────────────────
    if not video:
        log("WARN", "No video stream selected — skipping merge")
        await db_run(state, db_set_task_status, conn, task_id, "done")
        await broadcast(state, {"type": EVT_TASK_UPDATE, "task_id": task_id, "status": "done"})
        return

    video_sid = stream_ids.get("video")
    video_path = work_dir / "video.mkv"

    vid_stream = await db_run(state, lambda c, s: c.execute(
        "SELECT * FROM streams WHERE id=?", (s,)).fetchone(), conn, video_sid) if video_sid else None

    if vid_stream and vid_stream["status"] == "done":
        log("INFO", "Video already downloaded — skip")
    else:
        log("INFO", f"Downloading video ({video.get('resolution', '?')})...")
        if video_sid:
            await db_run(state, db_update_stream, conn, video_sid, status="downloading")

        def _video_progress(p: dict):
            msg = {
                "type":        EVT_STREAM_PROGRESS,
                "task_id":     task_id,
                "stream_id":   video_sid,
                "stream_type": "video",
                "label":       video.get("resolution", ""),
                **{k: v for k, v in p.items() if v is not None},
            }
            _emit_progress(state, msg)

        try:
            ok = await download_video_stream(
                video["uri"], video_path, duration_sec,
                _video_progress, state.config.stall_timeout_sec
            )
            if ok and video_sid:
                await db_run(state, db_update_stream, conn, video_sid,
                             status="done",
                             size_bytes=video_path.stat().st_size if video_path.exists() else None)
                _clear_live(state, video_sid)
                await broadcast(state, {"type": EVT_STREAM_UPDATE, "stream_id": video_sid,
                                        "task_id": task_id, "status": "done"})
                log("INFO", "Video download complete")
            elif not ok:
                raise RuntimeError("ffmpeg returned non-zero for video download")
        except (StallError, RuntimeError, Exception) as e:
            if video_sid:
                await db_run(state, db_update_stream, conn, video_sid,
                             status="failed", error=str(e))
                _clear_live(state, video_sid)
            attempts = await db_run(state, db_increment_attempts, conn, task_id)
            log("ERROR", f"Video failed (attempt {attempts}): {e}")
            await broadcast(state, {
                "type": EVT_TASK_ERROR, "task_id": task_id,
                "error": str(e), "attempt": attempts,
                "will_retry": attempts < MAX_ATTEMPTS,
            })
            if attempts >= MAX_ATTEMPTS:
                await db_run(state, db_set_task_status, conn, task_id, "failed", error=str(e))
            return

    # ── 8. Download audio tracks ──────────────────────────────────────────────
    downloaded_audio: list[tuple[Path, dict]] = []

    for i, at in enumerate(audios):
        sid = stream_ids.get(f"audio_{i}")
        audio_path = work_dir / f"audio_{i+1}.m4a"

        a_stream = await db_run(state, lambda c, s: c.execute(
            "SELECT * FROM streams WHERE id=?", (s,)).fetchone(), conn, sid) if sid else None

        if a_stream and a_stream["status"] == "done":
            log("INFO", f"Audio {i+1} already done — skip")
            track_name = clean_track_name(at.get("name", f"Audio {i+1}"))
            downloaded_audio.append((audio_path, {
                "title":    track_name,
                "language": lang_from_track_name(at.get("name", "")) or at.get("language"),
            }))
            continue

        label = at.get("name", f"Audio {i+1}")
        log("INFO", f"Downloading audio [{label}]...")
        if sid:
            await db_run(state, db_update_stream, conn, sid, status="downloading")

        def _audio_progress(p: dict, _sid=sid, _label=label):
            msg = {
                "type":        EVT_STREAM_PROGRESS,
                "task_id":     task_id,
                "stream_id":   _sid,
                "stream_type": "audio",
                "label":       _label,
                **{k: v for k, v in p.items() if v is not None},
            }
            _emit_progress(state, msg)

        try:
            ok = await download_audio_stream(
                at["uri"], audio_path, duration_sec,
                _audio_progress, state.config.stall_timeout_sec
            )
            if ok:
                if sid:
                    await db_run(state, db_update_stream, conn, sid,
                                 status="done",
                                 size_bytes=audio_path.stat().st_size if audio_path.exists() else None)
                    _clear_live(state, sid)
                track_name = clean_track_name(label)
                downloaded_audio.append((audio_path, {
                    "title":    track_name,
                    "language": lang_from_track_name(label) or at.get("language"),
                }))
                log("INFO", f"Audio [{label}] done")
            else:
                if sid:
                    await db_run(state, db_update_stream, conn, sid,
                                 status="failed", error="ffmpeg returned non-zero")
                    _clear_live(state, sid)
                log("WARN", f"Audio [{label}] failed — will skip from merge")
        except (StallError, Exception) as e:
            if sid:
                await db_run(state, db_update_stream, conn, sid, status="failed", error=str(e))
                _clear_live(state, sid)
            log("WARN", f"Audio [{label}] error (non-fatal, skipping): {e}")

    # ── 9. Merge ──────────────────────────────────────────────────────────────
    log("INFO", f"Merging {1 + len(downloaded_audio)} streams into MKV...")
    try:
        _safe_mkdir(mkv_path.parent, "creating output directory")
    except TaskFSError as e:
        log("ERROR", str(e))
        await db_run(state, db_set_task_status, conn, task_id, "failed", error=str(e))
        return

    try:
        ok = await merge_into_mkv(video_path, downloaded_audio, mkv_path)
    except OSError as e:
        err = TaskFSError("writing merged MKV to", mkv_path, e)
        log("ERROR", f"Merge failed: {err}")
        attempts = await db_run(state, db_increment_attempts, conn, task_id)
        await db_run(state, db_set_task_status, conn, task_id,
                     "failed" if attempts >= MAX_ATTEMPTS else "pending",
                     error=str(err))
        return
    except Exception as e:
        log("ERROR", f"Merge failed: {e}")
        attempts = await db_run(state, db_increment_attempts, conn, task_id)
        await db_run(state, db_set_task_status, conn, task_id,
                     "failed" if attempts >= MAX_ATTEMPTS else "pending",
                     error=str(e))
        return

    if not ok:
        log("ERROR", "Merge returned failure")
        attempts = await db_run(state, db_increment_attempts, conn, task_id)
        await db_run(state, db_set_task_status, conn, task_id,
                     "failed" if attempts >= MAX_ATTEMPTS else "pending",
                     error="merge failed")
        return

    # ── 10. Cleanup ───────────────────────────────────────────────────────────
    try:
        shutil.rmtree(work_dir)
    except Exception as e:
        log("WARN", f"Could not remove tmp dir: {e}")

    await db_run(state, db_set_task_status, conn, task_id, "done",
                 mkv_path=str(mkv_path))
    log("INFO", f"Done → {mkv_path.name}")
    await broadcast(state, {
        "type":     EVT_TASK_UPDATE,
        "task_id":  task_id,
        "status":   "done",
        "mkv_path": str(mkv_path),
    })


def item_from_meta(item: dict) -> dict:
    """Reconstruct the info dict from the DB item row for scaffold()."""
    import json
    if item.get("meta_json"):
        try:
            return json.loads(item["meta_json"])
        except Exception:
            pass
    return {
        "id":        item["id"],
        "kind":      item["kind"],
        "title_orig": item["title_orig"],
        "title_ru":  item.get("title_ru"),
        "year":      item.get("year"),
        "url":       item["url"],
        "poster_url": item.get("poster_url"),
    }


async def add_audio_to_task(
    task_id: int,
    audio_url: str,
    label: str | None,
    state,
) -> int:
    """
    Download an extra audio track and remux it into the existing MKV.
    Returns the new stream_id.
    """
    from .scheduler import db_run
    from .ws_server import broadcast

    conn   = state.conn
    config = state.config

    task = await db_run(state, db_get_task, conn, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    if task["status"] != "done":
        raise ValueError(f"Task {task_id} is not done (status={task['status']})")

    mkv_path = Path(task["mkv_path"])
    if not mkv_path.exists():
        raise FileNotFoundError(f"MKV not found: {mkv_path}")

    work_dir = Path(config.tmp_dir) / f"add_audio_{task_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp_audio = work_dir / "extra_audio.m4a"

    # Register stream
    sid = await db_run(state, db_upsert_stream, conn,
        task_id=task_id, stream_type="audio",
        label=label or "Extra Audio",
        lang=lang_from_track_name(label or "") or "und",
        forced=False, source_url=audio_url,
        tmp_path=str(tmp_audio),
    )

    from .db import db_log, db_update_stream
    await db_run(state, db_update_stream, conn, sid, status="downloading")

    def _progress(p):
        _emit_progress(state, {
            "type": EVT_STREAM_PROGRESS, "task_id": task_id, "stream_id": sid,
            "stream_type": "audio", "label": label or "Extra Audio",
            **{k: v for k, v in p.items() if v is not None},
        })

    ok = await download_audio_stream(audio_url, tmp_audio, None, _progress,
                                     state.config.stall_timeout_sec)
    if not ok:
        await db_run(state, db_update_stream, conn, sid, status="failed",
                     error="download failed")
        _clear_live(state, sid)
        raise RuntimeError("Audio download failed")

    audio_meta = {
        "title":    clean_track_name(label or "Extra Audio"),
        "language": lang_from_track_name(label or ""),
    }
    remux_ok = await remux_add_audio(mkv_path, tmp_audio, audio_meta, work_dir)
    if not remux_ok:
        await db_run(state, db_update_stream, conn, sid, status="failed",
                     error="remux failed")
        _clear_live(state, sid)
        raise RuntimeError("Remux failed")

    await db_run(state, db_update_stream, conn, sid, status="done",
                 size_bytes=tmp_audio.stat().st_size if tmp_audio.exists() else None)
    _clear_live(state, sid)
    await db_run(state, db_log, conn, "INFO",
                 f"Added audio track [{label}] to {mkv_path.name}", task_id)

    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)
    await broadcast(state, {"type": EVT_STREAM_UPDATE, "stream_id": sid,
                            "task_id": task_id, "status": "done"})
    return sid


async def add_sub_to_task(
    task_id: int,
    sub_url: str,
    lang: str | None,
    state,
) -> int:
    """
    Download an extra subtitle track and save as a Plex sidecar .srt.
    Returns the new stream_id.
    """
    from .scheduler import db_run
    from .ws_server import broadcast

    conn   = state.conn
    config = state.config

    task = await db_run(state, db_get_task, conn, task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")

    guessed_lang = lang or lang_from_sub_url(sub_url)

    # Find out_path (count existing sub tracks in same lang for numbering)
    existing = await db_run(state, db_get_streams, conn, task_id)
    same_lang = [s for s in existing if s["stream_type"] == "subtitle" and s["lang"] == guessed_lang]
    idx = len(same_lang) + 1

    output_root = task_output_root(task, config)
    out_stem = output_root / task["plex_stem"]
    out_path = out_stem.parent / (
        f"{out_stem.name}.{guessed_lang}.srt" if idx == 1
        else f"{out_stem.name}.{guessed_lang}.{idx}.srt"
    )
    try:
        _safe_mkdir(out_path.parent, "creating sidecar directory")
    except TaskFSError as e:
        raise RuntimeError(str(e)) from e

    sid = await db_run(state, db_upsert_stream, conn,
        task_id=task_id, stream_type="subtitle",
        label=guessed_lang, lang=guessed_lang, forced=False,
        source_url=sub_url, out_path=str(out_path),
    )

    from .db import db_log, db_update_stream
    await db_run(state, db_update_stream, conn, sid, status="downloading")

    ok = await download_subtitle_stream(sub_url, out_path, stall_timeout=120)
    if not ok:
        await db_run(state, db_update_stream, conn, sid, status="failed",
                     error="download failed")
        raise RuntimeError("Subtitle download failed")

    await db_run(state, db_update_stream, conn, sid, status="done",
                 size_bytes=out_path.stat().st_size if out_path.exists() else None,
                 out_path=str(out_path))
    await db_run(state, db_log, conn, "INFO",
                 f"Added subtitle [{guessed_lang}] → {out_path.name}", task_id)

    await broadcast(state, {"type": EVT_STREAM_UPDATE, "stream_id": sid,
                            "task_id": task_id, "status": "done"})
    return sid
