# Two targets: `serve` (the read-only MCP server; python and pypdf only) and `build` (the weekly fetch,
# fulltext and build jobs; adds poppler and tesseract for scanned PDFs).
#   docker build --target serve -t sb:serve .
#   docker build --target build -t sb:build .
# The base is pinned by digest (python:3.13-slim, looked up 2026-11-26); move it on purpose.
FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin sb \
    && mkdir /data && chown sb /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sb ./sb
USER sb
VOLUME /data
ENTRYPOINT ["python", "-m", "sb"]

FROM base AS serve
EXPOSE 8790
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/healthz', timeout=4)"]
CMD ["serve", "--data", "/data", "--host", "0.0.0.0"]

FROM base AS build
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
USER sb
CMD ["status", "--content", "/content", "--data", "/data"]
