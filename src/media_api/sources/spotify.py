"""Resolves Spotify tracks, albums, and playlists to YouTube tracks.

Spotify serves no audio to third parties.
Each Spotify track is therefore matched to a YouTube video by its name and first artist.
Without API credentials, a track is matched by the title from Spotify's public oEmbed endpoint.
"""

import asyncio
import json
import re
import urllib.request
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode, urlsplit

import spotipy
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyClientCredentials

from ..config import Settings
from ..extractor import fetch_info, search
from ..models import PlaylistTrack

# Links shared from a localized client carry a market segment such as /intl-fi/ before the type.
_MARKET = r"(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?"
_TRACK_RE = re.compile(rf"spotify\.com/{_MARKET}track/([^/?#]+)")
_ALBUM_RE = re.compile(rf"spotify\.com/{_MARKET}album/([^/?#]+)")
_PLAYLIST_RE = re.compile(rf"spotify\.com/{_MARKET}playlist/([^/?#]+)")
_SPOTIFY_HOST = "open.spotify.com"
_OEMBED_URL = "https://open.spotify.com/oembed"
_OEMBED_TIMEOUT = 10.0


def is_spotify_url(url: str) -> bool:
    """Tells whether a URL points to the Spotify web player.

    Args:
        url: The URL to check.

    Returns:
        True when the URL's host is open.spotify.com.
    """
    try:
        return urlsplit(url).hostname == _SPOTIFY_HOST
    except ValueError:
        return False


def is_spotify_collection(url: str) -> bool:
    """Tells whether a Spotify URL names an album or a playlist.

    Args:
        url: The URL to check.

    Returns:
        True when the path holds an album or playlist segment.
    """
    return bool(_ALBUM_RE.search(url) or _PLAYLIST_RE.search(url))


def oembed_title(url: str) -> str:
    """Reads a Spotify item's title from the public oEmbed endpoint.

    The endpoint needs no credentials. For a track, the title is the track name without the artist.

    Args:
        url: The Spotify URL.

    Returns:
        The title.

    Raises:
        ValueError: When the endpoint answers without a title.
        OSError: When the endpoint cannot be reached.
    """
    request_url = f"{_OEMBED_URL}?{urlencode({'url': url})}"
    with urllib.request.urlopen(request_url, timeout=_OEMBED_TIMEOUT) as response:
        data = json.load(response)
    title = data.get("title") if isinstance(data, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"Spotify's oEmbed endpoint returned no title for {url}")
    return title.strip()


def _get_client(settings: Settings) -> spotipy.Spotify:
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        raise ValueError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set to use Spotify features."
        )
    return _client_for(settings.spotify_client_id, settings.spotify_client_secret)


@lru_cache(maxsize=4)
def _client_for(client_id: str, client_secret: str) -> spotipy.Spotify:
    """Builds one Spotify client per credential pair and reuses it.

    The access token is kept in memory, so the client asks for a new one only when it expires.
    """
    return spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
            cache_handler=MemoryCacheHandler(),
        )
    )


async def get_info(url: str, settings: Settings) -> dict[str, Any]:
    """Resolves a Spotify track URL to MediaInfo fields through a YouTube search.

    With Spotify credentials, the search uses the track name and first artist.
    Without them, it uses the track title from Spotify's oEmbed endpoint.

    Args:
        url: The Spotify track URL.
        settings: The settings that hold the Spotify credentials and the yt-dlp format.

    Returns:
        A new dict with the MediaInfo fields of the first YouTube match.

    Raises:
        ValueError: When the URL holds no track ID or the search finds nothing.
    """
    m = _TRACK_RE.search(url)
    if not m:
        raise ValueError(f"Could not parse Spotify track URL: {url}")
    if settings.spotify_client_id and settings.spotify_client_secret:
        sp = _get_client(settings)
        track = await asyncio.to_thread(sp.track, m.group(1))
        query = f"{track['name']} {track['artists'][0]['name']}"
    else:
        query = await asyncio.to_thread(oembed_title, url)

    results = await search(query, "youtube", 1, settings)
    if not results:
        raise ValueError(f"Could not resolve Spotify URL: {url}")
    return await fetch_info(results[0]["webpage_url"], settings)


async def _resolve_one(
    name: str,
    artist: str,
    duration_ms: int | None,
    settings: Settings,
    sem: asyncio.Semaphore,
) -> PlaylistTrack | None:
    async with sem:
        results = await search(f"{name} {artist}", "youtube", 1, settings)
        if not results:
            return None
        r = results[0]
        return PlaylistTrack(
            title=r.get("title", name),
            webpage_url=r["webpage_url"],
            duration_seconds=r.get("duration_seconds"),
            duration_formatted=r.get("duration_formatted"),
            thumbnail_url=r.get("thumbnail_url"),
        )


async def get_collection(url: str, settings: Settings) -> list[PlaylistTrack]:
    """Resolves a Spotify album or playlist URL to YouTube tracks.

    Every page of the collection is read, and up to five YouTube searches run at once.

    Args:
        url: The Spotify album or playlist URL.
        settings: The settings that hold the Spotify credentials and the yt-dlp format.

    Returns:
        The matched tracks in collection order, leaving out tracks without a match.

    Raises:
        ValueError: When the Spotify credentials are missing or the URL is not an album or playlist.
    """
    sp = _get_client(settings)
    raw_tracks: list[tuple[str, str, int | None]] = []

    album_m = _ALBUM_RE.search(url)
    playlist_m = _PLAYLIST_RE.search(url)

    if album_m:
        page: dict[str, Any] | None = await asyncio.to_thread(
            sp.album_tracks, album_m.group(1)
        )
        while page:
            for item in page.get("items", []):
                if item and item.get("name") and item.get("artists"):
                    raw_tracks.append(
                        (
                            item["name"],
                            item["artists"][0]["name"],
                            item.get("duration_ms"),
                        )
                    )
            page = await asyncio.to_thread(sp.next, page) if page.get("next") else None
    elif playlist_m:
        page = await asyncio.to_thread(
            sp.playlist_items,
            playlist_m.group(1),
            fields="items(track(name,artists,duration_ms)),next",
        )
        while page:
            for item in page.get("items", []):
                if not item:
                    continue
                track = item.get("track")
                if track and track.get("name") and track.get("artists"):
                    raw_tracks.append(
                        (
                            track["name"],
                            track["artists"][0]["name"],
                            track.get("duration_ms"),
                        )
                    )
            page = await asyncio.to_thread(sp.next, page) if page.get("next") else None
    else:
        raise ValueError(f"URL is not a Spotify album or playlist: {url}")

    sem = asyncio.Semaphore(5)
    tasks = [
        _resolve_one(name, artist, dur, settings, sem)
        for name, artist, dur in raw_tracks
    ]
    resolved = await asyncio.gather(*tasks)
    return [t for t in resolved if t is not None]
