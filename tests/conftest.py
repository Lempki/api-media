"""Fixtures that replace yt-dlp and the settings, so no test reaches the network."""

import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

SECRET = "test-secret-0123456789"
os.environ["DISCORD_API_SECRET"] = SECRET

from media_api import cache, extractor  # noqa: E402
from media_api.config import Settings, get_settings  # noqa: E402
from media_api.main import app  # noqa: E402

STREAM_TTL = 300


class FakeExtraction:
    """Stands in for yt-dlp's extraction and counts how often it runs.

    Attributes:
        calls: The URLs extracted so far, in order.
        expire_in: Seconds from now written into the stream URL's expire parameter.
            None leaves the parameter out.
        duration: The duration reported for every track.
        error: An exception to raise instead of returning a track.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.expire_in: int | None = 6 * 3600
        self.duration: float | int | None = 212
        self.error: Exception | None = None

    def __call__(self, url: str, ydl_opts: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        stream_url = f"https://rr1.googlevideo.com/videoplayback?id={len(self.calls)}"
        if self.expire_in is not None:
            stream_url += f"&expire={int(time.time()) + self.expire_in}"
        return {
            "extractor_key": "Youtube",
            "title": "Never Gonna Give You Up",
            "duration": self.duration,
            "uploader": "Rick Astley",
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
            "webpage_url": url,
            "url": stream_url,
            "is_live": False,
        }


@pytest.fixture(autouse=True)
def settings() -> Iterator[Settings]:
    """Empties the caches and serves fixed settings instead of the ones from .env."""
    fixed = Settings(
        discord_api_secret=SECRET,
        metadata_cache_ttl=3600,
        stream_url_cache_ttl=STREAM_TTL,
        _env_file=None,
    )
    cache.configure(fixed.metadata_cache_ttl, fixed.stream_url_cache_ttl)
    app.dependency_overrides[get_settings] = lambda: fixed
    yield fixed
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def extraction(monkeypatch: pytest.MonkeyPatch) -> FakeExtraction:
    """Replaces yt-dlp's single-URL extraction with a counting fake."""
    fake = FakeExtraction()
    monkeypatch.setattr(extractor, "_extract_blocking", fake)
    return fake
