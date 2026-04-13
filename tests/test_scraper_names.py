"""
Tests for the naming / path helpers in scrap_pub.daemon.scraper.

These test the pure filename logic only — no HTTP calls, no session needed.

The sanitiser has three jobs that the tests below exercise independently:

  1. Produce filenames that are trivial to quote in a POSIX shell — no
     metacharacters, no leading `-`, no path separators.
  2. Produce filenames that Plex's scanner can parse correctly — in particular,
     no stray `(YYYY)`, `{tmdb-N}`, or `[info]` fragments in the title that
     would fight Plex's own filename conventions (see docs/plex_naming.md).
  3. Preserve multilingual content — Cyrillic, CJK, accented Latin — so the
     original-language title from the target site round-trips into the output.
"""

import pytest

from scrap_pub.daemon.scraper import _dir_name, _episode_stem, _sanitise

# ── _sanitise: basics ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Normal Title",              "Normal Title"),
    ("Colon: Here",               "Colon Here"),           # colon → space
    ("Leading: colon",            "Leading colon"),
    ("Semicolon;here",            "Semicolonhere"),        # ; removed
    ("Exclamation!Mark",          "ExclamationMark"),      # ! removed
    ("Star Wars: A New Hope",     "Star Wars A New Hope"),
    ("C'était mieux demain",      "Cétait mieux demain"),  # apostrophe stripped
    ("L'avenger: butts",          "Lavenger butts"),       # user's example
    ('Say "Hello"',               "Say Hello"),            # quotes stripped
    ("Mr. Robot",                 "Mr Robot"),             # dot stripped
    ("Fast & Furious",            "Fast Furious"),         # ampersand stripped
    ("Rock 'n' Roll",             "Rock n Roll"),          # apostrophes stripped
    ("Hello, World",              "Hello World"),          # comma stripped
    ("(Parens) kept?",            "Parens kept"),          # parens + ? stripped
    ("Spider-Man",                "Spider-Man"),           # interior hyphen kept
    ("  spaces  ",                "spaces"),               # strip whitespace
    ("double  spaces",            "double spaces"),        # collapse inner spaces
    ("A: B: C",                   "A B C"),                # multiple colons
])
def test_sanitise_basics(raw, expected):
    assert _sanitise(raw) == expected


# ── _sanitise: Plex-special characters ────────────────────────────────────────
# Plex's filename scanner treats `(YYYY)` as the release year, `{tmdb-N}` /
# `{tvdb-N}` / `{imdb-ttN}` as agent-ID hints, and `[info]` as optional extra
# info that it strips before matching. A title containing any of those would
# confuse Plex or collide with the `(YYYY)` that _dir_name appends, so the
# sanitiser must remove them from the title portion.

@pytest.mark.parametrize("raw, expected", [
    ("Movie (2024)",                  "Movie 2024"),              # parens around year
    ("Borat (Subsequent Moviefilm)",  "Borat Subsequent Moviefilm"),
    ("Show {tmdb-123}",               "Show tmdb-123"),           # curly = Plex ID
    ("Show {tvdb-456}",               "Show tvdb-456"),
    ("Show {imdb-tt789}",             "Show imdb-tt789"),
    ("Movie [1080p Bluray]",          "Movie 1080p Bluray"),      # [] = optional info
    ("Nested ([1080p])",              "Nested 1080p"),
    ("Title (Director's Cut)",        "Title Directors Cut"),
])
def test_sanitise_strips_plex_special_chars(raw, expected):
    assert _sanitise(raw) == expected


# ── _sanitise: shell metacharacters ──────────────────────────────────────────
# Every character bash/zsh treats specially must be removed so the filename is
# safe to pass through a shell even unquoted.

@pytest.mark.parametrize("raw, expected", [
    ("AC/DC",                 "ACDC"),        # forward slash (path sep)
    ("A\\B",                  "AB"),          # backslash (path sep on Win)
    ("9/11",                  "911"),
    ("Movie|grep",            "Moviegrep"),   # pipe
    ("Movie>out",             "Movieout"),    # redirect
    ("Movie<in",              "Moviein"),     # redirect
    ("Movie$var",             "Movievar"),    # variable expansion
    ("Movie`pwd`",            "Moviepwd"),    # backtick subshell
    ("Movie#1",               "Movie1"),      # comment
    ("Movie~user",            "Movieuser"),   # tilde expansion
    ("Movie*",                "Movie"),       # glob
    ("Movie??",               "Movie"),       # glob
    ("Movie{1,2}",            "Movie12"),     # brace expansion
    ("$(rm -rf /)",           "rm -rf"),      # command substitution
    ("Movie; ls",             "Movie ls"),    # command chain
    ("Movie && rm",           "Movie  rm"),   # logical and (collapsed later)
])
def test_sanitise_strips_shell_metacharacters(raw, expected):
    # Note: re.sub collapses runs of whitespace, so "Movie && rm" → "Movie  rm"
    # still has two spaces pre-collapse; the assertion uses the post-collapse
    # form.
    result = _sanitise(raw)
    # Collapse the expected value the same way so the test is robust to the
    # exact number of intermediate spaces.
    import re as _re
    assert result == _re.sub(r"\s+", " ", expected).strip(" -_")


# ── _sanitise: control chars and whitespace ──────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("A\nB",           "A B"),        # newline → space
    ("A\tB",           "A B"),        # tab → space
    ("A\rB",           "A B"),        # carriage return → space
    ("A\u00a0B",       "A B"),        # non-breaking space → space
    ("A\x00B",         "AB"),         # null byte stripped
    ("A\x07B",         "AB"),         # bell stripped
    ("A\x1bB",         "AB"),         # escape stripped
])
def test_sanitise_strips_control_chars(raw, expected):
    assert _sanitise(raw) == expected


# ── _sanitise: exotic Unicode punctuation ────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("\u201cHello\u201d",         "Hello"),           # curly double quotes
    ("\u2018Hello\u2019",         "Hello"),           # curly single quotes
    ("\u00abBonjour\u00bb",       "Bonjour"),         # French guillemets
    ("\u201eHallo\u201c",         "Hallo"),           # German quotes
    ("Title \u2013 Subtitle",     "Title Subtitle"),  # en-dash surrounded by spaces
    ("Title\u2014Subtitle",       "TitleSubtitle"),   # em-dash, no spaces
    ("WALL\u00b7E",               "WALLE"),           # middle dot
    ("Waiting\u2026",             "Waiting"),         # unicode ellipsis
    ("Movie \U0001f3ac",          "Movie"),           # clapperboard emoji
    ("\u2605\u2605\u2605",        ""),                # black stars → empty
    ("Straße",                    "Straße"),          # German sharp-s preserved
])
def test_sanitise_strips_unicode_punctuation(raw, expected):
    assert _sanitise(raw) == expected


# ── _sanitise: multilingual letters preserved ────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Большая ошибка",   "Большая ошибка"),   # Russian Cyrillic
    ("C'était demain",   "Cétait demain"),    # French (apostrophe dropped)
    ("東京物語",           "東京物語"),            # Japanese kanji
    ("한국어",             "한국어"),              # Korean hangul
    ("Ñoño",             "Ñoño"),              # Spanish ñ
    ("Αθήνα",            "Αθήνα"),             # Greek
    ("العربية",           "العربية"),             # Arabic
])
def test_sanitise_preserves_unicode_letters(raw, expected):
    assert _sanitise(raw) == expected


# ── _sanitise: degenerate / edge inputs ──────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("???",          ""),        # only punctuation
    ("...",          ""),
    ("'''",          ""),
    ("   ",          ""),        # only whitespace
    ("",             ""),        # empty
    ("---Title---",  "Title"),   # edge hyphens stripped
    ("___Title___",  "Title"),   # edge underscores stripped
    ("- Title -",    "Title"),   # edge hyphens with spaces
    ("---",          ""),        # only hyphens
    ("_",            ""),        # only underscore
    ("- - -",        ""),        # only hyphens + spaces
])
def test_sanitise_degenerate(raw, expected):
    assert _sanitise(raw) == expected


# ── _sanitise: known-good titles pass through unchanged ──────────────────────

@pytest.mark.parametrize("title", [
    "Toy Story 4",
    "WALL-E",
    "Blade Runner 2049",
    "Se7en",
    "24",
    "1984",
    "Terminator 2",
])
def test_sanitise_passthrough_safe_titles(title):
    assert _sanitise(title) == title


# ── _sanitise: output is always shell-safe ───────────────────────────────────

@pytest.mark.parametrize("raw", [
    "Movie $(rm -rf /); echo 'hi'",
    "../../../etc/passwd",
    "Movie | tee /tmp/log",
    '"evil"; rm -rf ~/; #',
    "\x00\x01\x02binary",
    "--flag-lookalike",
    "{tmdb-999} [1080p] (2020)",
])
def test_sanitise_output_is_shell_safe(raw):
    result = _sanitise(raw)
    # No shell metacharacters.
    for bad in "$()`;'\"<>|&*?[]{}~#\\/":
        assert bad not in result, f"{bad!r} must be stripped from {raw!r}"
    # No leading `-` (would be parsed as a flag).
    assert not result.startswith("-"), f"{result!r} starts with `-`"
    # No control chars.
    for ch in result:
        assert ord(ch) >= 0x20 or ch in "\t", f"control char in {result!r}"


# ── _dir_name ─────────────────────────────────────────────────────────────────

def test_dir_name_with_year():
    assert _dir_name("Big Mistakes", 2026) == "Big Mistakes(2026)"


def test_dir_name_without_year_none():
    assert _dir_name("Untitled", None) == "Untitled"


def test_dir_name_without_year_zero():
    assert _dir_name("Untitled", 0) == "Untitled"


def test_dir_name_sanitises_colon():
    result = _dir_name("Star Wars: A New Hope", 1977)
    assert ":" not in result
    assert result == "Star Wars A New Hope(1977)"


def test_dir_name_french_title():
    result = _dir_name("C'était mieux demain", 2025)
    assert result == "Cétait mieux demain(2025)"


def test_dir_name_russian_title():
    result = _dir_name("Большая ошибка", 2026)
    assert result == "Большая ошибка(2026)"


# ── _dir_name vs Plex year-detection traps ───────────────────────────────────
# Plex extracts the year from a trailing `(YYYY)` in the folder name. If the
# title itself contains parens or a year-like number, the sanitised output
# must still produce exactly one trailing `(YYYY)`.

def test_dir_name_strips_leading_parens_in_title():
    # Without stripping, "(2024) Movie(2026)" has two years.
    result = _dir_name("(2024) Movie", 2026)
    assert result == "2024 Movie(2026)"
    # There must be exactly one `(...)` group and it's the trailing year.
    assert result.count("(") == 1
    assert result.count(")") == 1
    assert result.endswith("(2026)")


def test_dir_name_strips_trailing_parens_in_title():
    result = _dir_name("Borat (Subsequent Moviefilm)", 2020)
    assert result == "Borat Subsequent Moviefilm(2020)"
    assert result.count("(") == 1
    assert result.count(")") == 1


def test_dir_name_year_as_title():
    # Title "1984" directed in 1984 must produce "1984(1984)".
    assert _dir_name("1984", 1984) == "1984(1984)"


def test_dir_name_strips_plex_id_braces():
    # Curly braces would otherwise be parsed by Plex as an agent-id hint.
    result = _dir_name("Show {tmdb-999}", 2020)
    assert "{" not in result
    assert "}" not in result
    assert result == "Show tmdb-999(2020)"


def test_dir_name_strips_optional_info_brackets():
    result = _dir_name("Movie [1080p Bluray]", 2020)
    assert "[" not in result
    assert "]" not in result
    assert result == "Movie 1080p Bluray(2020)"


def test_dir_name_empty_after_sanitise_falls_back():
    # Degenerate input — all punctuation — must not yield "(2026)" which
    # starts with `(` and is unparseable by Plex.
    assert _dir_name("???", 2026) == "Untitled(2026)"
    assert _dir_name("???", None) == "Untitled"
    assert _dir_name("...", 1999) == "Untitled(1999)"


def test_dir_name_strips_path_separators():
    # A title containing `/` must never become a path traversal.
    result = _dir_name("AC/DC Live", 1992)
    assert "/" not in result
    assert result == "ACDC Live(1992)"


def test_dir_name_never_starts_with_dash():
    # A title like "- The End -" would otherwise produce a filename starting
    # with `-` which shells parse as a flag.
    result = _dir_name("- The End -", 2020)
    assert not result.startswith("-")
    assert result == "The End(2020)"


def test_dir_name_shell_injection_defanged():
    raw = "Movie $(rm -rf /); echo 'hi'"
    result = _dir_name(raw, 2026)
    # `(` and `)` are allowed in the trailing year suffix — check only the
    # title portion for shell metacharacters.
    assert result.endswith("(2026)")
    title_part = result[: -len("(2026)")]
    for bad in "$()`;'\"<>|&*?[]{}~#\\/":
        assert bad not in title_part, f"{bad!r} in title portion {title_part!r}"
    assert not result.startswith("-")


def test_dir_name_japanese_title():
    result = _dir_name("東京物語", 1953)
    assert result == "東京物語(1953)"


# ── _episode_stem ─────────────────────────────────────────────────────────────

def test_episode_stem_basic():
    stem = _episode_stem("Big Mistakes(2026)", season=1, episode=3,
                         ep_title="Get Your Nonna a Necklace")
    assert stem == "Big Mistakes(2026) - s01e03 - Get Your Nonna a Necklace"


def test_episode_stem_no_title():
    stem = _episode_stem("Show(2026)", season=2, episode=10, ep_title=None)
    assert stem == "Show(2026) - s02e10"


def test_episode_stem_pads_numbers():
    stem = _episode_stem("Show(2026)", season=1, episode=1, ep_title=None)
    assert "s01e01" in stem


def test_episode_stem_sanitises_episode_title():
    stem = _episode_stem("Show(2026)", season=1, episode=1, ep_title="Episode: One!")
    assert ":" not in stem
    assert "!" not in stem
    assert "Episode One" in stem


def test_episode_stem_contains_show_dir():
    stem = _episode_stem("MyShow(2024)", season=1, episode=5, ep_title="Ep Five")
    assert stem.startswith("MyShow(2024)")


def test_episode_stem_double_digit_season():
    stem = _episode_stem("Show(2020)", season=12, episode=3, ep_title=None)
    assert "s12e03" in stem


def test_episode_stem_strips_quotes_and_parens_in_ep_title():
    stem = _episode_stem("Show(2026)", 1, 1, "Episode: 'Hello' (Part 1)")
    # The `(2026)` in show_dir is load-bearing and must survive; we only
    # assert that the episode title portion was cleaned.
    assert stem == "Show(2026) - s01e01 - Episode Hello Part 1"
    ep_portion = stem.split(" - ", 2)[2]
    assert ":" not in ep_portion
    assert "'" not in ep_portion
    assert "(" not in ep_portion
    assert ")" not in ep_portion


def test_episode_stem_degenerate_ep_title_dropped():
    # If the episode title sanitises to empty, no trailing " - " should appear.
    stem = _episode_stem("Show(2026)", 1, 1, "???")
    assert stem == "Show(2026) - s01e01"
    assert not stem.endswith(" - ")


def test_episode_stem_preserves_show_dir_parens():
    # _episode_stem is passed the already-formed show_dir (which includes the
    # year in parens) and must NOT re-sanitise it — the caller's `(YYYY)` is
    # load-bearing for Plex.
    stem = _episode_stem("Show(2026)", 1, 1, "Pilot")
    assert "Show(2026)" in stem
    assert stem == "Show(2026) - s01e01 - Pilot"
