"""The FastAPI application, its lifespan, and its routes."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, status

from . import cache
from .auth import require_auth
from .config import Settings, get_settings
from .extractor import fetch_info, fetch_playlist, search
from .logging_config import configure_logging
from .models import (
    HealthResponse,
    MediaInfo,
    PlaylistResponse,
    PlaylistTrack,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Source,
)
from .service import service_version
from .sources import spotify

# The service name is also the project name in pyproject.toml, which the version is read from.
SERVICE = "discord-api-media"
VERSION = service_version(SERVICE)

# Logging is set up on import, before uvicorn prints its startup lines, so every line is JSON.
configure_logging(get_settings().log_level)

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)

# A 502 answer carries this fixed detail. The exception itself goes only to the server log.
_UPSTREAM_ERROR = "The media source could not be reached."


def _is_youtube_playlist(url: str) -> bool:
    """Tells whether a URL is a YouTube URL that names a playlist.

    Args:
        url: The URL to check.

    Returns:
        True when the host is a YouTube host and the query has a non-empty list parameter.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.hostname in _YOUTUBE_HOSTS and bool(parse_qs(parts.query).get("list"))


def _upstream_failure(route: str) -> HTTPException:
    """Logs the exception being handled and builds the 502 answer for it.

    Call it only inside an except block, so the log record carries the traceback.

    Args:
        route: The route that failed, for the log message.

    Returns:
        A 502 HTTPException whose detail does not reveal the exception.
    """
    logger.exception("The media source failed while serving %s.", route)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail=_UPSTREAM_ERROR
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configures the caches before the first request."""
    settings = get_settings()
    cache.configure(settings.metadata_cache_ttl, settings.stream_url_cache_ttl)
    yield


app = FastAPI(title=SERVICE, version=VERSION, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Reports that the service is up. It needs no token, so monitors and Docker can call it."""
    return HealthResponse(status="ok", service=SERVICE, version=VERSION)


@app.get("/media/info", response_model=MediaInfo, dependencies=[Depends(require_auth)])
async def media_info(
    settings: Annotated[Settings, Depends(get_settings)],
    url: Annotated[str | None, Query()] = None,
    query: Annotated[str | None, Query()] = None,
    source: Annotated[Source, Query()] = "youtube",
) -> MediaInfo:
    """Resolves a URL or a search query to track metadata and a playable stream URL."""
    if url and query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either url or query, not both.",
        )
    if not url and not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Provide url or query."
        )

    try:
        if url:
            if spotify.is_spotify_url(url):
                info = await spotify.get_info(url, settings)
            else:
                info = await fetch_info(url, settings)
        else:
            # The checks above leave a non-empty query whenever url is empty.
            assert query is not None
            results = await search(query, source, 1, settings)
            if not results:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="No results found."
                )
            info = await fetch_info(results[0]["webpage_url"], settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise _upstream_failure("/media/info") from exc

    return MediaInfo(**info)


@app.post(
    "/media/search", response_model=SearchResponse, dependencies=[Depends(require_auth)]
)
async def media_search(
    body: SearchRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchResponse:
    """Searches a source for tracks. The results carry no stream URLs."""
    max_results = min(body.max_results, settings.max_search_results)
    try:
        entries = await search(body.query, body.source, max_results, settings)
    except Exception as exc:
        raise _upstream_failure("/media/search") from exc

    return SearchResponse(results=[SearchResult(**e) for e in entries])


@app.get(
    "/media/playlist",
    response_model=PlaylistResponse,
    dependencies=[Depends(require_auth)],
)
async def media_playlist(
    settings: Annotated[Settings, Depends(get_settings)],
    url: Annotated[str, Query()],
) -> PlaylistResponse:
    """Expands a playlist or album URL into an ordered list of tracks.

    It accepts YouTube playlist URLs and Spotify album and playlist URLs.
    The tracks carry no stream URLs, because a stream URL in a long queue expires before its turn.
    Callers fetch ``/media/info`` for each track at play time instead.
    """
    try:
        if spotify.is_spotify_url(url):
            if not spotify.is_spotify_collection(url):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Use /media/info for Spotify track URLs.",
                )
            tracks = await spotify.get_collection(url, settings)
        elif _is_youtube_playlist(url):
            entries = await fetch_playlist(url, settings)
            tracks = [PlaylistTrack(**e) for e in entries]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL does not point to a supported playlist or album.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise _upstream_failure("/media/playlist") from exc

    return PlaylistResponse(tracks=tracks)
