from typing import Any

from cachetools import TTLCache

_metadata_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=256, ttl=3600)
_stream_cache: TTLCache[str, str] = TTLCache(maxsize=256, ttl=300)


def configure(metadata_ttl: int, stream_ttl: int) -> None:
    global _metadata_cache, _stream_cache
    _metadata_cache = TTLCache(maxsize=256, ttl=metadata_ttl)
    _stream_cache = TTLCache(maxsize=256, ttl=stream_ttl)


def get_metadata(key: str) -> dict[str, Any] | None:
    return _metadata_cache.get(key)


def set_metadata(key: str, value: dict[str, Any]) -> None:
    _metadata_cache[key] = value


def get_stream_url(key: str) -> str | None:
    return _stream_cache.get(key)


def set_stream_url(key: str, url: str) -> None:
    _stream_cache[key] = url
