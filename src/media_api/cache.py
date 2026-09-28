"""Time-limited caches for track metadata and stream URLs."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cachetools import TTLCache

__all__ = [
    "STREAM_EXPIRY_MARGIN",
    "StreamEntry",
    "configure",
    "get_metadata",
    "get_stream_url",
    "set_metadata",
    "set_stream_url",
]

# A stream URL counts as expired this long before its real expiry.
# That leaves a bot enough time to start playback with the URL it gets.
STREAM_EXPIRY_MARGIN = timedelta(seconds=30)


@dataclass(frozen=True)
class StreamEntry:
    """A cached stream URL and the moment it stops working.

    Attributes:
        url: The direct stream URL.
        expires_at: When the URL expires, in UTC.
    """

    url: str
    expires_at: datetime


_metadata_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=256, ttl=3600)
_stream_cache: TTLCache[str, StreamEntry] = TTLCache(maxsize=256, ttl=300)


def _now() -> datetime:
    """Returns the current time in UTC. Tests replace it to move time forward."""
    return datetime.now(UTC)


def configure(metadata_ttl: int, stream_ttl: int) -> None:
    """Replaces both caches with empty ones that use the given lifetimes.

    Args:
        metadata_ttl: How long metadata entries live, in seconds.
        stream_ttl: The longest time a stream entry lives, in seconds.
    """
    global _metadata_cache, _stream_cache
    _metadata_cache = TTLCache(maxsize=256, ttl=metadata_ttl)
    _stream_cache = TTLCache(maxsize=256, ttl=stream_ttl)


def get_metadata(key: str) -> dict[str, Any] | None:
    """Returns a copy of the cached metadata for a URL.

    Args:
        key: The track URL.

    Returns:
        A new dict that the caller may change freely, or None on a miss.
    """
    cached = _metadata_cache.get(key)
    return dict(cached) if cached is not None else None


def set_metadata(key: str, value: dict[str, Any]) -> None:
    """Caches a copy of a track's metadata.

    Args:
        key: The track URL.
        value: The parsed metadata, without stream fields.
    """
    _metadata_cache[key] = dict(value)


def get_stream_url(key: str) -> StreamEntry | None:
    """Returns the cached stream entry for a URL while it is still safe to use.

    Args:
        key: The track URL.

    Returns:
        The entry, or None when it is missing or within the safety margin of its expiry.
    """
    entry = _stream_cache.get(key)
    if entry is None or entry.expires_at - STREAM_EXPIRY_MARGIN <= _now():
        return None
    return entry


def set_stream_url(key: str, entry: StreamEntry) -> None:
    """Caches a stream URL together with its expiry.

    Args:
        key: The track URL.
        entry: The stream URL and when it expires.
    """
    _stream_cache[key] = entry
