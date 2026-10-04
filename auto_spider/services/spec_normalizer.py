"""Deterministic normalization for evidence-backed discovery drafts.

The discovery agent and the canonical PlatformSpec use different vocabularies
at times.  Small, evidence-backed spelling mistakes should be repaired locally
before Pydantic validation (and before asking an Agent Server to do more work).
This module deliberately does not infer a parameter from a page that was not
observed: it only copies a value when the request evidence proves the key.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

_PAGINATION_ALIASES = ("pagination_param", "offset_param", "page_field")


def _request_keys(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    return {str(key) for key in value if str(key).strip()}


def _observed_pagination_keys(
    draft: Mapping[str, Any], observation: Mapping[str, Any] | None
) -> set[str]:
    keys: set[str] = set()
    endpoints = draft.get("endpoints")
    if isinstance(endpoints, Mapping):
        listing = endpoints.get("list")
        if isinstance(listing, Mapping):
            keys.update(_request_keys(listing.get("query")))
            keys.update(_request_keys(listing.get("body")))
    if isinstance(observation, Mapping):
        keys.update(_request_keys(observation.get("list_query")))
        keys.update(_request_keys(observation.get("list_body")))
        hint = observation.get("pagination_param")
        if isinstance(hint, str) and hint.strip():
            keys.add(hint.strip())
    return keys


def normalize_spec_draft(
    draft: Mapping[str, Any], *, observation: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Normalize safe pagination aliases and return an audit list.

    ``cursor_param`` is only migrated for a page/offset draft when that exact
    key is present in the observed list request.  This handles the common
    agent mistake of putting ``offset`` in ``cursor_param`` without allowing
    an unobserved page parameter to be invented.
    """

    normalized = deepcopy(dict(draft))
    pagination = normalized.get("pagination")
    if not isinstance(pagination, dict):
        return normalized, []

    mode = str(pagination.get("mode") or "").strip().lower()
    if mode not in {"page", "offset"}:
        return normalized, []

    observed = _observed_pagination_keys(normalized, observation)
    corrections: list[dict[str, str]] = []

    def set_page_param(value: Any, source: str) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        value = value.strip()
        if value not in observed:
            return False
        pagination["page_param"] = value
        pagination.pop(source, None)
        if source != "page_param":
            corrections.append(
                {
                    "code": "PAGINATION_PARAM_ALIAS",
                    "from": source,
                    "to": "page_param",
                    "value": value,
                }
            )
        return True

    if not isinstance(pagination.get("page_param"), str) or not pagination.get("page_param"):
        cursor_alias = pagination.get("cursor_param")
        if (
            isinstance(cursor_alias, str)
            and cursor_alias.strip()
            and cursor_alias.strip() in observed
        ):
            pagination["page_param"] = cursor_alias.strip()
            pagination["cursor_param"] = None
            corrections.append(
                {
                    "code": "PAGINATION_CURSOR_ALIAS",
                    "from": "cursor_param",
                    "to": "page_param",
                    "value": cursor_alias.strip(),
                }
            )
        for alias in _PAGINATION_ALIASES:
            if pagination.get("page_param"):
                break
            if set_page_param(pagination.get(alias), alias):
                break
        if not pagination.get("page_param"):
            # The observed analyzer hint is already evidence-backed.  It is
            # added to ``observed`` above, so this remains deterministic.
            fallback = "offset" if mode == "offset" else "page"
            if fallback in observed:
                pagination["page_param"] = fallback
                corrections.append(
                    {
                        "code": "PAGINATION_PARAM_FROM_REQUEST",
                        "from": "request",
                        "to": "page_param",
                        "value": fallback,
                    }
                )

    # A frequent malformed draft uses cursor_param=offset for an offset API.
    # Migrate it only when the request body/query proves that exact key.
    if pagination.get("page_param") and isinstance(pagination.get("cursor_param"), str):
        cursor = pagination["cursor_param"].strip()
        if cursor and cursor == pagination["page_param"] and cursor in observed:
            pagination["cursor_param"] = None
            corrections.append(
                {
                    "code": "PAGINATION_CURSOR_ALIAS",
                    "from": "cursor_param",
                    "to": "null",
                    "value": cursor,
                }
            )

    return normalized, corrections


def normalize_analysis(
    analysis: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return a copy of an analysis envelope with a normalized ``spec_draft``."""

    normalized = deepcopy(dict(analysis))
    draft = normalized.get("spec_draft")
    if not isinstance(draft, Mapping):
        return normalized, []
    normalized_draft, corrections = normalize_spec_draft(
        draft, observation=normalized
    )
    normalized["spec_draft"] = normalized_draft
    return normalized, corrections
