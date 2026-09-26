import asyncio
from datetime import UTC, datetime, timedelta

import yt_dlp

from . import cache
from .config import Settings


def _make_ydl_opts(settings: Settings, flat: bool = False) -> dict:
    return {
        "format": settings.ydl_format,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": flat,
    }


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_info(info: dict) -> dict:
    stream_url = info.get("url")
    return {
        "source": info.get("extractor_key", "unknown").lower(),
        "title": info.get("title", ""),
        "duration_seconds": info.get("duration"),
        "duration_formatted": _format_duration(info.get("duration")),
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail_url": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url", ""),
        "stream_url": stream_url,
        "stream_url_expires_at": (
            (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
            if stream_url
            else None
        ),
        "is_live": bool(info.get("is_live")),
    }


def _extract_blocking(url: str, ydl_opts: dict) -> dict:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)  # type: ignore[return-value]


def _search_blocking(
    query: str, source: str, max_results: int, ydl_opts: dict
) -> list[dict]:
    search_url = (
        f"ytsearch{max_results}:{query}"
        if source == "youtube"
        else f"scsearch{max_results}:{query}"
    )
    flat_opts = {**ydl_opts, "extract_flat": True}
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        result = ydl.extract_info(search_url, download=False)
    return result.get("entries", []) if result else []  # type: ignore[union-attr]


async def fetch_info(url: str, settings: Settings) -> dict:
    cached = cache.get_metadata(url)
    if cached:
        stream = cache.get_stream_url(url)
        if stream:
            cached["stream_url"] = stream
        return cached

    opts = _make_ydl_opts(settings)
    raw = await asyncio.to_thread(_extract_blocking, url, opts)
    parsed = _parse_info(raw)

    metadata = {
        k: v
        for k, v in parsed.items()
        if k not in ("stream_url", "stream_url_expires_at")
    }
    cache.set_metadata(url, metadata)
    if parsed.get("stream_url"):
        cache.set_stream_url(url, parsed["stream_url"])

    return parsed


def _entry_to_playlist_track(entry: dict) -> dict:
    webpage_url = entry.get("webpage_url") or entry.get("url") or ""
    if not webpage_url and entry.get("id"):
        webpage_url = f"https://www.youtube.com/watch?v={entry['id']}"
    return {
        "title": entry.get("title", ""),
        "webpage_url": webpage_url,
        "duration_seconds": entry.get("duration"),
        "duration_formatted": _format_duration(entry.get("duration")),
        "thumbnail_url": entry.get("thumbnail"),
    }


async def fetch_playlist(url: str, settings: Settings) -> list[dict]:
    opts = {**_make_ydl_opts(settings, flat=True), "noplaylist": False}
    raw = await asyncio.to_thread(_extract_blocking, url, opts)
    entries = raw.get("entries")
    if not entries:
        track = _entry_to_playlist_track(raw)
        return [track] if track["webpage_url"] else []
    return [t for e in entries if (t := _entry_to_playlist_track(e))["webpage_url"]]


async def search(
    query: str, source: str, max_results: int, settings: Settings
) -> list[dict]:
    opts = _make_ydl_opts(settings, flat=True)
    entries = await asyncio.to_thread(
        _search_blocking, query, source, max_results, opts
    )
    return [
        {
            "title": e.get("title", ""),
            "duration_seconds": e.get("duration"),
            "duration_formatted": _format_duration(e.get("duration")),
            "thumbnail_url": e.get("thumbnail"),
            "webpage_url": e.get("url") or e.get("webpage_url", ""),
        }
        for e in entries
    ]
