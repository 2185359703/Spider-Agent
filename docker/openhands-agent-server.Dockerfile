ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/auto_spider

RUN if command -v git >/dev/null 2>&1; then \
        :; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update \
        && apt-get install -y --no-install-recommends git gcc \
        && rm -rf /var/lib/apt/lists/*; \
    else \
        echo "git is required in the Agent Server image" >&2 \
        && exit 1; \
    fi

RUN pip install --upgrade pip \
    && pip install \
        "openhands-agent-server==1.49.6" \
        "openhands-sdk==1.49.6" \
        "openhands-tools==1.49.6"

CMD ["python", "-m", "openhands.agent_server", "--host", "0.0.0.0", "--port", "8000"]
