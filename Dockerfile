ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN if command -v git >/dev/null 2>&1 && command -v gcc >/dev/null 2>&1; then \
        :; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update \
        && apt-get install -y --no-install-recommends git gcc \
        && rm -rf /var/lib/apt/lists/*; \
    else \
        echo "git and gcc are required in the API/worker image" >&2 \
        && exit 1; \
    fi

COPY pyproject.toml README.md ./
COPY auto_spider ./auto_spider
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --upgrade pip \
    && pip install ".[dev]"

COPY tests ./tests

CMD ["uvicorn", "auto_spider.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
