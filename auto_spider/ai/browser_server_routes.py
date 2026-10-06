"""Authenticated resource reclamation routes on the existing Agent Server."""

import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from openhands.agent_server.api import api
from openhands.agent_server.dependencies import check_session_api_key

# The Agent Server is a separate Python process from the API/worker.  Import
# the tool module here so its ToolDefinition classes register before any
# remote conversation resolves CollectorReadTool/CollectorBrowserTool.
from auto_spider.ai import collector_tools as _collector_tools  # noqa: F401
from auto_spider.ai.browser_cli import BrowserCLI, runtime_root, validate_id
from auto_spider.ai.openhands_compat import install_provider_compat
from auto_spider.ai.workspace_access import load_policy

install_provider_compat()

router = APIRouter(prefix="/api/collector-browser", dependencies=[Depends(check_session_api_key)])


@router.post("/{execution_id}/close")
def close_browser(execution_id: str):
    try:
        return BrowserCLI(execution_id, load_policy(execution_id)).release()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cleanup/idle")
def reclaim_idle():
    outcomes = []
    root = runtime_root()
    if not root.is_dir():
        return {"released": []}
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        try:
            validate_id(folder.name)
            browser = BrowserCLI(folder.name)
            receipt = browser.receipt()
            if (
                not receipt.get("busy")
                and not receipt.get("owner")
                and time.time() - receipt.get("touched", time.time()) > 1800
            ):
                outcomes.append({"execution_id": folder.name, **browser.close()})
        except (OSError, ValueError, RuntimeError):
            continue
    return {"released": outcomes}


@router.post("/batches/{resource_id}/close")
def close_batch(resource_id: str):
    browser = BrowserCLI(resource_id)
    receipt = browser.receipt()
    if receipt.get("owner") or receipt.get("busy"):
        return {"released": False, "reason": "company_active"}
    return browser.close()


def janitor():
    while True:
        time.sleep(30)
        reclaim_idle()


api.include_router(router)
threading.Thread(target=janitor, name="collector-browser-reaper", daemon=True).start()
