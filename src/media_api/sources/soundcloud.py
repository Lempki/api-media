from typing import Any

from ..config import Settings
from ..extractor import TrackSummary, fetch_info, search


async def get_info(url: str, settings: Settings) -> dict[str, Any]:
    return await fetch_info(url, settings)


async def search_tracks(
    query: str, max_results: int, settings: Settings
) -> list[TrackSummary]:
    return await search(query, "soundcloud", max_results, settings)
