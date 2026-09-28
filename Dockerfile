FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY auto_spider ./auto_spider
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --upgrade pip \
    && pip install ".[dev]"

COPY tests ./tests

CMD ["uvicorn", "auto_spider.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
