"""
Tests for scaffold()'s `only` filter and show-level short-circuit.

Uses a hand-built info dict so no HTTP / cookies are required. Exercises:

  1. `only=(season, episode)` restricts per-episode file creation to that
     one episode — siblings in the same season (and other seasons) do not
     get info.json / thumb files written.
  2. When show.info.json + poster.jpg already exist on disk, scaffold does
     not re-write show.info.json — callers rely on this to avoid redoing
     show-level work for every queued episode of a large series.
  3. The show-level description from `info["description"]` round-trips
     into show.info.json.
"""

import json
from unittest.mock import patch

from scrap_pub.daemon import scraper


def _fake_info() -> dict:
    return {
        "id":          "12345",
        "url":         "https://example.com/item/view/12345",
        "kind":        "series",
        "title_ru":    "Тестовый сериал",
        "title_orig":  "Test Show",
        "year":        "2024",
        "poster_url":  "https://example.com/poster.jpg",
        "description": "A show that exists only to test scaffold().",
        "genres":      ["Drama"],
        "seasons":     [1, 2],
        "seasons_data": {
            1: {
                "episodes": [
                    _ep(1, 1, "Pilot",  101),
                    _ep(1, 2, "Second", 102),
                    _ep(1, 3, "Third",  103),
                ],
                "audio_tracks": [],
                "subtitles":    [],
            },
            2: {
                "episodes": [
                    _ep(2, 1, "S2 premiere", 201),
                ],
                "audio_tracks": [],
                "subtitles":    [],
            },
        },
    }


def _ep(season: int, episode: int, title: str, media_id: int) -> dict:
    return {
        "season":       season,
        "episode":      episode,
        "title":        title,
        "thumb":        None,
        "media_id":     media_id,
        "duration_sec": 1800,
    }


def test_scaffold_only_creates_one_episode(tmp_path):
    """only=(1, 2) should produce exactly one episode info.json, not all."""
    # Configure a dummy website so episode_url() has a base to prepend
    scraper.set_website("https://example.com")

    with patch.object(scraper, "_download_image", return_value=True):
        stems = scraper.scaffold(_fake_info(), tmp_path, only=(1, 2))

    # Walk the tree and gather every info.json we emitted.
    show_dir = tmp_path / "Test Show(2024)"
    assert show_dir.is_dir()

    episode_jsons = sorted(
        p.relative_to(show_dir).as_posix()
        for p in show_dir.rglob("*.info.json")
        if p.name != "show.info.json"
    )
    assert episode_jsons == ["Season 01/Test Show(2024) - s01e02 - Second.info.json"]

    # And the returned stems list should likewise be scoped to the one episode.
    assert stems == ["Test Show(2024)/Season 01/Test Show(2024) - s01e02 - Second"]


def test_scaffold_show_level_short_circuits_when_present(tmp_path):
    """Second scaffold call must not overwrite an existing show.info.json."""
    scraper.set_website("https://example.com")

    with patch.object(scraper, "_download_image") as dl:
        # First call writes show.info.json + poster + episode files.
        dl.return_value = True
        # Pre-create poster.jpg via the mock's side effect so the .exists()
        # check in scaffold returns True on the second call below.
        def _fake_dl(url, dest, output_root):
            dest.write_bytes(b"\x89PNG\r\n\x1a\n")
            return True
        dl.side_effect = _fake_dl

        scraper.scaffold(_fake_info(), tmp_path, only=(1, 1))

        show_info_path = tmp_path / "Test Show(2024)" / "show.info.json"
        poster_path    = tmp_path / "Test Show(2024)" / "poster.jpg"
        assert show_info_path.exists()
        assert poster_path.exists()

        # Tamper with show.info.json so we can detect a re-write.
        show_info_path.write_text('{"sentinel": "do-not-touch"}', encoding="utf-8")

        # Second call (different episode) — should leave show.info.json alone.
        scraper.scaffold(_fake_info(), tmp_path, only=(1, 2))
        assert json.loads(show_info_path.read_text()) == {"sentinel": "do-not-touch"}


def test_scaffold_show_description_in_show_info_json(tmp_path):
    """The show-level info.json must include the scraped description field."""
    scraper.set_website("https://example.com")

    with patch.object(scraper, "_download_image", return_value=True):
        scraper.scaffold(_fake_info(), tmp_path, only=(1, 1))

    show_info = json.loads(
        (tmp_path / "Test Show(2024)" / "show.info.json").read_text()
    )
    assert show_info["description"] == "A show that exists only to test scaffold()."
    assert show_info["kind"] == "series"
