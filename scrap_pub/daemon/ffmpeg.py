"""
ffmpeg.py — Async ffmpeg subprocess wrapper with progress tracking and stall detection.

run_ffmpeg(cmd, duration_sec, on_progress, stall_timeout)
  → Runs ffmpeg, parses stderr for time= progress lines, calls on_progress(dict).
  → Kills the process if no progress for stall_timeout seconds.
  → Returns (returncode, killed_by_stall).

run_ffmpeg_merge(video, audios, subs, output, audio_meta, sub_meta)
  → Merges streams into a single MKV (stream-copy, no re-encode).
  → Returns True on success.
"""

import asyncio
import re
from pathlib import Path
from typing import Callable

# ffmpeg headers for CDN downloads (bypasses rate-limiting by mimicking a browser).
# The `origin` header is set from the configured target website via set_origin()
# — called once at daemon startup with Config.website.
_origin: str = ""


def set_origin(url: str) -> None:
    """Configure the ffmpeg `origin` header. Called once at daemon startup."""
    global _origin
    _origin = (url or "").rstrip("/")


def _headers() -> str:
    """Return the ffmpeg -headers block with the configured origin."""
    origin_line = f"origin: {_origin}\r\n" if _origin else ""
    return (
        "accept: */*\r\n"
        "accept-language: en-CA,en;q=0.9,ru-RU;q=0.8,ru;q=0.7\r\n"
        "dnt: 1\r\n"
        + origin_line +
        "sec-fetch-dest: empty\r\n"
        "sec-fetch-mode: cors\r\n"
        "sec-fetch-site: cross-site\r\n"
        "user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36\r\n"
    )


class StallError(RuntimeError):
    pass


def _parse_progress_line(line: str, duration_sec: int | None) -> dict | None:
    """
    Parse a single ffmpeg stderr line for progress information.
    Returns a dict with elapsed_sec, pct, speed, size_bytes, or None if no
    time= token is found on this line.
    """
    m = re.search(r'time=(\d+):(\d{2}):(\d{2})(?:\.(\d+))?', line)
    if not m:
        return None
    elapsed = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    pct: float | None = None
    if duration_sec and duration_sec > 0:
        pct = min(99.0, elapsed / duration_sec * 100)
    speed_m = re.search(r'speed=\s*([\d.]+)x', line)
    size_m  = re.search(r'size=\s*(\d+)kB', line)
    return {
        "elapsed_sec": elapsed,
        "pct":         pct,
        "speed":       float(speed_m.group(1)) if speed_m else None,
        "size_bytes":  int(size_m.group(1)) * 1024 if size_m else None,
    }


async def run_ffmpeg(
    cmd: list[str],
    duration_sec: int | None,
    on_progress: Callable[[dict], None],
    stall_timeout: int = 300,
) -> tuple[int, bool]:
    """
    Run ffmpeg with progress tracking and stall detection.
    Returns (returncode, killed_by_stall).
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    loop = asyncio.get_event_loop()
    last_tick = [loop.time()]
    killed_by_stall = [False]
    stderr_tail: list[str] = []  # last N lines for error reporting

    check_interval = min(30, max(1, stall_timeout // 2))

    async def watchdog():
        while True:
            await asyncio.sleep(check_interval)
            idle = loop.time() - last_tick[0]
            if idle > stall_timeout:
                killed_by_stall[0] = True
                proc.kill()
                return

    wd = asyncio.create_task(watchdog())

    async for raw in proc.stderr:
        line = raw.decode(errors="replace").rstrip()
        stderr_tail.append(line)
        if len(stderr_tail) > 30:
            stderr_tail.pop(0)

        info = _parse_progress_line(line, duration_sec)
        if info:
            last_tick[0] = loop.time()
            on_progress(info)

    wd.cancel()
    try:
        await wd
    except asyncio.CancelledError:
        pass

    await proc.wait()

    if killed_by_stall[0]:
        raise StallError(f"ffmpeg stalled for {stall_timeout}s with no progress")

    return proc.returncode, False


def _ffmpeg_dl_cmd(url: str, output: Path, extra: list[str] | None = None) -> list[str]:
    cmd = ["ffmpeg", "-y", "-headers", _headers(), "-i", url, "-c", "copy"]
    if extra:
        cmd += extra
    cmd.append(str(output))
    return cmd


async def download_video_stream(
    url: str,
    output: Path,
    duration_sec: int | None,
    on_progress: Callable[[dict], None],
    stall_timeout: int = 300,
) -> bool:
    """Download an HLS video stream. Returns True on success."""
    if output.exists() and output.stat().st_size > 0:
        on_progress({"elapsed_sec": 0, "pct": 100.0, "speed": None, "size_bytes": output.stat().st_size})
        return True
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = _ffmpeg_dl_cmd(url, output)
    rc, _ = await run_ffmpeg(cmd, duration_sec, on_progress, stall_timeout)
    return rc == 0


async def download_audio_stream(
    url: str,
    output: Path,
    duration_sec: int | None,
    on_progress: Callable[[dict], None],
    stall_timeout: int = 300,
) -> bool:
    """Download an HLS audio stream (AAC, with ADTS→ASC bitstream filter). Returns True on success."""
    if output.exists() and output.stat().st_size > 0:
        on_progress({"elapsed_sec": 0, "pct": 100.0, "speed": None, "size_bytes": output.stat().st_size})
        return True
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = _ffmpeg_dl_cmd(url, output, extra=["-bsf:a", "aac_adtstoasc"])
    rc, _ = await run_ffmpeg(cmd, duration_sec, on_progress, stall_timeout)
    return rc == 0


async def download_subtitle_stream(
    url: str,
    output: Path,
    stall_timeout: int = 120,
) -> bool:
    """Download a subtitle stream (VTT/m3u8 → .srt). Returns True on success."""
    if output.exists() and output.stat().st_size > 0:
        return True
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-headers", _headers(), "-i", url, str(output)]
    rc, _ = await run_ffmpeg(cmd, None, lambda p: None, stall_timeout)
    return rc == 0


async def merge_into_mkv(
    video_file: Path,
    audio_files: list[tuple[Path, dict]],  # (path, {title, language})
    output_file: Path,
    sub_meta: list[dict] | None = None,    # unused (subs are sidecars)
) -> bool:
    """
    Merge video + audio tracks into a single MKV (stream-copy, no re-encode).
    Returns True on success.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-i", str(video_file)]
    for path, _ in audio_files:
        cmd += ["-i", str(path)]

    cmd += ["-map", "0:v"]
    for i in range(len(audio_files)):
        cmd += ["-map", f"{i+1}:a"]

    cmd += ["-c", "copy"]

    for idx, (_, meta) in enumerate(audio_files):
        if meta.get("title"):
            cmd += [f"-metadata:s:a:{idx}", f"title={meta['title']}"]
        if meta.get("language"):
            cmd += [f"-metadata:s:a:{idx}", f"language={meta['language']}"]

    cmd.append(str(output_file))

    rc, _ = await run_ffmpeg(cmd, None, lambda p: None, stall_timeout=600)
    return rc == 0


async def remux_add_audio(
    existing_mkv: Path,
    new_audio: Path,
    audio_meta: dict,
    tmp_dir: Path,
) -> bool:
    """
    Add a new audio track to an existing MKV (stream-copy, no re-encode).
    Replaces existing_mkv atomically on success.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = tmp_dir / "remux_tmp.mkv"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(existing_mkv),
        "-i", str(new_audio),
        "-map", "0", "-map", "1:a",
        "-c", "copy",
        f"-metadata:s:a:{_count_audio_tracks(existing_mkv)}",
        f"title={audio_meta.get('title', 'Audio')}",
    ]
    if audio_meta.get("language"):
        cmd += [f"-metadata:s:a:{_count_audio_tracks(existing_mkv)}", f"language={audio_meta['language']}"]
    cmd.append(str(tmp_out))

    rc, _ = await run_ffmpeg(cmd, None, lambda p: None, stall_timeout=600)
    if rc == 0 and tmp_out.exists():
        tmp_out.replace(existing_mkv)
        return True
    return False


def _count_audio_tracks(mkv: Path) -> int:
    """Quick heuristic count of audio tracks — used for metadata index."""
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(mkv)],
            capture_output=True, text=True, timeout=10
        )
        return len([line for line in r.stdout.splitlines() if line.strip()])
    except Exception:
        return 0


def lang_from_track_name(name: str) -> str | None:
    """Extract ISO-639-2 code from audio track name like '01. Дубляж. Студия (RUS)'."""
    m = re.search(r'\(([A-Z]{3})\)', name)
    if not m:
        return None
    code = m.group(1).lower()
    return {"fre": "fre", "rus": "rus", "eng": "eng", "esp": "spa",
            "ukr": "ukr", "ger": "ger", "ita": "ita", "jpn": "jpn"}.get(code, code)


def clean_track_name(name: str) -> str:
    """Strip leading ordinal like '01. ' from track name."""
    return re.sub(r'^\d+\.\s*', '', name).strip()


def lang_from_sub_url(url: str) -> str:
    """Guess 3-letter ISO-639-2 code from a subtitle stream URL."""
    m = re.search(r'/([a-z]{3})(?:[/_-]|$)', url.lower())
    return m.group(1) if m else "und"
