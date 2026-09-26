import asyncio
import re
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from ..config import Settings
from ..extractor import fetch_info, search
from ..models import PlaylistTrack

_TRACK_RE = re.compile(r"spotify\.com/track/([^/?#]+)")
_ALBUM_RE = re.compile(r"spotify\.com/album/([^/?#]+)")
_PLAYLIST_RE = re.compile(r"spotify\.com/playlist/([^/?#]+)")


def is_spotify_url(url: str) -> bool:
    return "spotify.com" in url


def is_spotify_collection(url: str) -> bool:
    return bool(_ALBUM_RE.search(url) or _PLAYLIST_RE.search(url))


def _get_client(settings: Settings) -> spotipy.Spotify:
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        raise ValueError(
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set to use Spotify features."
        )
    return spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
    )


async def get_info(url: str, settings: Settings) -> dict[str, Any]:
    """Resolve a Spotify track URL to a MediaInfo dict via YouTube search."""
    if settings.spotify_client_id and settings.spotify_client_secret:
        m = _TRACK_RE.search(url)
        if not m:
            raise ValueError(f"Could not parse Spotify track URL: {url}")
        sp = _get_client(settings)
        track = await asyncio.to_thread(sp.track, m.group(1))
        query = f"{track['name']} {track['artists'][0]['name']}"
    else:
        # No Spotify credentials — fall back to searching by track ID (poor quality)
        track_id = url.rstrip("/").split("/")[-1].split("?")[0]
        query = f"spotify track {track_id}"

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
    """Resolve a Spotify album or playlist URL to a list of PlaylistTrack items."""
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
