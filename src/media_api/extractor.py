"""The yt-dlp wrapper that extracts track metadata, stream URLs, and search results."""

import asyncio
import math
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlsplit

import yt_dlp

from . import cache
from .config import Settings


class TrackSummary(TypedDict):
    """A search result or playlist entry, without a stream URL."""

    title: str
    webpage_url: str
    duration_seconds: int | None
    duration_formatted: str | None
    thumbnail_url: str | None


def _make_ydl_opts(settings: Settings, flat: bool = False) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": settings.ydl_format,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": flat,
    }
    if settings.ydl_cookies_file is not None:
        opts["cookiefile"] = str(settings.ydl_cookies_file)
    return opts


@contextmanager
def _private_cookie_file(ydl_opts: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Gives one extraction its own copy of the configured cookies file.

    yt-dlp rewrites its cookies file whenever a YoutubeDL instance closes.
    Concurrent extractions sharing one file would read each other's half-written copies.
    A cookies file mounted read-only would also make every extraction fail on close.
    The configured file is therefore only ever read.

    Args:
        ydl_opts: The yt-dlp options, which name the cookies file under "cookiefile".

    Yields:
        The options unchanged when no cookies file is set.
        Otherwise a copy of them that points to a temporary copy of the file.
    """
    cookie_file = ydl_opts.get("cookiefile")
    if cookie_file is None:
        yield ydl_opts
        return
    with tempfile.TemporaryDirectory(prefix="api-media-cookies-") as directory:
        private_copy = Path(directory) / "cookies.txt"
        shutil.copyfile(cookie_file, private_copy)
        yield {**ydl_opts, "cookiefile": str(private_copy)}


def duration_seconds(value: Any) -> int | None:
    """Turns a duration that yt-dlp reports into whole seconds.

    yt-dlp may report a duration as an int, as a float, or not at all.

    Args:
        value: The raw duration field.

    Returns:
        The duration rounded to whole seconds, or None when it is missing or not a finite number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        return None
    return round(value)


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def stream_url_expiry(stream_url: str, ttl: int, now: datetime) -> datetime:
    """Works out when a stream URL stops working.

    Googlevideo URLs carry their expiry as a Unix timestamp in the `expire` query parameter.
    The result never lies further ahead than the stream URL cache TTL.

    Args:
        stream_url: The direct stream URL.
        ttl: The stream URL cache TTL in seconds.
        now: The current time in UTC.

    Returns:
        The earlier of the URL's own expiry and now plus the TTL.
    """
    ceiling = now + timedelta(seconds=ttl)
    values = parse_qs(urlsplit(stream_url).query).get("expire")
    if not values:
        return ceiling
    try:
        expires_at = datetime.fromtimestamp(int(values[0]), UTC)
    except (ValueError, OverflowError, OSError):
        return ceiling
    return min(expires_at, ceiling)


def _parse_metadata(info: dict[str, Any]) -> dict[str, Any]:
    duration = duration_seconds(info.get("duration"))
    return {
        "source": info.get("extractor_key", "unknown").lower(),
        "title": info.get("title", ""),
        "duration_seconds": duration,
        "duration_formatted": _format_duration(duration),
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail_url": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url", ""),
        "is_live": bool(info.get("is_live")),
    }


def _with_stream(
    metadata: dict[str, Any], stream: cache.StreamEntry | None
) -> dict[str, Any]:
    return {
        **metadata,
        "stream_url": stream.url if stream else None,
        "stream_url_expires_at": stream.expires_at if stream else None,
    }


def _extract_blocking(url: str, ydl_opts: dict[str, Any]) -> dict[str, Any]:
    with _private_cookie_file(ydl_opts) as opts, yt_dlp.YoutubeDL(opts) as ydl:
        info: dict[str, Any] = ydl.extract_info(url, download=False)
    return info


def _search_blocking(
    query: str, source: str, max_results: int, ydl_opts: dict[str, Any]
) -> list[dict[str, Any]]:
    search_url = (
        f"ytsearch{max_results}:{query}"
        if source == "youtube"
        else f"scsearch{max_results}:{query}"
    )
    flat_opts = {**ydl_opts, "extract_flat": True}
    with _private_cookie_file(flat_opts) as opts, yt_dlp.YoutubeDL(opts) as ydl:
        result: dict[str, Any] | None = ydl.extract_info(search_url, download=False)
    entries: list[dict[str, Any]] = result.get("entries", []) if result else []
    return entries


async def fetch_info(url: str, settings: Settings) -> dict[str, Any]:
    """Returns a track's metadata and a stream URL that is still valid.

    Metadata and stream URLs are cached separately.
    A cached stream URL is used only while it is outside the safety margin of its expiry.
    Otherwise the track is extracted again, so a cache hit never serves a stale stream URL.

    Args:
        url: The track URL.
        settings: The settings that hold the yt-dlp format and the stream URL cache TTL.

    Returns:
        A new dict with the MediaInfo fields, which the caller may change freely.
    """
    metadata = cache.get_metadata(url)
    stream = cache.get_stream_url(url)
    if metadata is not None and stream is not None:
        return _with_stream(metadata, stream)

    opts = _make_ydl_opts(settings)
    raw = await asyncio.to_thread(_extract_blocking, url, opts)
    metadata = _parse_metadata(raw)
    cache.set_metadata(url, metadata)

    stream = None
    stream_url = raw.get("url")
    if stream_url:
        expires_at = stream_url_expiry(
            stream_url, settings.stream_url_cache_ttl, datetime.now(UTC)
        )
        stream = cache.StreamEntry(url=stream_url, expires_at=expires_at)
        cache.set_stream_url(url, stream)

    return _with_stream(metadata, stream)


def _entry_to_playlist_track(entry: dict[str, Any]) -> TrackSummary:
    webpage_url = entry.get("webpage_url") or entry.get("url") or ""
    if not webpage_url and entry.get("id"):
        webpage_url = f"https://www.youtube.com/watch?v={entry['id']}"
    duration = duration_seconds(entry.get("duration"))
    return {
        "title": entry.get("title", ""),
        "webpage_url": webpage_url,
        "duration_seconds": duration,
        "duration_formatted": _format_duration(duration),
        "thumbnail_url": entry.get("thumbnail"),
    }


async def fetch_playlist(url: str, settings: Settings) -> list[TrackSummary]:
    """Expands a playlist URL into its tracks without stream URLs.

    Args:
        url: The playlist URL.
        settings: The settings that hold the yt-dlp format.

    Returns:
        The tracks in playlist order, leaving out entries without a page URL.
    """
    opts = {**_make_ydl_opts(settings, flat=True), "noplaylist": False}
    raw = await asyncio.to_thread(_extract_blocking, url, opts)
    entries = raw.get("entries")
    if not entries:
        track = _entry_to_playlist_track(raw)
        return [track] if track["webpage_url"] else []
    return [t for e in entries if (t := _entry_to_playlist_track(e))["webpage_url"]]


async def search(
    query: str, source: str, max_results: int, settings: Settings
) -> list[TrackSummary]:
    """Searches a source for tracks without extracting stream URLs.

    Args:
        query: The search text.
        source: The site to search.
        max_results: The largest number of results to return.
        settings: The settings that hold the yt-dlp format.

    Returns:
        The results in the order the source ranks them.
    """
    opts = _make_ydl_opts(settings, flat=True)
    entries = await asyncio.to_thread(
        _search_blocking, query, source, max_results, opts
    )
    results: list[TrackSummary] = []
    for e in entries:
        duration = duration_seconds(e.get("duration"))
        results.append(
            {
                "title": e.get("title", ""),
                "duration_seconds": duration,
                "duration_formatted": _format_duration(duration),
                "thumbnail_url": e.get("thumbnail"),
                "webpage_url": e.get("url") or e.get("webpage_url", ""),
            }
        )
    return results
