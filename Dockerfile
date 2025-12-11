# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/ama \
    AMA_DATA_DIRECTORY=/app/data

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends --yes \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ama \
    && useradd --uid 10001 --gid ama --create-home --shell /usr/sbin/nologin ama

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install . \
    && mkdir -p /app/data \
    && chown ama:ama /app/data

USER ama

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["ama-api", "--host", "0.0.0.0", "--port", "8000"]
