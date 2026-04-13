from scrap_pub.models import Movie, Person, TVSeries


def test_movie_basic():
    m = Movie(id="1", source="test-site", url="https://example.com/item/1", title="Test")
    assert m.kind == "movie"
    assert m.genres == []


def test_series_basic():
    s = TVSeries(id="2", source="test-site", url="https://example.com/item/2", title="Show")
    assert s.kind == "series"
    assert s.episodes == []


def test_person():
    p = Person(name="Director Name", role="director")
    assert p.name == "Director Name"
