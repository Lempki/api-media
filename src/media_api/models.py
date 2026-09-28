from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The sites that /media/info and /media/search can search.
Source = Literal["youtube", "soundcloud"]


class MediaInfo(BaseModel):
    source: str
    title: str
    duration_seconds: int | None
    duration_formatted: str | None
    uploader: str | None
    thumbnail_url: str | None
    webpage_url: str
    stream_url: str | None
    stream_url_expires_at: datetime | None
    is_live: bool


class SearchRequest(BaseModel):
    query: str
    source: Source = "youtube"
    max_results: int = Field(default=5, ge=1, le=25)


class SearchResult(BaseModel):
    title: str
    duration_seconds: int | None
    duration_formatted: str | None
    thumbnail_url: str | None
    webpage_url: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


class PlaylistTrack(BaseModel):
    title: str
    webpage_url: str
    duration_seconds: int | None = None
    duration_formatted: str | None = None
    thumbnail_url: str | None = None


class PlaylistResponse(BaseModel):
    tracks: list[PlaylistTrack]


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
