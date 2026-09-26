# discord-api-media

A FastAPI service that resolves YouTube, SoundCloud, and Spotify metadata and stream URLs for Discord bots.
It wraps yt-dlp and spotipy so bots can call this API instead of bundling those dependencies themselves.
It is based on the [discord-api-template](https://github.com/Lempki/discord-api-template) repository.
The shared conventions live in [discord-dev-standards](https://github.com/Lempki/discord-dev-standards), and its README is the rulebook for code, prose, and commits.

## Commands

* `uv sync` installs the package and its locked dependencies into `.venv`.
* `uv run uvicorn media_api.main:app --reload` starts the API on port 8000. It reads its settings from `.env`.
* `uv run pytest` runs the tests.
* `uvx pre-commit run --all-files` runs every lint and format hook.
* `docker-compose up --build` runs the API in a container, published on host port 8001.

## Layout

* `src/media_api/main.py` defines the app, the lifespan, and the routes.
* `src/media_api/config.py` adds this service's settings to `ServiceSettings`.
* `src/media_api/service.py` holds `ServiceSettings`, which validates the shared secret, and `service_version()`, which reads the version from pyproject.toml.
* `src/media_api/logging_config.py` turns every log record, including uvicorn's, into one JSON line.
* `src/media_api/auth.py` holds the bearer token dependency that protects every route except `/health`.
* `src/media_api/models.py` holds the request and response models.
* `src/media_api/extractor.py` wraps yt-dlp with `asyncio.to_thread` and TTL caching.
* `src/media_api/cache.py` holds the metadata and stream URL TTL caches.
* `src/media_api/sources/` holds the YouTube, SoundCloud, and Spotify source helpers.

## Template origin

* `src/media_api/auth.py`, `src/media_api/logging_config.py`, `src/media_api/service.py`, `.dockerignore`, `setup.sh`, `setup.bat`, `.pre-commit-config.yaml`, and `.github/dependabot.yml` are kept identical to the template, per its `.template-manifest.toml`.
* Check `dev-standards template-check --template <path-to-discord-api-template> --diff` before hand-editing one of those files.
* Keep the version only in pyproject.toml, and keep `SERVICE` in main.py equal to the project name there.
* `uv run mypy src` must pass in strict mode, because CI runs it.
