from datetime import datetime

from pydantic import BaseModel


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
    source: str = "youtube"
    max_results: int = 5


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
