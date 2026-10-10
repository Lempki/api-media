"""Tests for /media/stream, which relays a track's audio from yt-dlp's downloader."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from media_api import streamer
from media_api.config import Settings
from media_api.main import app

from .conftest import SECRET

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {SECRET}"}
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Enough audio for several chunks, so the relay loop runs more than once.
AUDIO = bytes(range(256)) * 1000


def fake_ytdlp(monkeypatch: pytest.MonkeyPatch, program: str) -> list[list[str]]:
    """Replaces yt-dlp with a Python program and records the command each stream asked for."""
    commands: list[list[str]] = []
    real_command = streamer.ytdlp_command

    def command(url: str, settings: Settings, cookie_file: Path | None) -> list[str]:
        commands.append(real_command(url, settings, cookie_file))
        return [sys.executable, "-c", program]

    monkeypatch.setattr(streamer, "ytdlp_command", command)
    return commands


def test_stream_relays_all_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = fake_ytdlp(
        monkeypatch,
        f"import sys; sys.stdout.buffer.write(bytes(range(256)) * {len(AUDIO) // 256})",
    )

    r = client.get("/media/stream", params={"url": VIDEO}, headers=AUTH)

    assert r.status_code == 200
    assert r.content == AUDIO
    assert commands[0][-2:] == ["--", VIDEO]


def test_track_without_audio_is_502_without_the_ytdlp_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ytdlp(
        monkeypatch,
        "import sys; sys.stderr.write('ERROR: Video unavailable'); sys.exit(1)",
    )

    r = client.get("/media/stream", params={"url": VIDEO}, headers=AUTH)

    assert r.status_code == 502
    assert "unavailable" not in r.text


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh",
        "file:///etc/passwd",
        "--exec=whoami",
    ],
)
def test_stream_accepts_only_web_page_urls(url: str) -> None:
    r = client.get("/media/stream", params={"url": url}, headers=AUTH)

    assert r.status_code == 400


def test_command_writes_audio_to_stdout_with_the_format(tmp_path: Path) -> None:
    settings = Settings(api_secret=SECRET, ydl_format="bestaudio", _env_file=None)

    command = streamer.ytdlp_command(VIDEO, settings, tmp_path / "cookies.txt")

    assert command[command.index("--format") + 1] == "bestaudio"
    assert command[command.index("--output") + 1] == "-"
    assert command[command.index("--cookies") + 1] == str(tmp_path / "cookies.txt")
    assert command[-2:] == ["--", VIDEO]


def test_command_without_cookies_sends_none() -> None:
    settings = Settings(api_secret=SECRET, _env_file=None)

    assert "--cookies" not in streamer.ytdlp_command(VIDEO, settings, None)


def test_workdir_holds_a_private_cookie_copy_and_is_removed(tmp_path: Path) -> None:
    original = tmp_path / "youtube.txt"
    original.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    workdir, copy = streamer._make_workdir(original)
    assert copy is not None
    assert copy.parent == workdir
    assert copy.read_text(encoding="utf-8") == original.read_text(encoding="utf-8")

    streamer._remove_workdir(workdir)
    assert not workdir.exists()
    assert original.exists()


def test_ytdlp_runs_in_its_own_writable_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    # An HLS track makes yt-dlp write fragment files into its working directory.
    fake_ytdlp(
        monkeypatch,
        "import os, sys; open('--Frag1.part', 'wb').write(b'x'); "
        "sys.stdout.write(os.getcwd())",
    )

    r = client.get("/media/stream", params={"url": VIDEO}, headers=AUTH)

    assert r.status_code == 200
    workdir = Path(r.text)
    assert "api-media-stream-" in workdir.name
    assert not workdir.exists()
