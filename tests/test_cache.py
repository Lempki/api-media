import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from media_api import cache
from media_api.config import Settings
from media_api.extractor import fetch_info, stream_url_expiry
from media_api.main import app

from .conftest import SECRET, STREAM_TTL, FakeExtraction

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}
YOUTUBE_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _info() -> dict[str, object]:
    r = client.get("/media/info", params={"url": YOUTUBE_VIDEO}, headers=AUTH)
    assert r.status_code == 200, r.text
    body: dict[str, object] = r.json()
    return body


def test_cache_hit_serves_the_stream_url_until_it_expires(
    extraction: FakeExtraction, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _info()
    second = _info()

    assert first["stream_url"]
    assert first["stream_url_expires_at"]
    assert second == first
    assert len(extraction.calls) == 1

    later = datetime.now(UTC) + timedelta(seconds=STREAM_TTL + 1)
    monkeypatch.setattr(cache, "_now", lambda: later)
    third = _info()

    assert len(extraction.calls) == 2
    assert third["stream_url"]
    assert third["stream_url"] != first["stream_url"]


def test_stream_url_inside_the_safety_margin_is_extracted_again(
    extraction: FakeExtraction,
) -> None:
    extraction.expire_in = int(cache.STREAM_EXPIRY_MARGIN.total_seconds()) - 5
    _info()
    _info()
    assert len(extraction.calls) == 2


def test_response_carries_the_real_expiry(extraction: FakeExtraction) -> None:
    extraction.expire_in = 120
    before = int(time.time())
    expires_at = datetime.fromisoformat(str(_info()["stream_url_expires_at"]))
    assert before + 120 <= expires_at.timestamp() <= int(time.time()) + 120


def test_stream_url_without_expire_uses_the_ttl(extraction: FakeExtraction) -> None:
    extraction.expire_in = None
    before = datetime.now(UTC)
    expires_at = datetime.fromisoformat(str(_info()["stream_url_expires_at"]))
    assert expires_at - before >= timedelta(seconds=STREAM_TTL - 1)
    assert expires_at - datetime.now(UTC) <= timedelta(seconds=STREAM_TTL)


async def test_fetch_info_returns_a_fresh_dict(
    extraction: FakeExtraction, settings: Settings
) -> None:
    first = await fetch_info(YOUTUBE_VIDEO, settings)
    first["title"] = "Changed"
    first.pop("stream_url")

    second = await fetch_info(YOUTUBE_VIDEO, settings)

    assert second["title"] == "Never Gonna Give You Up"
    assert second["stream_url"]
    assert second is not first
    assert len(extraction.calls) == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"https://rr1.googlevideo.com/videoplayback?expire={int(NOW.timestamp()) + 60}",
            NOW + timedelta(seconds=60),
        ),
        (
            f"https://rr1.googlevideo.com/videoplayback?id=1&expire={int(NOW.timestamp()) + 21600}",
            NOW + timedelta(seconds=300),
        ),
        (
            "https://rr1.googlevideo.com/videoplayback?id=1",
            NOW + timedelta(seconds=300),
        ),
        (
            "https://rr1.googlevideo.com/videoplayback?expire=soon",
            NOW + timedelta(seconds=300),
        ),
        (
            "https://rr1.googlevideo.com/videoplayback?expire=",
            NOW + timedelta(seconds=300),
        ),
        (
            "https://rr1.googlevideo.com/videoplayback?expire=99999999999999999999",
            NOW + timedelta(seconds=300),
        ),
    ],
    ids=[
        "expire-before-ttl",
        "expire-after-ttl",
        "no-expire",
        "not-a-number",
        "empty",
        "huge",
    ],
)
def test_stream_url_expiry(url: str, expected: datetime) -> None:
    assert stream_url_expiry(url, 300, NOW) == expected
