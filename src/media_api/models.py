"""Request and response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The sites that /media/info and /media/search can search.
Source = Literal["youtube", "soundcloud"]


class MediaInfo(BaseModel):
    """The body of GET /media/info, a track's metadata and its stream URL.

    Attributes:
        source: The yt-dlp extractor that handled the track in lower case, such as "youtube".
        title: The track title.
        duration_seconds: The length in whole seconds, or None when the source does not report it.
        duration_formatted: The length as M:SS or H:MM:SS, or None when it is unknown.
        uploader: The uploader or channel name, if the source reports one.
        thumbnail_url: The thumbnail image URL, if the source reports one.
        webpage_url: The track's page URL.
        stream_url: The direct stream URL, or None when extraction returned none.
        stream_url_expires_at: When the stream URL stops working, in UTC.
        is_live: Whether the track is a live stream.
    """

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
    """The body of POST /media/search.

    Attributes:
        query: The search text.
        source: The site to search.
        max_results: How many results to return, from 1 to 25. MAX_SEARCH_RESULTS caps it further.
    """

    query: str
    source: Source = "youtube"
    max_results: int = Field(default=5, ge=1, le=25)


class SearchResult(BaseModel):
    """One search result, without a stream URL."""

    title: str
    duration_seconds: int | None
    duration_formatted: str | None
    thumbnail_url: str | None
    webpage_url: str


class SearchResponse(BaseModel):
    """The body that POST /media/search returns."""

    results: list[SearchResult]


class PlaylistTrack(BaseModel):
    """One track of a playlist or album, without a stream URL."""

    title: str
    webpage_url: str
    duration_seconds: int | None = None
    duration_formatted: str | None = None
    thumbnail_url: str | None = None


class PlaylistResponse(BaseModel):
    """The body of GET /media/playlist, the tracks in their original order."""

    tracks: list[PlaylistTrack]


class HealthResponse(BaseModel):
    """The body of GET /health."""

    status: str
    service: str
    version: str
