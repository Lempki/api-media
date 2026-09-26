import logging
import logging.config
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status

from . import cache
from .auth import require_auth
from .config import Settings, get_settings
from .extractor import fetch_info, fetch_playlist, search
from .models import (
    HealthResponse,
    MediaInfo,
    PlaylistResponse,
    PlaylistTrack,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from .sources import spotify


def _is_youtube_playlist(url: str) -> bool:
    return ("youtube.com" in url or "youtu.be" in url) and "list=" in url


def _configure_logging(level: str) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "formatters": {
                "json": {
                    "format": (
                        '{"time":"%(asctime)s","level":"%(levelname)s",'
                        '"name":"%(name)s","message":"%(message)s"}'
                    )
                }
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "json"}
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    cache.configure(settings.metadata_cache_ttl, settings.stream_url_cache_ttl)
    yield


app = FastAPI(title="discord-api-media", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="discord-api-media", version="1.0.0")


@app.get("/media/info", response_model=MediaInfo, dependencies=[Depends(require_auth)])
async def media_info(
    settings: Annotated[Settings, Depends(get_settings)],
    url: str | None = Query(default=None),
    query: str | None = Query(default=None),
    source: str = Query(default="youtube"),
) -> MediaInfo:
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
            results = await search(query, source, 1, settings)  # type: ignore[arg-type]
            if not results:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="No results found."
                )
            info = await fetch_info(results[0]["webpage_url"], settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return MediaInfo(**info)


@app.post(
    "/media/search", response_model=SearchResponse, dependencies=[Depends(require_auth)]
)
async def media_search(
    body: SearchRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchResponse:
    max_results = min(body.max_results, settings.max_search_results)
    try:
        entries = await search(body.query, body.source, max_results, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return SearchResponse(results=[SearchResult(**e) for e in entries])


@app.get(
    "/media/playlist",
    response_model=PlaylistResponse,
    dependencies=[Depends(require_auth)],
)
async def media_playlist(
    settings: Annotated[Settings, Depends(get_settings)],
    url: str = Query(...),
) -> PlaylistResponse:
    """Expand a playlist or album URL into an ordered list of tracks.

    Accepts YouTube playlist URLs and Spotify album/playlist URLs.
    Stream URLs are intentionally omitted; call ``/media/info`` per track at
    play time so that expired URLs are never served from a stale queue.
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return PlaylistResponse(tracks=tracks)
