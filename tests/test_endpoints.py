import os

import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-0123456789"
os.environ["API_SECRET"] = SECRET

from media_api import extractor  # noqa: E402
from media_api.config import Settings  # noqa: E402
from media_api.extractor import duration_seconds  # noqa: E402
from media_api.main import VERSION, app  # noqa: E402
from media_api.service import service_version  # noqa: E402

from .conftest import FakeExtraction  # noqa: E402

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}

YOUTUBE_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
YOUTUBE_PLAYLIST = "https://www.youtube.com/playlist?list=PL123"


def test_health_needs_no_token() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "service": "api-media",
        "version": VERSION,
    }


def test_version_comes_from_package_metadata() -> None:
    assert VERSION == service_version("api-media") != "0.0.0"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/media/info?url={YOUTUBE_VIDEO}", None),
        ("POST", "/media/search", {"query": "test"}),
        ("GET", f"/media/playlist?url={YOUTUBE_PLAYLIST}", None),
        ("GET", f"/media/stream?url={YOUTUBE_VIDEO}", None),
    ],
    ids=["info", "search", "playlist", "stream"],
)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Bearer {SECRET}x"},
        {"Authorization": f"Basic {SECRET}"},
    ],
    ids=["missing", "wrong", "longer", "wrong-scheme"],
)
def test_protected_route_rejects_without_valid_token(
    method: str, path: str, body: dict[str, str] | None, headers: dict[str, str]
) -> None:
    r = client.request(method, path, json=body, headers=headers)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_info_missing_params() -> None:
    r = client.get("/media/info", headers=AUTH)
    assert r.status_code == 400


def test_info_conflicting_params() -> None:
    r = client.get("/media/info?url=https://x.com&query=test", headers=AUTH)
    assert r.status_code == 400


def test_playlist_missing_url() -> None:
    r = client.get("/media/playlist", headers=AUTH)
    assert r.status_code == 422


def test_playlist_rejects_spotify_track() -> None:
    r = client.get(
        "/media/playlist?url=https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh",
        headers=AUTH,
    )
    assert r.status_code == 400


def test_spotify_collection_without_credentials_is_503() -> None:
    # The test settings carry no Spotify credentials.
    r = client.get(
        "/media/playlist?url=https://open.spotify.com/album/6eUW0wxWtzkFdaEFsTJto6",
        headers=AUTH,
    )
    assert r.status_code == 503
    assert r.json()["detail"] == (
        "Spotify albums and playlists are not configured on this server."
    )


def test_playlist_rejects_plain_youtube_url() -> None:
    r = client.get(f"/media/playlist?url={YOUTUBE_VIDEO}", headers=AUTH)
    assert r.status_code == 400


def test_search_missing_body() -> None:
    r = client.post("/media/search", headers=AUTH)
    assert r.status_code == 422


def test_info_without_search_results_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extractor, "_search_blocking", lambda *args: [])
    r = client.get("/media/info", params={"query": "nothing"}, headers=AUTH)
    assert r.status_code == 404
    assert r.json() == {"detail": "No results found."}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/media/info?url={YOUTUBE_VIDEO}", None),
        ("GET", f"/media/playlist?url={YOUTUBE_PLAYLIST}", None),
        ("POST", "/media/search", {"query": "test"}),
    ],
    ids=["info", "playlist", "search"],
)
def test_upstream_failure_is_502_without_the_exception_text(
    method: str,
    path: str,
    body: dict[str, str] | None,
    extraction: FakeExtraction,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    extraction.error = RuntimeError("secret upstream detail")

    def failing_search(*args: object) -> list[dict[str, object]]:
        raise RuntimeError("secret upstream detail")

    monkeypatch.setattr(extractor, "_search_blocking", failing_search)
    r = client.request(method, path, json=body, headers=AUTH)
    assert r.status_code == 502
    assert r.json() == {"detail": "The media source could not be reached."}
    assert "secret upstream detail" not in r.text
    assert "secret upstream detail" in caplog.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/media/info?query=test&source=bandcamp", None),
        ("POST", "/media/search", {"query": "test", "source": "bandcamp"}),
        ("POST", "/media/search", {"query": "test", "max_results": 0}),
        ("POST", "/media/search", {"query": "test", "max_results": 26}),
    ],
    ids=["info-source", "search-source", "search-too-few", "search-too-many"],
)
def test_invalid_input_is_422(
    method: str, path: str, body: dict[str, object] | None
) -> None:
    r = client.request(method, path, json=body, headers=AUTH)
    assert r.status_code == 422


def test_search_accepts_soundcloud_and_caps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, int]] = []

    def fake_search(
        query: str, source: str, max_results: int, opts: object
    ) -> list[dict[str, object]]:
        seen.append((source, max_results))
        return []

    monkeypatch.setattr(extractor, "_search_blocking", fake_search)
    body = {"query": "test", "source": "soundcloud", "max_results": 25}
    r = client.post("/media/search", json=body, headers=AUTH)
    assert r.status_code == 200
    assert seen == [("soundcloud", 10)]


@pytest.mark.parametrize(
    ("duration", "seconds", "formatted"),
    [(212.6, 213, "3:33"), (3600.2, 3600, "1:00:00"), (None, None, None)],
    ids=["float", "float-hour", "missing"],
)
def test_info_rounds_fractional_durations(
    extraction: FakeExtraction,
    duration: float | None,
    seconds: int | None,
    formatted: str | None,
) -> None:
    extraction.duration = duration
    r = client.get("/media/info", params={"url": YOUTUBE_VIDEO}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["duration_seconds"] == seconds
    assert r.json()["duration_formatted"] == formatted


def test_search_and_playlist_round_fractional_durations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {"title": "Song", "url": YOUTUBE_VIDEO, "duration": 59.7}
    monkeypatch.setattr(extractor, "_search_blocking", lambda *args: [entry])
    monkeypatch.setattr(
        extractor, "_extract_blocking", lambda *args: {"entries": [entry]}
    )

    search = client.post("/media/search", json={"query": "song"}, headers=AUTH)
    playlist = client.get(f"/media/playlist?url={YOUTUBE_PLAYLIST}", headers=AUTH)

    assert search.json()["results"][0]["duration_seconds"] == 60
    assert search.json()["results"][0]["duration_formatted"] == "1:00"
    assert playlist.json()["tracks"][0]["duration_seconds"] == 60
    assert playlist.json()["tracks"][0]["duration_formatted"] == "1:00"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(212, 212), (212.4, 212), (212.6, 213), (None, None), ("212", None), (True, None)],
)
def test_duration_seconds(value: object, expected: int | None) -> None:
    assert duration_seconds(value) == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metadata_cache_ttl", 0),
        ("stream_url_cache_ttl", 0),
        ("stream_url_cache_ttl", -5),
        ("max_search_results", 0),
    ],
)
def test_settings_refuse_out_of_range_values(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        Settings.model_validate({"api_secret": SECRET, field: value})
