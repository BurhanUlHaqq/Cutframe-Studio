# syntax=docker/dockerfile:1
FROM node:24-slim AS javascript
FROM mwader/static-ffmpeg:9.0.2-1 AS media
FROM ghcr.io/astral-sh/uv:0.13.9 AS installer
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --system studio \
    && useradd --system --gid studio --home-dir /data studio \
    && mkdir -p /data \
    && chown studio:studio /data

RUN apt-get update \
    && apt-get install -y --no-install-recommends fontconfig fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
ARG UV_DEFAULT_INDEX=https://pypi.org/simple
RUN --mount=from=installer,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy uv pip install --system -r requirements.txt

COPY --from=javascript /usr/local/bin/node /usr/local/bin/node
COPY --from=media /ffmpeg /ffprobe /usr/local/bin/
COPY manage.py ./
COPY shorts_generator ./shorts_generator
COPY studio ./studio
COPY publishing ./publishing

USER studio
EXPOSE 8000
