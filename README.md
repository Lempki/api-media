# api-media

This is a REST API that centralizes media metadata resolution for bots and other clients. It wraps [yt-dlp](https://github.com/yt-dlp/yt-dlp) to provide track information, stream URLs, and search results for YouTube, SoundCloud, and Spotify over HTTP. Clients call this API instead of bundling yt-dlp themselves, keeping their dependencies minimal and allowing media support to be updated in a single place. This project is based on the [api-template](https://github.com/Lempki/api-template) repository, which provides the core architecture.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/media/info` | Resolve a URL or search query to full track metadata including a playable stream URL. |
| `GET` | `/media/playlist` | Expand a YouTube playlist or Spotify album/playlist into an ordered list of tracks. |
| `POST` | `/media/search` | Search for tracks and return a list of results. Results do not include stream URLs. Call `/media/info` after the user selects a result. |
| `GET` | `/health` | Returns the service name and version. Used for uptime monitoring and as the Docker image's health check. |

All endpoints except `/health` require a bearer token in the `Authorization` header.
A request without the header or with a wrong token gets `401 Unauthorized` with a `WWW-Authenticate: Bearer` header.
Tokens are compared in constant time.

When yt-dlp or Spotify fails, the endpoint answers `502 Bad Gateway`.
Its detail is always the same text, "The media source could not be reached."
The error itself goes only to the server log, so no internal detail reaches the caller.

### GET /media/info

Accepts either a `url` parameter or a `query` + `source` pair. Providing both or neither gets `400 Bad Request`. The `source` parameter accepts `youtube`, which is the default, or `soundcloud`. Any other value gets `422 Unprocessable Content`. A query without any search results gets `404 Not Found`. Supported URL types:

* YouTube video URLs.
* SoundCloud track URLs.
* Spotify track URLs (`spotify.com/track/…`).

```
GET /media/info?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ
GET /media/info?url=https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8?si=dcb2cb604ade42a8
GET /media/info?query=rick+astley&source=youtube
```

Spotify URLs are resolved to a matching YouTube video using the Spotify track name and artist. That needs `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Without them, the API reads the track title from Spotify's public oEmbed endpoint and searches for the title alone, which can match a cover or a remix instead.

Response fields include `source`, `title`, `duration_seconds`, `duration_formatted`, `uploader`, `thumbnail_url`, `webpage_url`, `stream_url`, `stream_url_expires_at`, and `is_live`.

Stream URLs from YouTube expire after a short time. `stream_url_expires_at` is the moment the returned stream URL stops working. The API reads it from the `expire` parameter that YouTube stream URLs carry and never sets it more than `STREAM_URL_CACHE_TTL` seconds ahead, which is five minutes by default. A cached stream URL is served until 30 seconds before it expires. After that, the next request extracts the track again. Metadata is cached for one hour by default.

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

Returns a `tracks` array. Each item contains `title`, `webpage_url`, `duration_seconds`, `duration_formatted`, and `thumbnail_url`. Stream URLs are intentionally omitted. Call `/media/info?url=<webpage_url>` per track at play time to avoid serving expired URLs from a stale queue.

For Spotify collections, each track is resolved to a YouTube `webpage_url` by searching YouTube for the track name and artist. Up to five searches run in parallel. Spotify albums and playlists require `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Without them, the request gets `503 Service Unavailable`, and the detail says that Spotify albums and playlists are not configured on this server.

Any other URL gets `400 Bad Request`. Spotify track URLs belong in `/media/info` instead.

### POST /media/search

```json
{
  "query": "lofi hip hop",
  "source": "youtube",
  "max_results": 5
}
```

Supported sources are `youtube` and `soundcloud`. `max_results` must be between 1 and 25, and `MAX_SEARCH_RESULTS` caps it further. Any other source or an out-of-range `max_results` gets `422 Unprocessable Content`.

## Prerequisites

* [Docker](https://docs.docker.com/get-started/get-docker/) and Docker Compose.

Running without Docker requires Python 3.12, [uv](https://docs.astral.sh/uv/), and [Deno](https://deno.com/) available in the system PATH. yt-dlp runs YouTube's player JavaScript with Deno, so YouTube extraction fails or finds fewer formats without it. On Windows, install uv with `winget install --id astral-sh.uv` and Deno with `winget install --id DenoLand.Deno`. The Docker image already includes Deno.

## Setup

The setup script prepares the project in a single run, and it is safe to run again at any time.

On Windows, double-click `setup.bat` or run it from a terminal:

```
setup.bat
```

On macOS or Linux, run the following commands:

```
chmod +x setup.sh
./setup.sh
```

The script asks before it installs anything, and it does the following:

1. It installs [uv](https://docs.astral.sh/uv/) when uv is missing. uv also provides Python 3.12 when the machine lacks it.
2. It offers to install Docker, and the tools that the Docker image includes for running outside Docker. It uses winget on Windows, Homebrew on macOS, and the system package manager on Linux. On Windows it also turns on WSL, which Docker Desktop needs, and says when Windows needs a restart or virtualization is turned off in the firmware. It shows the computer's RAM and offers to cap the memory of Docker Desktop's virtual machine, at a suggested or your own size.
3. It runs `uv sync`, which installs the package and its locked dependencies into `.venv`.
4. It copies `.env.template` to `.env` on the first run and fills `API_SECRET` with a random value.

A step that fails says what went wrong, why it matters, and what to do next, and the summary at the end lists it again.
The steps live in `scripts/bootstrap.py`, which needs only the Python standard library.

If you prefer to perform the setup manually, follow these steps:

```bash
uv sync
cp .env.template .env
# Edit .env and set API_SECRET and other values as needed.
uv run uvicorn media_api.main:app --port 8001
```

### Running

After setup has run once, the run script starts the API.
Double-click `run.bat` on Windows, or run `./run.sh` on macOS and Linux.
It builds and starts the API in Docker in the background, waits until its health check passes, and shows its status.
The container then starts again whenever Docker starts.

The script also takes an action, such as `run.bat stop` on Windows or `./run.sh stop` elsewhere:

| Action | What it does |
|---|---|
| `start` | Builds and starts everything in Docker and waits until it is ready. It is the default. |
| `stop` | Stops the containers. They stay stopped until the next start. |
| `status` | Shows whether each container runs and is healthy. |
| `logs` | Follows the logs. Press Ctrl+C to stop following. |
| `update` | Pulls the latest code, rebuilds on fresh base images, and restarts. |
| `local` | Runs the project in the terminal without Docker. Press Ctrl+C to stop it. |

When a service crashes right after it starts, the script shows the end of its log and stops it, so it does not restart over and over.
The `update` action needs a Git clone. In a downloaded release, it explains how to replace the files by hand instead.

### Docker

Alternatively, you can run the API as a Docker container.

1. Copy `.env.template` to `.env` and set `API_SECRET`.
2. Build and start the container:

   ```
   docker compose up --build
   ```

The container runs on port `8000` internally. Docker Compose maps it to port `8001` on the host.
The image has a health check that calls `/health`, so `docker ps` shows whether the container is healthy.
The service keeps no state.
Its caches live in memory and start empty after every restart, so the container needs no data volume.
Its only mount is the read-only `cookies` folder.

## Configuration

All configuration is read from environment variables or from a `.env` file in the project root.

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_SECRET` | Yes | None | Shared bearer token of at least 16 characters. Every client must send this value in the `Authorization` header. The service refuses to start with a placeholder or short secret. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `LOG_LEVEL` | No | `INFO` | Log verbosity. Accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Every log line, including uvicorn's access log, is one JSON object. |
| `METADATA_CACHE_TTL` | No | `3600` | How long to cache track metadata in seconds. Must be greater than 0. |
| `STREAM_URL_CACHE_TTL` | No | `300` | The longest time a stream URL stays cached, in seconds. A URL that expires sooner is dropped 30 seconds before its own expiry. Must be at least 60. |
| `YDL_FORMAT` | No | `bestaudio/best` | The yt-dlp format selector used when extracting stream URLs. |
| `YDL_COOKIES_FILE` | No | Not set | Path to a cookies file in Netscape format that yt-dlp sends with its requests. Use it when YouTube answers with "Sign in to confirm you're not a bot". The file must exist when the service starts. [YouTube cookies](#youtube-cookies) explains how to export it. |
| `MAX_SEARCH_RESULTS` | No | `10` | Upper limit on results returned by `/media/search`. Must be at least 1. |
| `SPOTIFY_CLIENT_ID` | No | Not set | Spotify application Client ID. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard). Required for Spotify albums and playlists, and for accurate Spotify track matching. |
| `SPOTIFY_CLIENT_SECRET` | No | Not set | Spotify application Client Secret. Required alongside `SPOTIFY_CLIENT_ID`. |

### YouTube cookies

YouTube sometimes refuses requests from an address it does not trust, most often a server in a data center.
yt-dlp then fails with "Sign in to confirm you're not a bot".
Sending the cookies of a signed-in YouTube session usually gets past this check.

A cookies file gives full access to the Google account it came from, so treat it like a password.
Use a separate account made for this, because Google may restrict accounts that it sees being used by yt-dlp.

1. Open a private browser window and sign in to YouTube with that account.
2. In the same tab, go to `https://www.youtube.com/robots.txt`. The session then stays on a page that never refreshes its cookies.
3. Export the cookies for `youtube.com` with a browser extension that writes the Netscape `cookies.txt` format, such as Get cookies.txt LOCALLY. Save the file as `cookies/youtube.txt` in this repository.
4. Close the private window without signing out. Signing out would invalidate the exported cookies.
5. Add `YDL_COOKIES_FILE=cookies/youtube.txt` to `.env` and restart the service.

Git ignores everything in the `cookies` folder except the file that keeps the folder in the repository.
Docker Compose mounts the folder read-only at `/app/cookies`, which is the same relative path the setting uses outside Docker.
The service never changes the file.
Each extraction works on a private copy, because yt-dlp rewrites its cookies file after every use.
When YouTube asks to sign in again, the cookies have expired, so export a new file.

## Project structure

```
api-media/
├── src/media_api/
│   ├── main.py           # FastAPI application and route definitions.
│   ├── config.py         # This service's settings on top of ServiceSettings.
│   ├── service.py        # Shared settings, secret validation, and the version lookup.
│   ├── logging_config.py # JSON logging for every logger, including uvicorn's.
│   ├── auth.py           # Bearer token dependency.
│   ├── models.py         # Pydantic request and response models.
│   ├── extractor.py      # yt-dlp wrapper with asyncio.to_thread and TTL caching.
│   ├── cache.py          # Metadata and stream URL TTL caches.
│   └── sources/
│       └── spotify.py    # Spotify resolver.
├── tests/
├── cookies/              # Optional YouTube cookies file. Git ignores its contents.
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml        # Project metadata and dependencies.
├── uv.lock               # Locked dependency versions.
├── ruff.toml             # Lint and format settings on top of the shared baseline.
├── setup.bat             # Windows setup script.
├── setup.sh              # macOS and Linux setup script.
├── scripts/bootstrap.py  # The steps that both setup scripts run.
├── scripts/run.py        # The actions that both run scripts take.
├── run.bat               # Windows run script.
├── run.sh                # macOS and Linux run script.
└── .env.template         # Template for environment variables.
```

## Running tests

```bash
uv run pytest
```

Run every lint and format check with `uvx pre-commit run --all-files`, or install the hooks once with `uvx pre-commit install` so they run on each commit.
The coding, prose, and commit conventions are documented in [dev-standards](https://github.com/Lempki/dev-standards).

## License

This project is licensed under the [MIT License](LICENSE).
You may use, change, and share it, as long as every copy keeps the copyright notice and the license text.
