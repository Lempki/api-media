import io

import pytest
from fastapi.testclient import TestClient

from media_api.config import Settings
from media_api.main import _is_youtube_playlist, app
from media_api.sources import spotify

from .conftest import SECRET, FakeExtraction

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh", True),
        ("https://OPEN.SPOTIFY.COM/album/6eUW0wxWtzkFdaEFsTJto6?si=x", True),
        ("https://example.com/?spotify.com", False),
        ("https://example.com/spotify.com/track/abc", False),
        ("https://open.spotify.com.example.com/track/abc", False),
        ("https://notspotify.com/track/abc", False),
        ("open.spotify.com/track/abc", False),
        ("http://[::1", False),
    ],
)
def test_is_spotify_url_checks_the_host(url: str, expected: bool) -> None:
    assert spotify.is_spotify_url(url) is expected


@pytest.mark.parametrize(
    ("url", "is_track", "is_collection"),
    [
        ("https://open.spotify.com/track/abc", True, False),
        ("https://open.spotify.com/intl-fi/track/abc?si=x", True, False),
        ("https://open.spotify.com/intl-pt-br/album/abc", False, True),
        ("https://open.spotify.com/intl-fi/playlist/abc", False, True),
    ],
)
def test_spotify_links_from_localized_clients_are_recognized(
    url: str, is_track: bool, is_collection: bool
) -> None:
    assert bool(spotify._TRACK_RE.search(url)) is is_track
    assert spotify.is_spotify_collection(url) is is_collection


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/playlist?list=PL123", True),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&list=PL123", True),
        ("https://m.youtube.com/playlist?list=PL123", True),
        ("https://music.youtube.com/playlist?list=PL123", True),
        ("https://youtu.be/dQw4w9WgXcQ?list=PL123", True),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=", False),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ#list=PL123", False),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&playlist=PL123", False),
        ("https://example.com/?next=youtube.com&list=PL123", False),
        ("https://youtube.com.example.com/playlist?list=PL123", False),
        ("http://[::1", False),
    ],
)
def test_is_youtube_playlist_checks_the_host_and_query(
    url: str, expected: bool
) -> None:
    assert _is_youtube_playlist(url) is expected


def test_playlist_route_rejects_a_lookalike_host(extraction: FakeExtraction) -> None:
    url = "https://example.com/watch?list=PL123&next=youtube.com"
    r = client.get("/media/playlist", params={"url": url}, headers=AUTH)
    assert r.status_code == 400
    assert extraction.calls == []


def test_spotify_client_is_reused_per_credential_pair(settings: Settings) -> None:
    first = settings.model_copy(
        update={"spotify_client_id": "id", "spotify_client_secret": "secret"}
    )
    other = first.model_copy(update={"spotify_client_secret": "other-secret"})

    assert spotify._get_client(first) is spotify._get_client(first)
    assert spotify._get_client(first) is not spotify._get_client(other)


def test_spotify_client_needs_credentials(settings: Settings) -> None:
    with pytest.raises(ValueError, match="SPOTIFY_CLIENT_ID"):
        spotify._get_client(settings)


async def test_spotify_track_without_credentials_searches_its_title(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    queries: list[str] = []

    async def fake_search(
        query: str, source: str, max_results: int, settings: Settings
    ) -> list[dict[str, str]]:
        queries.append(query)
        return [{"webpage_url": "https://www.youtube.com/watch?v=x"}]

    async def fake_fetch_info(url: str, settings: Settings) -> dict[str, str]:
        return {"webpage_url": url}

    monkeypatch.setattr(spotify, "oembed_title", lambda url: "Never Gonna Give You Up")
    monkeypatch.setattr(spotify, "search", fake_search)
    monkeypatch.setattr(spotify, "fetch_info", fake_fetch_info)

    await spotify.get_info(
        "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", settings
    )

    # The old fallback searched for "spotify track <ID>", which rarely found the song.
    assert queries == ["Never Gonna Give You Up"]


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"title": " Never Gonna Give You Up "}', "Never Gonna Give You Up"),
        (b"{}", None),
    ],
)
def test_oembed_title_reads_the_title(
    monkeypatch: pytest.MonkeyPatch, body: bytes, expected: str | None
) -> None:
    requested: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> _FakeResponse:
        requested.append(url)
        return _FakeResponse(body)

    monkeypatch.setattr(spotify.urllib.request, "urlopen", fake_urlopen)
    track = "https://open.spotify.com/track/abc"

    if expected is None:
        with pytest.raises(ValueError, match="no title"):
            spotify.oembed_title(track)
    else:
        assert spotify.oembed_title(track) == expected
    assert requested == [
        "https://open.spotify.com/oembed?url=https%3A%2F%2Fopen.spotify.com%2Ftrack%2Fabc"
    ]
