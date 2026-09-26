import os

import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-0123456789"
os.environ["DISCORD_API_SECRET"] = SECRET

from media_api.main import VERSION, app  # noqa: E402
from media_api.service import service_version  # noqa: E402

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}

YOUTUBE_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
YOUTUBE_PLAYLIST = "https://www.youtube.com/playlist?list=PL123"


def test_health_needs_no_token() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "service": "discord-api-media",
        "version": VERSION,
    }


def test_version_comes_from_package_metadata() -> None:
    assert VERSION == service_version("discord-api-media") != "0.0.0"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/media/info?url={YOUTUBE_VIDEO}", None),
        ("POST", "/media/search", {"query": "test"}),
        ("GET", f"/media/playlist?url={YOUTUBE_PLAYLIST}", None),
    ],
    ids=["info", "search", "playlist"],
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


def test_playlist_rejects_plain_youtube_url() -> None:
    r = client.get(f"/media/playlist?url={YOUTUBE_VIDEO}", headers=AUTH)
    assert r.status_code == 400


def test_search_missing_body() -> None:
    r = client.post("/media/search", headers=AUTH)
    assert r.status_code == 422
