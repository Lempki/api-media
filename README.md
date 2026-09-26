# discord-api-media

This is a REST API that centralizes media metadata resolution for Discord bots. It wraps [yt-dlp](https://github.com/yt-dlp/yt-dlp) to provide track information, stream URLs, and search results for YouTube, SoundCloud, and Spotify over HTTP. Bots call this API instead of bundling yt-dlp themselves, keeping their dependencies minimal and allowing media support to be updated in a single place. This project is based on the [discord-api-template](https://github.com/Lempki/discord-api-template) repository, which provides the core architecture.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/media/info` | Resolve a URL or search query to full track metadata including a playable stream URL. |
| `GET` | `/media/playlist` | Expand a YouTube playlist or Spotify album/playlist into an ordered list of tracks. |
| `POST` | `/media/search` | Search for tracks and return a list of results. Results do not include stream URLs; call `/media/info` after the user selects a result. |
| `GET` | `/health` | Returns the service name and version. Used for uptime monitoring. |

All endpoints except `/health` require a bearer token in the `Authorization` header.

### GET /media/info

Accepts either a `url` parameter or a `query` + `source` pair. Providing both is an error. Supported URL types:

* YouTube video URLs.
* SoundCloud track URLs.
* Spotify track URLs (`spotify.com/track/…`).

```
GET /media/info?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ
GET /media/info?url=https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8?si=dcb2cb604ade42a8
GET /media/info?query=rick+astley&source=youtube
```

Spotify URLs are resolved to a matching YouTube video using the Spotify track name and artist. Requires `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` for accurate Spotify matching; falls back to an ID-based search if credentials are absent.

Response fields include `title`, `duration_seconds`, `duration_formatted`, `uploader`, `thumbnail_url`, `webpage_url`, `stream_url`, `stream_url_expires_at`, and `is_live`.

Stream URLs from YouTube expire after a short time. The API caches them for five minutes. Metadata is cached for one hour.

### GET /media/playlist

Accepts a `url` parameter. Supported URL types:

* YouTube playlist URLs (containing `list=`).
* Spotify album URLs (`spotify.com/album/…`).
* Spotify playlist URLs (`spotify.com/playlist/…`).

```
GET /media/playlist?url=https://www.youtube.com/playlist?list=PL2MI040U_GXobmpXtTwBF7oHBGT5BETSD
GET /media/playlist?url=https://open.spotify.com/album/6eUW0wxWtzkFdaEFsTJto6?si=ZDLdDue4SNWAjCUsIKsbKw
GET /media/playlist?url=https://open.spotify.com/playlist/19RcUUR4b9oxhcREqD8Xoq?si=75Lt4s1fSQS4OhpDCS3Oag
```

Returns a `tracks` array. Each item contains `title`, `webpage_url`, `duration_seconds`, `duration_formatted`, and `thumbnail_url`. Stream URLs are intentionally omitted; call `/media/info?url=<webpage_url>` per track at play time to avoid serving expired URLs from a stale queue.

For Spotify collections, each track is resolved to a YouTube `webpage_url` by searching YouTube for the track name and artist. Up to five searches run in parallel.

### POST /media/search

```json
{
  "query": "lofi hip hop",
  "source": "youtube",
  "max_results": 5
}
```

Supported sources are `youtube` and `soundcloud`.

## Prerequisites

* [Docker](https://docs.docker.com/get-started/get-docker/) and Docker Compose.

Running without Docker requires Python 3.12, [uv](https://docs.astral.sh/uv/), and FFmpeg available in the system PATH. On Windows, install uv with `winget install --id astral-sh.uv`.

## Setup

You can use the included setup script to prepare the project in a single step.

On Windows, run the following command:

```
setup.bat
```

On macOS or Linux, run the following commands:

```
chmod +x setup.sh
./setup.sh
```

The script runs `uv sync`, which creates the `.venv` virtual environment if needed and installs the package with its locked dependencies. It copies `.env.template` to `.env` on the first run. You must edit `.env` and set `DISCORD_API_SECRET` before starting the API.

If you prefer to perform the setup manually, follow these steps:

```bash
uv sync
cp .env.template .env
# Edit .env and set DISCORD_API_SECRET and other values as needed.
uv run uvicorn media_api.main:app --port 8001
```

### Docker

Alternatively, you can run the API as a Docker container.

1. Copy `.env.template` to `.env` and set `DISCORD_API_SECRET`.
2. Build and start the container:

   ```
   docker-compose up --build
   ```

The container runs on port `8000` internally. Docker Compose maps it to port `8001` on the host.

## Configuration

All configuration is read from environment variables or from a `.env` file in the project root.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_API_SECRET` | Yes | — | Shared bearer token. All Discord bots must send this value in the `Authorization` header. |
| `LOG_LEVEL` | No | `INFO` | Log verbosity. Accepts standard Python logging levels. |
| `METADATA_CACHE_TTL` | No | `3600` | How long to cache track metadata in seconds. |
| `STREAM_URL_CACHE_TTL` | No | `300` | How long to cache stream URLs in seconds. YouTube URLs expire, so keep this value short. |
| `YDL_FORMAT` | No | `bestaudio/best` | The yt-dlp format selector used when extracting stream URLs. |
| `MAX_SEARCH_RESULTS` | No | `10` | Upper limit on results returned by `/media/search`. |
| `SPOTIFY_CLIENT_ID` | No | — | Spotify application Client ID. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard). Required for accurate Spotify track, album, and playlist resolution. |
| `SPOTIFY_CLIENT_SECRET` | No | — | Spotify application Client Secret. Required alongside `SPOTIFY_CLIENT_ID`. |

## Project structure

```
discord-api-media/
├── src/media_api/
│   ├── main.py         # FastAPI application and route definitions.
│   ├── config.py       # Environment variable reader.
│   ├── auth.py         # Bearer token dependency.
│   ├── models.py       # Pydantic request and response models.
│   ├── extractor.py    # yt-dlp wrapper with asyncio.to_thread and TTL caching.
│   ├── cache.py        # Metadata and stream URL TTL caches.
│   └── sources/
│       ├── youtube.py      # YouTube helper.
│       ├── soundcloud.py   # SoundCloud helper.
│       └── spotify.py      # Spotify resolver.
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml      # Project metadata and dependencies.
├── uv.lock             # Locked dependency versions.
├── ruff.toml           # Lint and format settings on top of the shared baseline.
├── setup.bat           # Windows setup script.
├── setup.sh            # macOS and Linux setup script.
└── .env.template       # Template for environment variables.
```

## Running tests

```bash
uv run pytest
```

Run every lint and format check with `uvx pre-commit run --all-files`, or install the hooks once with `uvx pre-commit install` so they run on each commit.
The coding, prose, and commit conventions are documented in [discord-dev-standards](https://github.com/Lempki/discord-dev-standards).
