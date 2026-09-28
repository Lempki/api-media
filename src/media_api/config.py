"""This service's settings, read from the environment or from .env."""

from functools import lru_cache

from pydantic import Field

from .service import ServiceSettings

__all__ = ["Settings", "get_settings"]


class Settings(ServiceSettings):
    """The shared settings plus this service's own.

    Each field reads the environment variable of the same name in upper case.

    Attributes:
        metadata_cache_ttl: How long track metadata stays cached, in seconds.
        stream_url_cache_ttl: The longest time a stream URL stays cached, in seconds.
            A URL that carries an earlier expiry leaves the cache at that expiry instead.
        ydl_format: The yt-dlp format selector used when extracting stream URLs.
        max_search_results: The upper limit on results that /media/search returns.
        spotify_client_id: The Spotify application client ID, if Spotify support is set up.
        spotify_client_secret: The Spotify application client secret, if Spotify support is set up.
    """

    metadata_cache_ttl: int = Field(default=3600, gt=0)
    stream_url_cache_ttl: int = Field(default=300, gt=0)
    ydl_format: str = "bestaudio/best"
    max_search_results: int = Field(default=10, ge=1)
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Returns the settings, read once and then cached for the process."""
    return Settings()
