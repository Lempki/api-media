"""Streams a track's audio through yt-dlp's downloader, for clients that play it live.

Playing a YouTube stream URL directly holds one connection open for the whole track.
YouTube resets such connections partway through and sometimes refuses them outright.
yt-dlp downloads in ranges, sends the headers YouTube expects, and retries.
The audio is relayed to the client as it arrives, so playback starts without waiting.
"""

import asyncio
import contextlib
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from .config import Settings

__all__ = [
    "CHUNK_SIZE",
    "STREAM_ATTEMPTS",
    "StreamError",
    "open_audio",
    "ytdlp_command",
]

log = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024

# yt-dlp's last error lines explain a failed track, and nothing older is needed.
_STDERR_LIMIT = 2000

# YouTube now and then refuses a download link with 403 Forbidden, and a fresh link usually works.
# A new yt-dlp run gets a fresh link, so a track gets this many tries before it counts as failed.
STREAM_ATTEMPTS = 3

# The errors that a new try can fix, unlike an unavailable or private video.
_TRANSIENT_ERROR = re.compile(r"HTTP Error (403|5\d\d)")


class StreamError(Exception):
    """Raised when yt-dlp ends before it produced any audio."""


def ytdlp_command(url: str, settings: Settings, cookie_file: Path | None) -> list[str]:
    """Builds the yt-dlp command that writes a track's audio to standard output.

    Args:
        url: The track's page URL.
        settings: The settings that hold the format selector.
        cookie_file: A private copy of the cookies file, or None to send no cookies.

    Returns:
        The program and its arguments.
    """
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--format",
        settings.ydl_format,
        "--output",
        "-",
    ]
    if cookie_file is not None:
        command += ["--cookies", str(cookie_file)]
    # The separator keeps a URL that starts with a dash from being read as an option.
    return [*command, "--", url]


def _make_workdir(cookie_file: Path | None) -> tuple[Path, Path | None]:
    """Creates the private folder that one stream's yt-dlp runs in.

    yt-dlp writes the fragments of an HLS track, such as a SoundCloud track, next to its output.
    The image's working directory is not writable, so each stream gets a folder of its own.
    A folder per stream also keeps two streams from overwriting each other's fragments.
    yt-dlp rewrites its cookies file when it finishes, so the folder holds a private copy.

    Returns:
        The folder, and the cookies copy in it or None without a cookies file.
    """
    workdir = Path(tempfile.mkdtemp(prefix="api-media-stream-"))
    if cookie_file is None:
        return workdir, None
    copy = workdir / "cookies.txt"
    shutil.copyfile(cookie_file, copy)
    return workdir, copy


def _remove_workdir(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


async def _read_tail(stream: asyncio.StreamReader | None) -> str:
    # Reading stderr to the end keeps a chatty yt-dlp from blocking on a full pipe.
    if stream is None:
        return ""
    tail = b""
    while chunk := await stream.read(4096):
        tail = (tail + chunk)[-_STDERR_LIMIT:]
    return tail.decode(errors="replace").strip()


async def open_audio(url: str, settings: Settings) -> AsyncIterator[bytes]:
    """Starts yt-dlp for a track and waits for its first audio.

    A run that YouTube refuses before any audio is tried again, up to STREAM_ATTEMPTS runs.

    Args:
        url: The track's page URL.
        settings: The settings that hold the format selector and the cookies file.

    Returns:
        The audio in chunks, starting with the first one.
        Closing the iterator stops yt-dlp, such as when the client hangs up.

    Raises:
        StreamError: yt-dlp ended without audio, such as for an unavailable video.
    """
    for attempt in range(1, STREAM_ATTEMPTS):
        try:
            return await _start(url, settings)
        except StreamError as error:
            if not _TRANSIENT_ERROR.search(str(error)):
                raise
            log.warning(
                f"Try {attempt} to stream {url} was refused, so it starts again: {error}"
            )
    return await _start(url, settings)


async def _start(url: str, settings: Settings) -> AsyncIterator[bytes]:
    """Runs yt-dlp once and waits for its first audio, as open_audio describes."""
    workdir, cookie_copy = await asyncio.to_thread(
        _make_workdir, settings.ydl_cookies_file
    )
    process = await asyncio.create_subprocess_exec(
        *ytdlp_command(url, settings, cookie_copy),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workdir,
    )
    errors = asyncio.create_task(_read_tail(process.stderr))
    assert process.stdout is not None
    first = await process.stdout.read(CHUNK_SIZE)
    if not first:
        await process.wait()
        detail = await errors
        await asyncio.to_thread(_remove_workdir, workdir)
        raise StreamError(detail or f"yt-dlp exited with code {process.returncode}.")
    return _relay(first, process, errors, workdir)


async def _relay(
    first: bytes,
    process: asyncio.subprocess.Process,
    errors: "asyncio.Task[str]",
    workdir: Path,
) -> AsyncIterator[bytes]:
    assert process.stdout is not None
    try:
        yield first
        while chunk := await process.stdout.read(CHUNK_SIZE):
            yield chunk
        if await process.wait() != 0:
            log.warning(
                f"yt-dlp stopped with code {process.returncode} mid-track: {await errors}"
            )
    finally:
        if process.returncode is None:
            # The client stopped listening, such as after /skip, so the download stops too.
            # The process may have ended on its own a moment ago.
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        errors.cancel()
        await asyncio.to_thread(_remove_workdir, workdir)
