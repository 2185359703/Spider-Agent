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

RUN pip install --no-cache-dir "uv==0.11.16"

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --extra dev --no-emit-project --format requirements.txt > /tmp/requirements.txt \
    && uv pip install --system --requirement /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

RUN set -eu; \
    for attempt in 1 2 3; do \
        if python -m playwright install --with-deps chromium; then \
            exit 0; \
        fi; \
        echo "Playwright dependency installation failed (attempt ${attempt}/3), retrying" >&2; \
        sleep $((attempt * 3)); \
    done; \
    exit 1

COPY auto_spider ./auto_spider
COPY agent_skills ./agent_skills
COPY README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests

CMD ["uvicorn", "auto_spider.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
