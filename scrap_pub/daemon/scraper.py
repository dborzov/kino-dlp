"""
scraper.py — Sync scraping functions for the configured target website.

All public functions are synchronous and designed to run in a ThreadPoolExecutor.
They use get_session() from session.py for HTTP.

The target site is configured at runtime via set_website(url) — typically called
from daemon startup with the value of Config.website. Nothing in this module
hardcodes any specific site URL; unset → RuntimeError on the first URL call.

Public API:
  set_website(url)         → configure base URL (call once at startup)
  scrape(url)              → full metadata dict (items, seasons, episodes)
  parse_manifest(url)      → {video_streams, audio_tracks, subtitle_tracks, ...}
  get_manifest_url(url)    → (manifest_url, episode_info_dict)
  scaffold(info, config)   → create Plex dirs, download poster/thumbs, write .info.json
  canonical_title(info)    → original-language title for filenames
  episode_url(item_id, season, episode) → {website}/item/view/{id}/s{s}e{e}
"""

import json
import random
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .session import get_session, is_login_response

_website: str = ""


def set_website(url: str) -> None:
    """Configure the target website base URL. Called once at daemon startup."""
    global _website
    _website = (url or "").rstrip("/")


def _base() -> str:
    if not _website:
        raise RuntimeError(
            "No target website configured. Set the `website` key in config.json "
            "to the full base URL of the on-demand video site you want to scrape "
            "(e.g. `scrap-pub config --set website=https://example.com`)."
        )
    return _website


def _host() -> str:
    return urlparse(_base()).netloc


class CookieExpiredError(RuntimeError):
    """Raised when the target site returns 403 or redirects to login."""
    pass


# ── URL helpers ────────────────────────────────────────────────────────────────

def normalise_url(arg: str) -> tuple[str, str]:
    """Return (canonical_url, item_id). Accepts any URL form for the target site."""
    arg = arg.strip().rstrip("/")
    if arg.startswith("http"):
        full = arg
        host = _host()
        if host and host in arg:
            path = arg.split(host, 1)[1]
        else:
            path = urlparse(arg).path
    else:
        path = "/" + arg.lstrip("/")
        full = _base() + path
    m = re.search(r'/view/(\d+)', path)
    if not m:
        raise ValueError(f"Cannot extract item id from: {arg!r}")
    return full, m.group(1)


def episode_url(item_id: str, season: int, ep: int) -> str:
    return f"{_base()}/item/view/{item_id}/s{season}e{ep}"


def _season_url(item_id: str, season: int) -> str:
    return f"{_base()}/item/view/{item_id}/s{season}e1"


# ── HTTP fetch ─────────────────────────────────────────────────────────────────

def _fetch(url: str, referer: str | None = None) -> tuple[BeautifulSoup, str]:
    headers: dict = {}
    if referer:
        headers["referer"] = referer
        headers["sec-fetch-site"] = "same-origin"
    r = get_session().get(url, headers=headers)
    if r.status_code == 403 or is_login_response(r.text):
        raise CookieExpiredError(f"Session expired (HTTP {r.status_code}) fetching {url}")
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml"), r.text


# ── Parsers ────────────────────────────────────────────────────────────────────

def _main_row(soup: BeautifulSoup):
    for row in soup.select(".padding > .row"):
        if row.select_one("table.table-striped"):
            return row
    return None


def _parse_og_titles(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    og = soup.find("meta", attrs={"property": "og:title"})
    if not og or not og.get("content"):
        return None, None
    content = og["content"].strip()
    # The site is inconsistent with whitespace around the RU/orig separator:
    # sometimes " / ", sometimes " /", sometimes "/ ". Accept any of them.
    m = re.split(r'\s*/\s*', content, maxsplit=1)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return m[0].strip(), m[1].strip()
    return content or None, None


def _parse_meta_table(row) -> dict:
    meta: dict = {}
    if not row:
        return meta
    table = row.select_one("table.table-striped")
    if not table:
        return meta
    for tr in table.select("tr"):
        cells = tr.select("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True)
        vc    = cells[1]
        if label == "Рейтинг":
            kp_a   = vc.select_one('a[href*="kinopoisk.ru"]')
            imdb_a = vc.select_one('a[href*="imdb.com"]')
            if kp_a:
                meta["rating_kp"] = kp_a.get_text(strip=True)
                m = re.search(r'/film/(\d+)', kp_a["href"])
                meta["kinopoisk_id"] = m.group(1) if m else None
            if imdb_a:
                meta["rating_imdb"] = imdb_a.get_text(strip=True)
                m = re.search(r'/(tt\d+)', imdb_a["href"])
                meta["imdb_id"] = m.group(1) if m else None
        elif label == "Год выхода":
            meta["year"] = vc.get_text(strip=True)
        elif label == "Страна":
            meta["countries"] = [a.get_text(strip=True) for a in vc.select("a")]
        elif label == "Жанр":
            meta["genres"] = [a.get_text(strip=True) for a in vc.select("a")]
        elif label in ("Создатель", "Режиссёр"):
            meta["directors"] = [a.get_text(strip=True) for a in vc.select("a")]
        elif label == "В ролях":
            meta["cast"] = [a.get_text(strip=True) for a in vc.select("a")]
        elif label == "Длительность":
            meta["duration_str"] = re.sub(r'\s+', ' ', vc.get_text(" ", strip=True))
        elif label == "Возраст":
            meta["age_rating"] = vc.get_text(strip=True)
        elif label == "Субтитры":
            raw = re.sub(r'\s*Добавить\s*', '', vc.get_text(" ", strip=True)).strip().rstrip(",")
            meta["subtitles"] = [s.strip() for s in raw.split(",") if s.strip()]
    return meta


def _parse_description(row) -> str | None:
    """Return the plot text from the `#plot` tab-pane inside the main content row.

    The site keeps the synopsis in `div.tab-pane#plot`, which is always a
    descendant of the main content row (never in the "related items" area).
    Non-breaking spaces in the source are folded into regular spaces.
    """
    if not row:
        return None
    el = row.select_one("#plot")
    if not el:
        return None
    text = el.get_text(" ", strip=True).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_poster(row, item_id: str) -> str:
    img = row.select_one("img.item-poster-relative") if row else None
    if img and img.get("src"):
        return img["src"]
    return f"https://m.pushbr.com/poster/item/big/{item_id}.jpg"


def _parse_audio(row) -> list[str]:
    ol = row.select_one("#audio ol") if row else None
    if not ol:
        return []
    return [li.get_text(strip=True) for li in ol.select("li")]


def _parse_seasons(soup: BeautifulSoup, item_id: str) -> list[int]:
    seasons = []
    for a in soup.select(f'a[href^="/item/view/{item_id}/s"]'):
        m = re.match(r'/item/view/\d+/s(\d+)e1$', a["href"])
        if m:
            seasons.append(int(m.group(1)))
    return sorted(set(seasons))


def _parse_playlist(html: str, season: int | None = None) -> list[dict]:
    m = re.search(r'window\.PLAYER_PLAYLIST\s*=\s*(\[.*?\]);', html)
    if not m:
        return []
    entries = json.loads(m.group(1))
    result = []
    for ep in entries:
        if season is not None and ep.get("season") != season:
            continue
        result.append({
            "season":       ep.get("season", 0),
            "episode":      ep.get("episode", 1),
            "title":        ep.get("episode_title"),
            "duration_sec": ep.get("duration"),
            "thumb":        ep.get("thumb"),
            "media_id":     ep.get("media_id"),
            "manifest":     ep.get("manifest"),
        })
    return result


# ── Full scrape ────────────────────────────────────────────────────────────────

def scrape(
    arg: str,
    *,
    fetch_episodes: bool = True,
    only_season: int | None = None,
    progress_cb: Callable[[int, int, int], None] | None = None,
) -> dict:
    """
    Scrape metadata for a movie or TV series. Returns metadata dict.

    fetch_episodes=True (default) walks every season of a series and populates
    result["seasons_data"][s] = {episodes, audio_tracks, subtitles}. This is
    what downloader.download_task() and scaffold() need.

    fetch_episodes=False is a fast path used by `scrap-pub lookup`: one HTTP
    request, parses titles/year/kind, and for a series populates just
    result["seasons"] (list of season numbers from the selector buttons).
    No per-season fetches, no seasons_data, no PLAYER_PLAYLIST walk.

    only_season, when set, restricts per-season fetches to that one season —
    seasons_data ends up with a single key. Used by enqueue when the caller
    named a specific episode URL, so we avoid re-walking 13 seasons of a
    long-running show just to download one episode.

    progress_cb(done, total, current_season) is invoked before each season
    fetch when fetch_episodes=True and the target is a series. The callback
    renders whatever UI it likes; the scraper stays I/O-agnostic.
    """
    url, item_id = normalise_url(arg)

    # Capture input-URL season/episode for callers that want to display it
    # (e.g. `lookup` showing "current URL points at S10E04"). Only surfaced
    # in the returned dict when the URL actually encoded them.
    m_se = re.search(r'/s(\d+)e(\d+)$', url)
    input_season = int(m_se.group(1)) if m_se else None
    input_episode = int(m_se.group(2)) if m_se else None

    # Normalise to root URL for metadata (strip sNNeNN suffix)
    is_episode_url = m_se is not None
    root_url = f"{_base()}/item/view/{item_id}" if is_episode_url else url

    soup_root, html_root = _fetch(root_url)

    row = _main_row(soup_root)
    if not row:
        raise RuntimeError("Could not find main content row on page.")

    title_ru, title_orig = _parse_og_titles(soup_root)
    poster_url = _parse_poster(row, item_id)
    meta = _parse_meta_table(row)
    description = _parse_description(row)
    seasons = _parse_seasons(soup_root, item_id)

    result = {
        "id":          item_id,
        "url":         root_url,
        "title_ru":    title_ru,
        "title_orig":  title_orig,
        "poster_url":  poster_url,
        "description": description,
        **meta,
    }
    # Movie URLs are served as /s0e1, which is a site convention rather
    # than a meaningful season/episode. Only surface input_season/episode
    # when they look like a real series reference.
    if input_season is not None and (seasons or input_season > 0):
        result["input_season"] = input_season
        result["input_episode"] = input_episode

    if not seasons:
        # Movie — parse PLAYER_PLAYLIST from root page (or the episode URL)
        result["kind"] = "movie"
        # Try root page first, then the original URL if different
        playlist = _parse_playlist(html_root)
        if not playlist and is_episode_url:
            _, html_ep = _fetch(url, referer=root_url)
            playlist = _parse_playlist(html_ep)
        if playlist:
            ep = playlist[0]
            result["movie_entry"] = {
                "season":       ep.get("season", 0),
                "episode":      ep.get("episode", 1),
                "media_id":     ep.get("media_id"),
                "duration_sec": ep.get("duration_sec"),
            }
        result["audio_tracks"] = _parse_audio(row)
        return result

    result["kind"] = "series"
    result["seasons"] = seasons

    if not fetch_episodes:
        # Fast path: caller only wants kind/title/year + season list.
        return result

    if only_season is not None:
        if only_season not in seasons:
            raise ValueError(
                f"season {only_season} not found for item {item_id} "
                f"(available: {seasons})"
            )
        seasons_to_fetch = [only_season]
    else:
        seasons_to_fetch = seasons

    result["seasons_data"] = {}
    total = len(seasons_to_fetch)
    for i, s in enumerate(seasons_to_fetch):
        if progress_cb is not None:
            progress_cb(i, total, s)
        s_url = _season_url(item_id, s)
        time.sleep(random.uniform(0.8, 2.0))
        s_soup, s_html = _fetch(s_url, referer=root_url)
        s_row = _main_row(s_soup)
        result["seasons_data"][s] = {
            "episodes":     _parse_playlist(s_html, s),
            "audio_tracks": _parse_audio(s_row) if s_row else [],
            "subtitles":    _parse_meta_table(s_row).get("subtitles", []) if s_row else [],
        }
    if progress_cb is not None and seasons_to_fetch:
        progress_cb(total, total, seasons_to_fetch[-1])

    return result


# ── Canonical title ────────────────────────────────────────────────────────────

def canonical_title(info: dict) -> str:
    """Use original-language title; fall back to Russian title."""
    return info.get("title_orig") or info.get("title_ru") or "Unknown"


# ── HLS manifest ───────────────────────────────────────────────────────────────

def get_manifest_url(item_id: str, season: int, ep: int) -> tuple[str, dict]:
    """
    Fetch a fresh signed manifest URL for the given episode.
    Returns (manifest_url, episode_info_dict).
    """
    url = episode_url(item_id, season, ep)
    r = get_session().get(url, headers={"referer": _base(), "sec-fetch-site": "same-origin"})
    if r.status_code == 403 or is_login_response(r.text):
        raise CookieExpiredError(f"Session expired fetching manifest for s{season}e{ep}")
    html = r.text
    m = re.search(r'window\.PLAYER_PLAYLIST\s*=\s*(\[.*?\]);', html)
    if not m:
        raise RuntimeError(f"No PLAYER_PLAYLIST found for item {item_id} s{season}e{ep}")
    playlist = json.loads(m.group(1))
    matches = [p for p in playlist if p.get("season") == season and p.get("episode") == ep]
    if not matches:
        # For movies (season=0), just take first entry
        if season == 0 and playlist:
            matches = [playlist[0]]
        else:
            raise RuntimeError(
                f"S{season:02d}E{ep:02d} not found in PLAYER_PLAYLIST "
                f"(got {len(playlist)} entries)"
            )
    entry = matches[0]
    return entry["manifest"], entry


def _parse_hls_attributes(attr_str: str) -> dict:
    attrs = {}
    for m in re.finditer(r'([A-Z0-9-]+)=("(?:[^"\\]|\\.)*"|[^,]+)', attr_str):
        attrs[m.group(1)] = m.group(2).strip('"')
    return attrs


def parse_manifest(manifest_url: str) -> dict:
    """Fetch HLS master manifest and return structured stream data."""
    r = get_session().get(manifest_url)
    r.raise_for_status()
    text = r.text
    parsed = urlparse(manifest_url)
    cdn_base = f"{parsed.scheme}://{parsed.netloc}"

    video_streams: list[dict] = []
    audio_tracks:  list[dict] = []
    sub_tracks:    list[dict] = []
    seen_audio:    dict[str, int] = {}

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_hls_attributes(line[len("#EXT-X-MEDIA:"):])
            kind  = attrs.get("TYPE", "")
            name  = attrs.get("NAME", "")
            lang  = attrs.get("LANGUAGE", "")
            group = attrs.get("GROUP-ID", "")
            uri   = attrs.get("URI", "")
            dflt  = attrs.get("DEFAULT", "NO") == "YES"
            if uri and not uri.startswith("http"):
                uri = cdn_base + uri
            if kind == "SUBTITLES":
                sub_tracks.append({
                    "name":     name,
                    "language": lang,
                    "forced":   "forced" in name.lower(),
                    "uri":      uri,
                })
            elif kind == "AUDIO":
                if name not in seen_audio:
                    seen_audio[name] = len(audio_tracks)
                    audio_tracks.append({
                        "name":     name,
                        "language": lang,
                        "default":  dflt,
                        "groups":   [group],
                        "uri":      uri,
                    })
                else:
                    audio_tracks[seen_audio[name]]["groups"].append(group)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            attrs = _parse_hls_attributes(line[len("#EXT-X-STREAM-INF:"):])
            uri   = lines[i + 1].strip() if i + 1 < len(lines) else ""
            bw    = int(attrs.get("BANDWIDTH", 0))
            res   = attrs.get("RESOLUTION", "")
            w = h = None
            if "x" in res:
                ww, hh = res.split("x", 1)
                w, h = int(ww), int(hh)
            video_streams.append({
                "resolution":    res,
                "width": w, "height": h,
                "bandwidth_bps": bw,
                "bandwidth_mbps": round(bw / 1_000_000, 2),
                "codecs":        attrs.get("CODECS", ""),
                "fps":           attrs.get("FRAME-RATE", ""),
                "video_range":   attrs.get("VIDEO-RANGE", ""),
                "audio_group":   attrs.get("AUDIO", ""),
                "uri":           uri,
            })
            i += 1
        i += 1

    video_streams.sort(key=lambda s: s["bandwidth_bps"], reverse=True)
    return {
        "manifest_url":    manifest_url,
        "cdn_base":        cdn_base,
        "video_streams":   video_streams,
        "audio_tracks":    audio_tracks,
        "subtitle_tracks": sub_tracks,
    }


def select_streams(manifest: dict, config) -> dict:
    """Filter manifest streams according to config preferences."""
    vs = manifest["video_streams"]
    if not vs:
        video = None
    elif config.video_quality == "lowest":
        video = vs[-1]
    elif config.video_quality == "highest":
        video = vs[0]
    else:
        # Try to match a resolution like "720p" or "1080p"
        target_h = int(re.sub(r'\D', '', config.video_quality) or "0")
        video = next((s for s in vs if s.get("height") == target_h), vs[-1])

    audio_langs_upper = [lang.upper() for lang in config.audio_langs]
    audio = []
    for at in manifest["audio_tracks"]:
        lang = at.get("language", "").upper()
        name = at.get("name", "")
        # Match by language code in name like "(RUS)" or by LANGUAGE field
        m = re.search(r'\(([A-Z]{3})\)', name)
        track_lang = m.group(1) if m else lang[:3]
        if track_lang in audio_langs_upper or lang in audio_langs_upper:
            audio.append(at)

    sub_langs_lower = [lang.lower() for lang in config.sub_langs]
    subs = []
    for st in manifest["subtitle_tracks"]:
        lang = st.get("language", "").lower()
        if lang in sub_langs_lower or not lang:
            subs.append(st)

    return {"video": video, "audio": audio, "subtitles": subs}


# ── Scaffold ───────────────────────────────────────────────────────────────────

def _sanitise(title: str) -> str:
    # Colon → space so "Title: Subtitle" keeps the word break.
    t = title.replace(":", " ")
    # Whitelist: unicode letters/digits, underscore, space, hyphen. Drops
    # apostrophes, quotes, commas, dots, parens/brackets/braces, shell
    # metacharacters, path separators, and any other exotic punctuation.
    # Parens, brackets and braces in particular would confuse Plex's
    # (YYYY) / [info] / {tmdb-N} filename conventions.
    t = re.sub(r"[^\w\s-]", "", t)
    # Strip edge hyphens/underscores too so filenames can't start with `-`
    # (which POSIX tools try to parse as a flag).
    return re.sub(r"\s+", " ", t).strip(" -_")


def _dir_name(title: str, year) -> str:
    clean = _sanitise(title) or "Untitled"
    return f"{clean}({year})" if year else clean


def _episode_stem(show_dir: str, season: int, episode: int, ep_title: str | None) -> str:
    stem = f"{show_dir} - s{season:02d}e{episode:02d}"
    if ep_title:
        t = _sanitise(ep_title)
        if t:
            stem += f" - {t}"
    return stem


def _download_image(url: str, dest: Path, output_root: Path) -> bool:
    if not url:
        return False
    try:
        r = get_session().get(url, headers={"referer": _base(), "sec-fetch-site": "same-origin"})
        r.raise_for_status()
        if "image" not in r.headers.get("content-type", "") and len(r.content) < 1000:
            return False
        dest.write_bytes(r.content)
        return True
    except Exception as e:
        print(f"[scraper] Image download failed ({dest.name}): {e}")
        return False


def _duration_secs(info: dict) -> int | None:
    d = info.get("duration_str", "")
    m = re.search(r'(\d+):(\d{2}):(\d{2})', d)
    if m:
        return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    m = re.search(r'(\d+)\s*(?:мин|min)', d, re.I)
    if m:
        return int(m[1]) * 60
    return None


def scaffold(
    info: dict,
    output_root: Path,
    *,
    only: tuple[int, int] | None = None,
) -> list[str]:
    """
    Create Plex directory structure: poster, thumbnails, .info.json files.
    Returns list of plex_stem strings (relative to output_root).
    Idempotent — safe to call multiple times.

    `only=(season, episode)` restricts per-episode file creation to that one
    episode (show-level files still get created if missing). Without it the
    function walks every episode in `info["seasons_data"]`, which is the right
    behaviour when the caller actually scraped the whole series.

    Show-level work (poster + show.info.json) is skipped when both files
    already exist on disk — cheap short-circuit for the common case where a
    second episode of an already-scaffolded show is being enqueued.
    """
    title     = canonical_title(info)
    year      = info.get("year")
    show_dir  = _dir_name(title, year)
    root_dir  = output_root / show_dir
    root_dir.mkdir(parents=True, exist_ok=True)
    stems: list[str] = []

    if info["kind"] == "movie":
        poster_dest = root_dir / f"{show_dir}.jpg"
        if not poster_dest.exists():
            _download_image(info.get("poster_url", ""), poster_dest, output_root)
        meta = {
            "id":           f"scrap-pub-{info['id']}",
            "webpage_url":  info["url"],
            "extractor":    "scrap-pub",
            "title":        title,
            "title_ru":     info.get("title_ru"),
            "title_orig":   info.get("title_orig"),
            "kind":         "movie",
            "release_year": int(year) if year else None,
            "description":  info.get("description"),
            "genres":       info.get("genres", []),
            "directors":    info.get("directors", []),
            "cast":         info.get("cast", []),
            "countries":    info.get("countries", []),
            "rating_imdb":  info.get("rating_imdb"),
            "imdb_id":      info.get("imdb_id"),
            "rating_kp":    info.get("rating_kp"),
            "kinopoisk_id": info.get("kinopoisk_id"),
            "duration":     _duration_secs(info),
            "thumbnail":    info.get("poster_url"),
        }
        (root_dir / f"{show_dir}.info.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        stems.append(f"{show_dir}/{show_dir}")
    else:
        # Series
        poster_dest    = root_dir / "poster.jpg"
        show_info_dest = root_dir / "show.info.json"
        show_level_ready = show_info_dest.exists() and poster_dest.exists()
        if not show_level_ready:
            if not poster_dest.exists():
                _download_image(info.get("poster_url", ""), poster_dest, output_root)
            show_meta = {
                "id":           f"scrap-pub-{info['id']}",
                "webpage_url":  info["url"],
                "extractor":    "scrap-pub",
                "title":        title,
                "title_ru":     info.get("title_ru"),
                "title_orig":   info.get("title_orig"),
                "kind":         "series",
                "release_year": int(year) if year else None,
                "description":  info.get("description"),
                "genres":       info.get("genres", []),
                "directors":    info.get("directors", []),
                "countries":    info.get("countries", []),
                "rating_imdb":  info.get("rating_imdb"),
                "imdb_id":      info.get("imdb_id"),
                "rating_kp":    info.get("rating_kp"),
                "kinopoisk_id": info.get("kinopoisk_id"),
                "thumbnail":    info.get("poster_url"),
            }
            show_info_dest.write_text(
                json.dumps(show_meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        for season_num, sd in info.get("seasons_data", {}).items():
            if only is not None and season_num != only[0]:
                continue
            season_dir = root_dir / f"Season {season_num:02d}"
            season_dir.mkdir(exist_ok=True)
            for ep in sd["episodes"]:
                if only is not None and ep["episode"] != only[1]:
                    continue
                stem_leaf = _episode_stem(show_dir, ep["season"], ep["episode"], ep.get("title"))
                stem_rel  = f"{show_dir}/Season {season_num:02d}/{stem_leaf}"
                stems.append(stem_rel)
                # Thumbnail
                thumb_url  = ep.get("thumb")
                thumb_dest = season_dir / f"{stem_leaf}-thumb.jpg"
                if thumb_url and not thumb_dest.exists():
                    time.sleep(random.uniform(0.2, 0.5))
                    _download_image(thumb_url, thumb_dest, output_root)
                # Episode info.json
                ep_meta = {
                    "id":             f"scrap-pub-{info['id']}-s{ep['season']}e{ep['episode']}",
                    "webpage_url":    episode_url(info["id"], ep["season"], ep["episode"]),
                    "extractor":      "scrap-pub",
                    "series":         title,
                    "title_ru":       info.get("title_ru"),
                    "title_orig":     info.get("title_orig"),
                    "series_id":      info["id"],
                    "season_number":  ep["season"],
                    "episode_number": ep["episode"],
                    "title":          ep.get("title") or f"S{ep['season']:02d}E{ep['episode']:02d}",
                    "duration":       ep.get("duration_sec"),
                    "thumbnail":      ep.get("thumb"),
                    "release_year":   int(year) if year else None,
                    "media_id":       ep.get("media_id"),
                }
                (season_dir / f"{stem_leaf}.info.json").write_text(
                    json.dumps(ep_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    return stems
