from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class Person(BaseModel):
    name: str
    role: Optional[str] = None


class Episode(BaseModel):
    season: int
    episode: int
    title: Optional[str] = None
    duration_sec: Optional[int] = None
    air_date: Optional[str] = None  # ISO date string


class MediaBase(BaseModel):
    id: str
    source: str
    url: str
    title: str
    title_ru: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    genres: list[str] = []
    countries: list[str] = []
    directors: list[Person] = []
    cast: list[Person] = []
    rating_kp: Optional[float] = None
    rating_imdb: Optional[float] = None
    kinopoisk_id: Optional[str] = None
    imdb_id: Optional[str] = None


class Movie(MediaBase):
    kind: str = "movie"
    duration_sec: Optional[int] = None


class TVSeries(MediaBase):
    kind: str = "series"
    seasons: list[int] = []
    episodes: list[Episode] = []
    status: Optional[str] = None
