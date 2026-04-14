"""
config.py — Load/save/validate daemon configuration.

Config file location (in priority order):
  1. Path passed via --config CLI flag
  2. ~/.config/scrap-pub/config.json
  3. Defaults (written to #2 on first run)
"""

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "scrap-pub" / "config.json"

# Detect project root relative to this file for sensible defaults
_PROJECT_ROOT = Path(__file__).parents[3]  # scrap-pub/
_DEFAULT_OUTPUT  = _PROJECT_ROOT / "output"
_DEFAULT_TMP     = _PROJECT_ROOT / "tmp"
_DEFAULT_DB      = Path.home() / ".local" / "share" / "scrap-pub" / "queue.db"
_DEFAULT_COOKIES = Path.home() / ".config" / "scrap-pub" / "cookies.txt"


@dataclass
class Config:
    website:      str  = ""
    output_dir:   Path = field(default_factory=lambda: _DEFAULT_OUTPUT)
    tmp_dir:      Path = field(default_factory=lambda: _DEFAULT_TMP)
    db_path:      Path = field(default_factory=lambda: _DEFAULT_DB)
    cookies_path: Path = field(default_factory=lambda: _DEFAULT_COOKIES)
    concurrency: int = 2
    stall_timeout_sec: int = 300
    http_port: int = 8765
    ws_port:   int = 8766
    video_quality: str = "lowest"     # "lowest" | "highest" | "720p" | "1080p"
    audio_langs: list[str] = field(default_factory=lambda: ["RUS", "ENG", "FRE"])
    sub_langs:   list[str] = field(default_factory=lambda: ["rus", "eng", "fra"])
    # Advisory floor for free space on a task's output filesystem. The check is
    # not atomic across concurrent workers — it's a guard against enqueueing
    # into a nearly-full disk, not a per-task size reservation.
    min_free_space_gb: int = 10

    # Internal: path this config was loaded from / should be saved to.
    # Not serialised — excluded from to_dict() and asdict() via field(repr=False).
    _cfg_path: Path = field(
        default_factory=lambda: DEFAULT_CONFIG_PATH,
        init=False, repr=False, compare=False,
    )

    def __post_init__(self):
        self.output_dir   = Path(self.output_dir).expanduser()
        self.tmp_dir      = Path(self.tmp_dir).expanduser()
        self.db_path      = Path(self.db_path).expanduser()
        self.cookies_path = Path(self.cookies_path).expanduser()
        self.website      = (self.website or "").rstrip("/")

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        if cfg_path.exists():
            try:
                raw = json.loads(cfg_path.read_text())
                # Convert path strings to Path objects
                for key in ("output_dir", "tmp_dir", "db_path", "cookies_path"):
                    if key in raw:
                        raw[key] = Path(raw[key]).expanduser()
                cfg = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
                cfg._cfg_path = cfg_path
                return cfg
            except Exception as e:
                print(f"[config] Warning: could not load {cfg_path}: {e}. Using defaults.")
        cfg = cls()
        cfg._cfg_path = cfg_path
        cfg.save(cfg_path)
        return cfg

    def save(self, path: Path | None = None) -> None:
        cfg_path = path or self._cfg_path
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        d = self.to_dict()
        cfg_path.write_text(json.dumps(d, indent=2, ensure_ascii=False))

    def to_dict(self) -> dict:
        d = asdict(self)
        # Serialize Path objects to strings; remove internal fields
        for key in ("output_dir", "tmp_dir", "db_path", "cookies_path"):
            d[key] = str(d[key])
        d.pop("_cfg_path", None)
        return d

    def update(self, key: str, value) -> None:
        """Update a single config field and save to the same path it was loaded from."""
        if key not in self.__dataclass_fields__ or key.startswith("_"):
            raise KeyError(f"Unknown config key: {key!r}")
        # Type coercion
        field_type = type(getattr(self, key))
        if field_type in (int, float):
            value = field_type(value)
        elif field_type is list:
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",")]
        setattr(self, key, value)
        self.save()

    def validate(self) -> tuple[list[str], list[str]]:
        """Sanity-check the config before the daemon starts.

        Returns (errors, warnings). An empty errors list means the daemon can start.
        Warnings are advisory (e.g. missing cookies file — scrape will fail clearly
        but nothing blows up at boot).
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not self.website:
            errors.append(
                "`website` is not set. Point it at your target on-demand site, "
                "e.g. `scrap-pub config --set website=https://example.com`."
            )
        elif not (self.website.startswith("http://") or self.website.startswith("https://")):
            errors.append(
                f"`website` must start with http:// or https:// (got {self.website!r})."
            )

        for label, path, need_parent in (
            ("output_dir", self.output_dir, False),
            ("tmp_dir",    self.tmp_dir,    False),
            ("db_path",    self.db_path,    True),
        ):
            target = path.parent if need_parent else path
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"`{label}` ({target}) is not creatable: {e}")
                continue
            # Writability probe: create and remove a temp file
            probe = target / ".scrap-pub-write-probe"
            try:
                probe.write_text("ok")
                probe.unlink()
            except Exception as e:
                errors.append(f"`{label}` ({target}) is not writable: {e}")

        if not self.cookies_path.exists():
            warnings.append(
                f"`cookies_path` ({self.cookies_path}) does not exist — "
                "downloads will fail until you provide one via "
                "`scrap-pub cookies /path/to/cookies.txt`."
            )

        if self.concurrency < 1:
            errors.append(f"`concurrency` must be >= 1 (got {self.concurrency}).")
        if not (1 <= int(self.http_port) <= 65535):
            errors.append(f"`http_port` must be 1..65535 (got {self.http_port}).")
        if not (1 <= int(self.ws_port) <= 65535):
            errors.append(f"`ws_port` must be 1..65535 (got {self.ws_port}).")
        if self.http_port == self.ws_port:
            errors.append(
                f"`http_port` and `ws_port` must differ (both are {self.http_port})."
            )
        if self.stall_timeout_sec < 1:
            errors.append(
                f"`stall_timeout_sec` must be >= 1 (got {self.stall_timeout_sec})."
            )
        if self.min_free_space_gb < 0:
            errors.append(
                f"`min_free_space_gb` must be >= 0 (got {self.min_free_space_gb})."
            )

        return errors, warnings


# ── Per-task output dir validation ────────────────────────────────────────────

class OutputDirError(ValueError):
    """Raised when a task-level output directory is unusable."""


def validate_task_output_dir(path: str | Path, min_free_gb: int) -> Path:
    """Resolve and validate a per-task output directory.

    Returns the resolved absolute Path on success. Raises `OutputDirError`
    with a human-readable message on failure — callers can surface `str(exc)`
    directly in the WS reply / task `last_error`.

    Behaviour:
      - Expands `~` and resolves to an absolute path.
      - If the directory does not exist, creates it when the parent exists
        and is writable. Fails loudly if the parent is missing (catches typos
        like `/mtn/plex`).
      - Fails if the path exists but is not a directory.
      - Runs a write-probe (create + unlink a sentinel file).
      - Checks `shutil.disk_usage(path).free >= min_free_gb * GiB`.
    """
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=False)
    except OSError as e:
        raise OutputDirError(f"cannot resolve output directory {p}: {e.strerror or e}") from e

    if not p.exists():
        parent = p.parent
        if not parent.exists():
            raise OutputDirError(
                f"output directory parent does not exist: {parent} "
                "(typo?). Create the parent first, then retry."
            )
        if not parent.is_dir():
            raise OutputDirError(
                f"output directory parent is not a directory: {parent}"
            )
        try:
            p.mkdir(parents=False, exist_ok=True)
        except PermissionError as e:
            raise OutputDirError(
                f"cannot create output directory {p}: permission denied on {parent}"
            ) from e
        except OSError as e:
            raise OutputDirError(
                f"cannot create output directory {p}: {e.strerror or e}"
            ) from e
    elif not p.is_dir():
        raise OutputDirError(f"output path is not a directory: {p}")

    probe = p / ".scrap-pub-write-probe"
    try:
        probe.write_text("ok")
        probe.unlink()
    except PermissionError as e:
        raise OutputDirError(
            f"output directory is not writable: {p} (permission denied)"
        ) from e
    except OSError as e:
        raise OutputDirError(
            f"output directory is not writable: {p} ({e.strerror or e})"
        ) from e

    try:
        free = shutil.disk_usage(p).free
    except OSError as e:
        raise OutputDirError(
            f"cannot stat filesystem at {p}: {e.strerror or e}"
        ) from e
    min_bytes = min_free_gb * 1024**3
    if free < min_bytes:
        free_gb = free / 1024**3
        raise OutputDirError(
            f"insufficient free space at {p}: "
            f"{free_gb:.1f} GB free, need at least {min_free_gb} GB"
        )

    return p


def estimate_min_free_gb(duration_sec: int | None, base_min_gb: int) -> int:
    """Refine the free-space floor using an estimated episode duration.

    Uses a rough ~3 GB/hour bitrate heuristic plus a 2 GB safety margin, then
    takes the maximum with the configured base floor. Returns an integer GB
    count suitable for passing back into `validate_task_output_dir`.
    """
    if not duration_sec or duration_sec <= 0:
        return base_min_gb
    estimated = int((duration_sec / 3600) * 3) + 2
    return max(base_min_gb, estimated)
