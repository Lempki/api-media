# api-media

A FastAPI service that resolves YouTube, SoundCloud, and Spotify metadata and stream URLs for bots and other clients.
It wraps yt-dlp and spotipy so clients can call this API instead of bundling those dependencies themselves.
It is based on the [api-template](https://github.com/Lempki/api-template) repository.
The shared conventions live in [dev-standards](https://github.com/Lempki/dev-standards), and its README is the rulebook for code, prose, commits, and engineering guidelines.
Read it before changing code. When the repositories are cloned side by side, the local copy is `../dev-standards/README.md`.

## Commands

* `uv sync` installs the package and its locked dependencies into `.venv`.
* `uv run uvicorn media_api.main:app --reload` starts the API on port 8000. It reads its settings from `.env`.
* Running the API outside Docker needs Deno on PATH, because yt-dlp runs YouTube's player JavaScript with it. The Docker image copies Deno in from a named stage.
* `uv run pytest` runs the tests.
* `uvx pre-commit run --all-files` runs every lint and format hook.
* `docker compose up --build` runs the API in a container, published on host port 8001.

## Layout

* `src/media_api/main.py` defines the app, the lifespan, and the routes. A failed upstream call answers 502 with a fixed detail, and the exception goes only to the log through `logger.exception`.
* `src/media_api/config.py` adds this service's settings to `ServiceSettings`.
* `src/media_api/service.py` holds `ServiceSettings`, which validates the shared secret, and `service_version()`, which reads the version from pyproject.toml.
* `src/media_api/logging_config.py` turns every log record, including uvicorn's, into one JSON line.
* `src/media_api/auth.py` holds the bearer token dependency that protects every route except `/health`.
* `src/media_api/models.py` holds the request and response models. `source` accepts only `youtube` and `soundcloud`, and `max_results` must be between 1 and 25.
* `src/media_api/extractor.py` wraps yt-dlp with `asyncio.to_thread` and TTL caching. It rounds every duration to whole seconds and always returns a fresh dict.
* yt-dlp rewrites its cookies file whenever a `YoutubeDL` closes, so every extraction hands it a temporary copy of `YDL_COOKIES_FILE`. Never pass the configured path to yt-dlp directly.
* `src/media_api/streamer.py` runs yt-dlp's downloader as a subprocess for `/media/stream` and relays its audio. Closing the iterator kills the process, which is how a skip stops the download. A run that ends with HTTP 403 or 5xx before any audio starts again, up to `STREAM_ATTEMPTS` runs.
* `src/media_api/cache.py` holds the metadata and stream URL TTL caches. A stream entry keeps its URL together with its expiry, which comes from the URL's `expire` parameter and is capped by `STREAM_URL_CACHE_TTL`. An entry counts as expired 30 seconds early, and the track is then extracted again.
* `src/media_api/sources/spotify.py` resolves Spotify tracks, albums, and playlists to YouTube tracks. It checks URLs by host and reuses one Spotify client per credential pair.

## Template origin

* `src/media_api/auth.py`, `src/media_api/logging_config.py`, `src/media_api/service.py`, `tests/test_shared.py`, `.dockerignore`, `setup.sh`, `setup.bat`, `scripts/bootstrap.py`, `tests/test_bootstrap.py`, `run.bat`, `run.sh`, `scripts/run.py`, `tests/test_run.py`, `.pre-commit-config.yaml`, and `.github/dependabot.yml` are kept identical to the template, per its `.template-manifest.toml`.
* Check `dev-standards template-check --template <path-to-api-template> --diff` before hand-editing one of those files.
* Keep the version only in pyproject.toml, and keep `SERVICE` in main.py equal to the project name there.
* `uv run mypy src` must pass in strict mode, because CI runs it.
