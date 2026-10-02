from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisResult:
    observation: dict[str, Any]
    list_endpoint: str | None
    detail_endpoint: str | None
    evidence_refs: list[str]
    list_method: str = "GET"
    list_query: dict[str, Any] | None = None
    list_body: dict[str, Any] | None = None
    list_response_file: str | None = None
    list_response_shape: str | None = None
    spec_draft: dict[str, Any] | None = None
