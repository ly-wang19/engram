# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS console-build
WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENGRAM_DATA_DIR=/data \
    ENGRAM_EMBEDDER=hashing \
    ENGRAM_MAX_REQUEST_BYTES=2097152

WORKDIR /app

RUN groupadd --gid 10001 engram \
    && useradd --uid 10001 --gid engram --home-dir /nonexistent --shell /usr/sbin/nologin engram \
    && install -d -o engram -g engram /data

COPY pyproject.toml README.md LICENSE COMMERCIAL-LICENSE.md ./
COPY engram/ ./engram/
RUN python -m pip install ".[server,mcp]"

COPY --from=console-build /build/frontend/dist/ ./frontend/dist/

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
    CMD ["python", "-c", "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3); assert r.status == 200 and json.load(r)['ready']"]

CMD ["python", "-m", "uvicorn", "engram.server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
