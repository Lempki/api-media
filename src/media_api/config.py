"""This service's settings, read from the environment or from .env."""

from functools import lru_cache

from .service import ServiceSettings

__all__ = ["Settings", "get_settings"]


class Settings(ServiceSettings):
    """The shared settings plus this service's own.

    Each field reads the environment variable of the same name in upper case.

    Attributes:
        metadata_cache_ttl: How long track metadata stays cached, in seconds.
        stream_url_cache_ttl: How long stream URLs stay cached, in seconds.
        ydl_format: The yt-dlp format selector used when extracting stream URLs.
        max_search_results: The upper limit on results that /media/search returns.
        spotify_client_id: The Spotify application client ID, if Spotify support is set up.
        spotify_client_secret: The Spotify application client secret, if Spotify support is set up.
    """

    metadata_cache_ttl: int = 3600
    stream_url_cache_ttl: int = 300
    ydl_format: str = "bestaudio/best"
    max_search_results: int = 10
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Returns the settings, read once and then cached for the process."""
    return Settings()
