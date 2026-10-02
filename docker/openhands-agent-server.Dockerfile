ARG BASE_IMAGE=python:3.12-slim
ARG BROWSER_RUNTIME_IMAGE=auto-spider-runtime-base:playwright-1.63
FROM ${BROWSER_RUNTIME_IMAGE} AS browser-node
# Reuse the existing runtime's official Playwright Node driver; no new Node image is required.
RUN python -c "import os,pathlib,playwright; os.symlink(str(pathlib.Path(playwright.__file__).parent/'driver'/'node'), '/usr/local/bin/node')"
RUN python -c "import io,tarfile,urllib.request; data=urllib.request.urlopen('https://registry.npmjs.org/npm/-/npm-10.8.2.tgz').read(); tarfile.open(fileobj=io.BytesIO(data),mode='r:gz').extractall('/opt/npm',filter='data')"
RUN node /opt/npm/package/bin/npm-cli.js install --global --prefix /opt/browser-cli @playwright/cli@0.1.22

FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/opt/auto-spider

WORKDIR /srv/auto_spider
COPY --from=browser-node /usr/local/lib/python3.12/site-packages/playwright/driver/node /usr/local/bin/node
COPY --from=browser-node /opt/browser-cli/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/@playwright/cli/playwright-cli.js /usr/local/bin/playwright-cli
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN node /usr/local/lib/node_modules/@playwright/cli/node_modules/playwright/cli.js install --with-deps chromium

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

COPY auto_spider/__init__.py /opt/auto-spider/auto_spider/__init__.py
COPY auto_spider/schemas.py /opt/auto-spider/auto_spider/schemas.py
COPY auto_spider/ai/__init__.py auto_spider/ai/collector_tools.py auto_spider/ai/workspace_access.py auto_spider/ai/browser_cli.py auto_spider/ai/browser_server_routes.py /opt/auto-spider/auto_spider/ai/
COPY auto_spider/services/__init__.py auto_spider/services/browser_evidence.py auto_spider/services/validation_activity.py /opt/auto-spider/auto_spider/services/
COPY auto_spider/services/spec_patch.py /opt/auto-spider/auto_spider/services/
COPY auto_spider/services/analysis_submission.py /opt/auto-spider/auto_spider/services/

CMD ["python", "-m", "openhands.agent_server", "--host", "0.0.0.0", "--port", "8000", "--import-modules", "auto_spider.ai.browser_server_routes"]
