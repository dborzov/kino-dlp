"""
Tests for the naming / path helpers in scrap_pub.daemon.scraper.

These test the pure filename logic only — no HTTP calls, no session needed.
"""

import pytest

from scrap_pub.daemon.scraper import _dir_name, _episode_stem, _sanitise

# ── _sanitise ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("Normal Title",              "Normal Title"),
    ("Colon: Here",               "Colon Here"),          # colon → space
    ("Leading: colon",            "Leading colon"),
    ("Semicolon;here",            "Semicolonhere"),        # ; removed
    ("Exclamation!Mark",          "ExclamationMark"),      # ! removed
    ("Star Wars: A New Hope",     "Star Wars A New Hope"),
    ("C'était mieux demain",      "C'était mieux demain"), # apostrophe kept
    ("  spaces  ",                "spaces"),               # strip whitespace
    ("double  spaces",            "double spaces"),        # collapse inner spaces
    ("A: B: C",                   "A B C"),               # multiple colons
])
def test_sanitise(raw, expected):
    assert _sanitise(raw) == expected


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
    assert result == "C'était mieux demain(2025)"


def test_dir_name_russian_title():
    result = _dir_name("Большая ошибка", 2026)
    assert result == "Большая ошибка(2026)"


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
    # colon replaced, ! removed
    assert ":" not in stem
    assert "!" not in stem
    # "Episode One" should appear
    assert "Episode One" in stem


def test_episode_stem_contains_show_dir():
    stem = _episode_stem("MyShow(2024)", season=1, episode=5, ep_title="Ep Five")
    assert stem.startswith("MyShow(2024)")


def test_episode_stem_double_digit_season():
    stem = _episode_stem("Show(2020)", season=12, episode=3, ep_title=None)
    assert "s12e03" in stem
