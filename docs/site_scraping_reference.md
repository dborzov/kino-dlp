# Site Scraping Reference

> One-line summary: structural conventions scrap-pub expects from kino-style,
> on-demand video websites — auth, HTML selectors, HLS manifest layout, naming.
>
> Last updated: 2026-04-13

scrap-pub is a generic scraper for a category of sites we call **kino-style,
on-demand video websites**: PHP/Yii2-based on-demand catalogs with Russian-language
UI chrome, sitting behind Cloudflare, serving signed HLS via a rotating CDN.

This document captures the concrete conventions scrap-pub was designed against. It
is a **reference**, not a targeting instruction — the user's chosen `website` in
config.json determines which site the scraper actually hits. Whether that site
matches these conventions is up to the user.

Nothing in this file identifies a specific third-party brand; the conventions below
are shared by a number of sites in this category.

---

## Auth

Cookie-based session. Required cookies (captured from a logged-in Chrome browser
on the target site):

| Cookie | Purpose |
|--------|---------|
| `_identity` | Session identity (PHP Yii2 auth) |
| `token` | User auth token |
| `_csrf` | CSRF token |
| `PHPSESSID` | PHP session |
| `cf_clearance` | Cloudflare clearance — **tied to TLS fingerprint** |
| `__cflb` | Cloudflare load balancer |
| `_ga`, `_ga_*` | Google Analytics (keep to look legit) |

**Cloudflare bypass:** `cf_clearance` is validated against the client's TLS
fingerprint. Plain `httpx` / `requests` fail (403). Use `curl-cffi` with
`impersonate="chrome136"` — it reproduces Chrome's BoringSSL TLS fingerprint and
passes the check.

```python
from curl_cffi import requests
sess = requests.Session(impersonate="chrome136")
sess.headers.update(BROWSER_HEADERS)
sess.cookies.update(AUTH_COOKIES)
```

**Cookie expiry:** `cf_clearance` and session cookies expire. When requests start
returning 403 or redirect to a login page, re-capture cookies from a fresh Chrome
browser session and upload them via `scrap-pub cookies FILE`. `chrome138` is not
yet in curl-cffi 0.15 — `chrome136` is the closest available and passes
Cloudflare's check.

**Follow-up request headers:** set `referer: {website}/...` and
`sec-fetch-site: same-origin` on any request that isn't the first page load —
mimics natural in-browser navigation. The scraper builds the referer from the
`website` config key at runtime.

**Rate limiting:** sleep `random.uniform(0.8, 2.0)` seconds between sequential
page fetches (e.g. per-season requests). Single-page requests need no delay.

---

## Site Sections

All section paths are relative to the configured `website` base URL:

| Path | Content |
|------|---------|
| `/movie` | Movies |
| `/serial` | TV Series |
| `/anime` | Anime |
| `/3d` | 3D Movies |
| `/concert` | Concerts |
| `/documovie` | Documentary films |
| `/docuserial` | Documentary series |
| `/tvshow` | TV Shows |
| `/sport` | Sport |
| `/popular` | New/popular (front page) |

### Filtering / sorting query params

Append to any section URL:
- `?order=added&period=all` — sort by date added, all time
- `?order=views&period=month` — sort by views this month
- `?page=N` — pagination (1-based)

### Search

`GET /item/search?query={text}` — returns an HTML results page with the same card
format.

---

## Listing Page — Item Cards

**Selector:** `.col-xs-4.col-sm-3` — each is one item card.

```
div.col-xs-4.col-sm-3
  div.item-poster
    a[href="/item/view/{id}/{slug}"]   ← item URL
      img.img-responsive               ← poster (medium size)
    div.bottomcenter-2x
      a[href*="imdb.com"]              ← IMDB rating
      a[href*="kinopoisk.ru"]          ← KP rating
  div.item-info
    div.item-title  a[title="{ru}"]    ← Russian title
    div.item-author a[title="{en}"]    ← Original/English title
```

**Item URL pattern:** `/item/view/{id}/{slug}`
- `id` — numeric, stable identifier
- `slug` — URL-friendly title, cosmetic only (can be any string, ignored by server)

**Poster URL patterns** (CDN — no auth needed to download):
- Small (favorites cards): `https://m.pushbr.com/poster/item/small/{id}.jpg`
- Medium (listing pages):  `https://m.pushbr.com/poster/item/medium/{id}.jpg`
- Large (detail pages):    `https://m.pushbr.com/poster/item/big/{id}.jpg`
- Episode thumbnail:       `https://m.pushbr.com/thumb/{path}/1280.jpg` (path from PLAYER_PLAYLIST)

---

## Item Detail Page

URL: `{website}/item/view/{id}/{slug}`

### Titles

In `<h3>` inside `.padding`:
```html
<h3>
  Громовержцы* 3D          ← Russian title (first text node)
  <small class="text-muted">Thunderbolts*  HD  + AC3</small>  ← EN title + tech info
</h3>
```
EN title = first token of `small.text-muted` before quality tags (`HD`, `UHD`,
`4K`, `+`).

### Scope Isolation — Avoiding "Похожие" Contamination

The item detail page has exactly **two** `.row` children under `.padding`:
- `row[0]` — poster + action buttons + tabs (`#audio`, plot) +
  `table.table-striped` ← **parse this**
- `row[1]` — "Похожие" (related items) in the same card format as listing
  pages ← **skip**

**Rule:** always resolve the `.row` that contains `table.table-striped` as the
parse root. Never select `.item-poster`, `.item-title`, etc. at `.padding` scope
— they match related items too.

### Metadata Table

`table.table-striped tbody tr` — label/value pairs within the main content row:

| `<strong>` label | Content |
|-----------------|---------|
| Рейтинг | KP and IMDB ratings + vote counts; links contain KP/IMDB IDs |
| Год выхода | Release year |
| Страна | Countries (list of `<a>`) |
| Жанр | Genres (list of `<a>`) |
| Создатель | Director(s) |
| В ролях | Cast |
| Длительность | Duration — movie: `HH:MM:SS / (N мин)`, series: avg episode + total |
| Возраст | Age rating (`16+`, `18+`, …) |
| Субтитры | Language names as text, e.g. `Русские, Английские` — strip "Добавить" button text |

**KP ID extraction:** `href="http://www.kinopoisk.ru/film/{kp_id}"`
**IMDB ID extraction:** `href="http://www.imdb.com/title/{tt_id}"`

### Audio Tracks

Tab pane `div#audio` (inside the main content row) contains `<ol><li>`:
```
Русский. Дубляж. Red Head Sound. AAC
Русский. Многоголосый. LostFilm. AAC
Английский. Оригинал. AAC
Русский. Дубляж. MovieDalen. AC3
```
Format per entry: `{Language}. {DubType}. {Studio}. {Codec}`

**DubType values:** `Дубляж` (full dub), `Многоголосый` (multi-voice),
`Двухголосый` (two-voice), `Оригинал` (original)

---

## Series — Seasons and Episodes

### Season Selector

Season buttons appear below the player:
```html
<span class="season-title">Сезоны: </span>
<a href="/item/view/{id}/s1e1" class="btn btn-success">1</a>          ← current (filled)
<a href="/item/view/{id}/s2e1" class="btn btn-outline-success">2</a>  ← other seasons
```
Selector: `a[href^="/item/view/{id}/s"][href$="e1"]` — matches only `s{N}e1`
links, not mid-episode links.

### Episode Data — `window.PLAYER_PLAYLIST`

Each season page (`/item/view/{id}/s{N}e1`) embeds:
```js
window.PLAYER_PLAYLIST = [{...}, ...];
```

Each entry:
```json
{
  "id": 100564,
  "media_id": 1011088,
  "season": 1,
  "episode": 1,
  "episode_title": "Конец",
  "duration": 4497,
  "yeaer": 2024,
  "poster": "https://m.pushbr.com/poster/item/medium/{id}.jpg",
  "thumb": "https://m.pushbr.com/thumb/{path}/1280.jpg",
  "manifest": "https://digital-cdn.net/hls4/.../{media_id}.m3u8?loc=nl"
}
```

**Known site typo:** `"yeaer"` (not `"year"`).
**Manifest URL:** signed HLS stream, session-scoped — do not cache across sessions.
**Playlist scope:** contains only the episodes for the season of the page being
fetched.

### Per-Season Audio and Subtitles

**Audio tracks and subtitles are per-season, not per-episode.**
- `s{N}e1` and `s{N}e7` of the same season return identical audio lists — one
  fetch per season suffices.
- The main item page (no season suffix) reflects **Season 1's** audio and
  subtitle data only.
- Later seasons often add new dubbing studios mid-run. Example — Fallout S2: 17
  audio tracks vs S1's 11 (6 studios added, 1 removed).
- **Rule:** for complete audio/subtitle data, fetch each `/item/view/{id}/s{N}e1`
  separately and parse `#audio ol li` and `table.table-striped` from each page.

**Season URL as input:** if a URL ends in `/s{N}e{M}`, strip to `/item/view/{id}`
to get the root page (show-level metadata + full season list), then re-fetch each
season individually.

---

## Закладки — User Favorites Playlists

### Discovery: `/favorites`

Lists all user playlists as `.card.favorites` cards:
```html
<div class="card card-inverse dark favorites">
  <div class="card-block">
    <h6 class="card-title"><a href="/favorites/view?id=2721421">Лена</a></h6>
    <span class="card-subtitle mb-2 text-muted">Фильмов 7</span>
  </div>
</div>
```
- List id: from `href` → `?id={list_id}`
- Item count: last word of `span.card-subtitle` (e.g. `"Фильмов 7"` → `7`) —
  available without loading the list

**Name matching:** the site uses short/informal names (e.g. `Лена` not `Елена`).
Match fuzzy: strip leading `Е/е` before comparing case-insensitively.

### Playlist Items: `/favorites/view?id={list_id}`

Different layout from section listing pages — horizontal cards, not poster grid.
All items appear on a **single page** (no pagination observed).

**Card root:** `div[id^="favorites-item-{item_id}"]`

Data attributes on root — quick metadata without a follow-up request:
- `data-year` — release year
- `data-imdb` — IMDB rating
- `data-kinopoisk` — KP rating
- `data-title` — short title (search form)
- `data-created` — unix timestamp added to favorites

```html
<div class="col-xs-12 col-sm-12 col-md-6"
     id="favorites-item-79387"
     data-year="2019" data-imdb="7.4" data-kinopoisk="0"
     data-title="Супер" data-created="1775929654">
  <div class="item r">
    <div class="item-media">
      <a class="item-media-content" href="/item/view/79387/...">
        <img src="https://m.pushbr.com/poster/item/small/{id}.jpg"/>
      </a>
    </div>
    <div class="item-info">
      <div class="item-title"><a>Суперстроения…</a></div>     ← RU title
      <div class="item-author"><a>Superstructures…</a></div>  ← EN title  (1st .item-author)
      <div class="item-author">2019, <span>реж. …</span></div>  ← year + director  (2nd)
      <div class="item-author"><a href="/docuserial?genre=79">Строительство</a></div>  ← genres  (3rd)
    </div>
  </div>
</div>
```

---

## HLS Master Manifest

Each episode/movie has a signed manifest URL in
`window.PLAYER_PLAYLIST[N].manifest`. Fetching it returns
`Content-Type: application/vnd.apple.mpegurl`.

**CDN hosts observed:** `cdn2cdn.com`, `cdn2site.com`, `digital-cdn.net` — varies
per request. Manifest URLs are **signed and session-scoped** — do not cache
across sessions or share between users.

### Manifest structure

```
#EXTM3U
#EXT-X-VERSION:4
#EXT-X-INDEPENDENT-SEGMENTS

# Subtitle tracks — relative URIs, resolve against CDN host of manifest URL
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="RUS #01",LANGUAGE="rus",URI="/hls/…"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="RUS #02 Forced",LANGUAGE="rus",URI="/hls/…"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="sub",NAME="ENG #03",LANGUAGE="eng",URI="/hls/…"

# Audio tracks — absolute URIs, repeated once per video-quality group
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio1080",NAME="01. Дубляж. MovieDalen (RUS)",LANGUAGE="rus",DEFAULT=YES,URI="https://…"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio720", NAME="01. Дубляж. MovieDalen (RUS)",LANGUAGE="rus",DEFAULT=YES,URI="https://…"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio480", NAME="01. Дубляж. MovieDalen (RUS)",LANGUAGE="rus",DEFAULT=YES,URI="https://…"

# Video renditions — one per quality tier, immediately followed by stream URI
#EXT-X-STREAM-INF:BANDWIDTH=3027404,RESOLUTION=1920x928,CODECS="avc1.640028,mp4a.40.2",FRAME-RATE=23.976,AUDIO="audio1080",SUBTITLES="sub"
https://…/index-v1.m3u8?loc=nl

# I-frame streams (for seeking) — skip, not needed for download
#EXT-X-I-FRAME-STREAM-INF:…
```

### Video streams

`#EXT-X-STREAM-INF` attributes:
- `BANDWIDTH` — bits/s (total, video + audio muxed reference)
- `RESOLUTION` — `{width}x{height}` pixels
- `CODECS` — e.g. `"avc1.640028,mp4a.40.2"` (H.264 + AAC)
- `FRAME-RATE` — e.g. `23.976` or `24`
- `VIDEO-RANGE` — `SDR` (no HDR observed yet)
- `AUDIO` — references an audio GROUP-ID (`audio1080`, `audio720`, `audio480`)
- `SUBTITLES` — always `"sub"`

Typical quality tiers:

| Resolution | Bandwidth | Audio group |
|-----------|-----------|-------------|
| 1920×H    | ~3–6 Mbps | audio1080   |
| 1280×H    | ~1.8–2 Mbps | audio720  |
| 720×H     | ~0.7–0.9 Mbps | audio480 |

Height varies by film's aspect ratio (e.g. 1920×800 for 2.40:1, 1920×928 for
~2.07:1).

### Audio tracks

`GROUP-ID` naming: `audio{N}` where N is the height of the paired video tier
(1080, 720, 480). The **same set of tracks** appears in every group — same NAME
and LANGUAGE, different CDN path. Deduplicate by `NAME` when displaying or
selecting.

Audio `NAME` format: `"{index}. {DubType}. {Studio} ({LANG})"` or
`"{index}. Оригинал ({LANG})"`. `DEFAULT=YES` marks the first (default) track.

### Subtitle tracks

- `URI` is **relative** — resolve as `scheme://host_of_manifest_url + URI`.
- Audio URIs are **absolute**.
- `NAME` format: `"{LANG} #{index}"`, with `" Forced"` suffix for forced tracks.
- 3-letter ISO-639-2 language codes (e.g. `rus`, `eng`, `ukr`, `fra`).
- Track count varies widely: a typical action movie has 5 (2 langs), a
  translation-heavy drama can have 42 (37 langs).

---

## og:title — Original Title Extraction

The target site sets the `og:title` meta tag as:

```
Russian Title / Original Title
```

Split on ` / ` to separate the two. The second part is the title in the content's
original language (French for French films, English for US/UK shows, Russian for
Russian originals, etc.).

This is the most reliable source for both titles and should be preferred over
parsing the `<h3>` / `<small>` tags. When `og:title` contains no ` / `, the title
is the same in both languages.

---

## Plex Directory Structure

`scraper.scaffold()` creates Plex-ready directories under `{output_dir}`.
Filenames use the **original-language title** from `og:title`.

### Movie

```
output/
  C'était mieux demain(2025)/
    C'était mieux demain(2025).jpg        ← poster (named like the future MKV)
    C'était mieux demain(2025).info.json  ← metadata: both titles, IMDB, genres, cast, audio tracks
```

### TV Series

```
output/
  Big Mistakes(2026)/
    poster.jpg
    show.info.json                        ← show-level metadata
    Season 01/
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace-thumb.jpg
      Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace.info.json
      ...
```

### Filename sanitisation rules

(Implemented inline in `scrap_pub/daemon/scraper.py`.)

- Colon + surrounding whitespace → single space: `"Foo: Bar"` → `"Foo Bar"`
- Removed: `; ! * " \ | < > ?`
- Kept: apostrophe `'` (NTFS-legal, common in French/English titles), accented chars
- Format: `Title(Year)` — no space before `(`
- Episode stem: `Title(Year) - s{SS}e{EE} - Episode Title`

### Plex stem

The relative path from `{output_dir}` to the file, without extension. Stored on
the `tasks` row as `plex_stem`:

```
# movie
C'était mieux demain(2025)/C'était mieux demain(2025)

# TV episode
Big Mistakes(2026)/Season 01/Big Mistakes(2026) - s01e01 - Get Your Nonna a Necklace
```
