# The uv stage only supplies the uv binary. A named stage lets Dependabot keep its tag current.
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

# yt-dlp runs YouTube's JavaScript with Deno, and naming the stage lets Dependabot keep it current.
FROM denoland/deno:bin-2.9.7 AS deno

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies are installed before the source is copied, so code changes reuse the cached layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN useradd --create-home appuser
WORKDIR /app
# The virtual environment holds the installed package, so the runtime image needs no source tree.
COPY --from=builder /app/.venv /app/.venv
COPY --from=deno /deno /usr/local/bin/deno
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
# Docker and compose mark the container unhealthy when /health stops answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "media_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
