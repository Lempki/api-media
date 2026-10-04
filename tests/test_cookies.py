"""Tests for the optional cookies file that yt-dlp sends with its requests."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from media_api import extractor
from media_api.config import Settings

from .conftest import SECRET

COOKIES = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\toriginal\n"


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL and rewrites its cookies file on close, as yt-dlp does.

    Attributes:
        seen: The cookies file path and its contents, recorded when each instance opens.
    """

    seen: list[tuple[str, str]] = []

    def __init__(self, params: dict[str, Any]) -> None:
        self._cookie_file = Path(params["cookiefile"])
        FakeYoutubeDL.seen.append(
            (str(self._cookie_file), self._cookie_file.read_text(encoding="utf-8"))
        )

    def __enter__(self) -> "FakeYoutubeDL":
        return self

    def __exit__(self, *args: object) -> None:
        self._cookie_file.write_text("rotated by yt-dlp\n", encoding="utf-8")

    def extract_info(self, url: str, download: bool) -> dict[str, Any]:
        return {"webpage_url": url, "entries": []}


@pytest.fixture
def cookie_file(tmp_path: Path) -> Path:
    path = tmp_path / "youtube.txt"
    path.write_text(COOKIES, encoding="utf-8")
    return path


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch) -> type[FakeYoutubeDL]:
    FakeYoutubeDL.seen = []
    monkeypatch.setattr(extractor.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    return FakeYoutubeDL


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_cookies_file_means_none(value: str) -> None:
    settings = Settings(api_secret=SECRET, ydl_cookies_file=value, _env_file=None)

    assert settings.ydl_cookies_file is None


def test_missing_cookies_file_is_rejected_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ydl_cookies_file"):
        Settings(
            api_secret=SECRET,
            ydl_cookies_file=tmp_path / "missing.txt",
            _env_file=None,
        )


def test_cookies_file_is_passed_to_yt_dlp(cookie_file: Path) -> None:
    settings = Settings(api_secret=SECRET, ydl_cookies_file=cookie_file, _env_file=None)

    assert extractor._make_ydl_opts(settings)["cookiefile"] == str(cookie_file)


def test_no_cookies_file_sends_no_cookies(settings: Settings) -> None:
    assert "cookiefile" not in extractor._make_ydl_opts(settings)


def test_extraction_never_rewrites_the_configured_file(
    cookie_file: Path, fake_ydl: type[FakeYoutubeDL]
) -> None:
    opts = {"cookiefile": str(cookie_file)}

    extractor._extract_blocking("https://youtu.be/dQw4w9WgXcQ", opts)
    extractor._search_blocking("never gonna", "youtube", 5, opts)

    assert cookie_file.read_text(encoding="utf-8") == COOKIES
    for used_path, contents in fake_ydl.seen:
        assert used_path != str(cookie_file)
        assert contents == COOKIES
    assert all(not Path(used_path).exists() for used_path, _ in fake_ydl.seen)
