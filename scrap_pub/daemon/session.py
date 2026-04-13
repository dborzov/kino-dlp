"""
session.py — curl-cffi session backed by a Netscape cookies.txt file.

Kino-style, on-demand video websites typically have no public API and sit
behind Cloudflare, so the scraper reuses the cookies from a logged-in browser
session. Cookies are stored in the same Netscape/Mozilla `cookies.txt` format
that yt-dlp, curl, and wget use — one file, easily exported from the browser
with any "Get cookies.txt" extension.

Path comes from `Config.cookies_path` (default `~/.config/scrap-pub/cookies.txt`).

Lifecycle:
  1. `init_session(cookies_path)` is called once at daemon startup; it reads
     the file, validates required keys, and seeds the module state.
  2. `get_session()` returns a singleton `curl_cffi.requests.Session` that
     impersonates Chrome at the TLS layer and has the cookies pre-loaded.
  3. `write_cookies_file(path, text)` is called when the user uploads fresh
     cookies via the CLI or Web UI; it atomically replaces the file and
     rebuilds the session on the next `get_session()` call.
"""

import http.cookiejar
from pathlib import Path

from curl_cffi import requests as cffi_requests

_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-CA,en;q=0.9,ru-RU;q=0.8,ru;q=0.7,en-GB;q=0.6,en-US;q=0.5",
    "dnt": "1",
    "priority": "u=0, i",
    "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
}

REQUIRED_COOKIE_KEYS = frozenset(
    {"_identity", "token", "_csrf", "PHPSESSID", "cf_clearance"}
)

_session: cffi_requests.Session | None = None
_current_cookies: dict[str, str] = {}


def _parse_cookies_file(path: Path) -> dict[str, str]:
    """Load a Netscape cookies.txt file and return {name: value}."""
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return {c.name: c.value for c in jar}


def init_session(cookies_path: Path) -> None:
    """Load cookies from a Netscape cookies.txt file at daemon startup."""
    global _current_cookies, _session
    cookies_path = Path(cookies_path).expanduser()
    if not cookies_path.exists():
        print(
            f"[session] cookies file not found at {cookies_path} — "
            "scraping will fail until you run `scrap-pub cookies <file>`."
        )
        _current_cookies = {}
    else:
        try:
            _current_cookies = _parse_cookies_file(cookies_path)
        except Exception as e:
            print(f"[session] could not parse {cookies_path}: {e}")
            _current_cookies = {}
        missing = validate_cookies(_current_cookies)
        if missing:
            print(
                f"[session] {cookies_path} is missing required cookies: {missing}. "
                "Upload a fresh cookies.txt via `scrap-pub cookies <file>`."
            )
    _session = None  # rebuilt lazily in get_session()


def get_session() -> cffi_requests.Session:
    """Return the singleton curl-cffi session (impersonates Chrome at TLS layer)."""
    global _session
    if _session is None:
        _session = cffi_requests.Session(impersonate="chrome136")
        _session.headers.update(_HEADERS)
        if _current_cookies:
            _session.cookies.update(_current_cookies)
    return _session


def write_cookies_file(cookies_path: Path, raw_text: str) -> dict[str, str]:
    """
    Validate and atomically replace the cookies file; reset the session so
    the next get_session() picks up the new cookies.

    Raises ValueError if `raw_text` is not a valid Netscape cookies.txt or
    is missing any of the REQUIRED_COOKIE_KEYS.
    """
    global _current_cookies, _session
    cookies_path = Path(cookies_path).expanduser()
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = cookies_path.with_name(cookies_path.name + ".tmp")
    tmp.write_text(raw_text)
    try:
        cookies = _parse_cookies_file(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"not a valid Netscape cookies.txt file: {e}") from e

    missing = validate_cookies(cookies)
    if missing:
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"cookies file is missing required keys: {missing}. "
            f"Required: {sorted(REQUIRED_COOKIE_KEYS)}"
        )

    tmp.replace(cookies_path)
    _current_cookies = cookies
    _session = None
    return cookies


def validate_cookies(cookies: dict) -> list[str]:
    """Return sorted list of missing required cookie keys (empty = valid)."""
    return sorted(REQUIRED_COOKIE_KEYS - set(cookies.keys()))


def is_login_response(html: str) -> bool:
    """Return True if the response looks like a login redirect."""
    return (
        "/user/login" in html
        or "Войти" in html[:2000]
        or "sign-in" in html[:2000]
    )
