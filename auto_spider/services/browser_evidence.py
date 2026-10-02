from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


SECRET_QUERY_KEYS = re.compile(
    r"(^|_)(token|signature|csrf|session|password|secret|auth)($|_)",
    re.IGNORECASE,
)
SECRET_HEADER_KEYS = re.compile(
    r"(cookie|authorization|csrf|token|session|password|secret)",
    re.IGNORECASE,
)
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(Authorization\s*:\s*(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?im)(^\s*Cookie\s*:\s*)[^\r\n]+"),
    re.compile(
        r"(?i)(['\"]?(?:[a-z0-9_]*(?:token|api_key|password|secret)|"
        r"authorization|csrf|session|cookie)['\"]?\s*[:=]\s*['\"])[^'\"]+"
    ),
    re.compile(r"(?i)([?&](?:token|signature|csrf|session|api_key|code)=)[^&\s\"']+"),
)


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, "[REDACTED]" if SECRET_QUERY_KEYS.search(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "[REDACTED]" if SECRET_HEADER_KEYS.search(key) else value
        for key, value in headers.items()
    }


def sanitize_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_TEXT_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


@dataclass(frozen=True)
class BrowserEvidenceResult:
    entry_url: str
    final_url: str
    title: str
    status_code: int | None
    request_count: int
    response_count: int
    manifest_path: str
    evidence_files: tuple[str, ...]


class BrowserEvidenceCollector:
    """Capture bounded page/network evidence for PlatformSpec generation."""

    def __init__(
        self,
        *,
        timeout_ms: int = 30_000,
        settle_ms: int = 1_500,
        max_body_bytes: int = 2_000_000,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self.max_body_bytes = max_body_bytes

    def capture(self, entry_url: str, output_dir: Path) -> BrowserEvidenceResult:
        if sync_playwright is None:
            raise RuntimeError("Playwright Python 包未安装")
        output_dir.mkdir(parents=True, exist_ok=True)
        requests: list[dict] = []
        responses: list[dict] = []
        response_objects: list[object] = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()

            def on_request(request) -> None:
                try:
                    post_data = request.post_data or ""
                except Exception:
                    post_data = "[BINARY_OR_UNAVAILABLE]"
                requests.append(
                    {
                        "method": request.method,
                        "url": sanitize_url(request.url),
                        "resource_type": request.resource_type,
                        "headers": sanitize_headers(request.headers),
                        "post_data": sanitize_text(post_data),
                    }
                )

            def on_response(response) -> None:
                responses.append(
                    {
                        "url": sanitize_url(response.url),
                        "status": response.status,
                        "headers": sanitize_headers(response.headers),
                        "content_type": response.headers.get("content-type", ""),
                    }
                )
                response_objects.append(response)

            page.on("request", on_request)
            page.on("response", on_response)
            status_code: int | None = None
            try:
                navigation = page.goto(
                    entry_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                status_code = navigation.status if navigation else None
                page.wait_for_timeout(self.settle_ms)
            except PlaywrightTimeoutError:
                status_code = None

            html = sanitize_text(page.content())
            (output_dir / "page.html").write_text(html, encoding="utf-8", newline="\n")
            page.screenshot(path=str(output_dir / "page.png"), full_page=True)
            final_url = sanitize_url(page.url)
            title = page.title()

            body_refs: list[dict] = []
            for index, response in enumerate(response_objects):
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type and "text" not in content_type:
                    continue
                try:
                    body = response.body()
                except Exception:
                    continue
                if len(body) > self.max_body_bytes:
                    continue
                safe = sanitize_text(body.decode("utf-8", errors="replace"))
                body_path = output_dir / f"response-{index}.txt"
                body_path.write_text(safe, encoding="utf-8", newline="\n")
                body_refs.append(
                    {
                        "index": index,
                        "path": body_path.name,
                        "sha256": hashlib.sha256(safe.encode("utf-8")).hexdigest(),
                        "size": len(safe.encode("utf-8")),
                    }
                )
            browser.close()

        (output_dir / "network.json").write_text(
            json.dumps(
                {
                    "entry_url": sanitize_url(entry_url),
                    "final_url": final_url,
                    "title": title,
                    "status_code": status_code,
                    "requests": requests,
                    "responses": responses,
                    "response_bodies": body_refs,
                    "redacted": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            newline="\n",
        )
        manifest = {
            "entry_url": sanitize_url(entry_url),
            "final_url": final_url,
            "title": title,
            "status_code": status_code,
            "request_count": len(requests),
            "response_count": len(responses),
            "redacted": True,
            "files": sorted(path.name for path in output_dir.iterdir()),
        }
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return BrowserEvidenceResult(
            entry_url=sanitize_url(entry_url),
            final_url=final_url,
            title=title,
            status_code=status_code,
            request_count=len(requests),
            response_count=len(responses),
            manifest_path=str(manifest_path),
            evidence_files=tuple(sorted(path.name for path in output_dir.iterdir())),
        )
