"""
config.py — Load/save/validate daemon configuration.

Config file location (in priority order):
  1. Path passed via --config CLI flag
  2. ~/.config/scrap-pub/config.json
  3. Defaults (written to #2 on first run)
"""

import json
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
