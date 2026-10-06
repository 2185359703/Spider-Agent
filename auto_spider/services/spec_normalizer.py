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


def _normalize_endpoint_aliases(
    normalized: dict[str, Any], corrections: list[dict[str, str]]
) -> None:
    """Move common agent aliases into the canonical PlatformSpec locations.

    The analysis tool receives a strict typed draft.  Models occasionally put
    pagination metadata on ``endpoints.list`` or use ``key_source`` on a
    detail endpoint while describing a ``{{source_id}}`` URL.  These are
    structural aliases, not new protocol facts, so they can be migrated
    deterministically before Pydantic validation.  Unknown fields remain
    rejected by the canonical models.
    """

    endpoints = normalized.get("endpoints")
    if not isinstance(endpoints, dict):
        return
    pagination = normalized.setdefault("pagination", {})
    if not isinstance(pagination, dict):
        return

    listing = endpoints.get("list")
    if isinstance(listing, dict):
        total_selector = listing.pop("total_count_selector", None)
        if total_selector is not None and pagination.get("total_count_selector") is None:
            pagination["total_count_selector"] = total_selector
            corrections.append(
                {
                    "code": "TOTAL_COUNT_SELECTOR_ALIAS",
                    "from": "endpoints.list.total_count_selector",
                    "to": "pagination.total_count_selector",
                    "value": "moved",
                }
            )
        next_selector = listing.pop("next_url_selector", None)
        if next_selector is not None and pagination.get("next_url_selector") is None:
            pagination["next_url_selector"] = next_selector
            if not pagination.get("mode"):
                pagination["mode"] = "next_url"
            corrections.append(
                {
                    "code": "NEXT_URL_SELECTOR_ALIAS",
                    "from": "endpoints.list.next_url_selector",
                    "to": "pagination.next_url_selector",
                    "value": "moved",
                }
            )

    detail = endpoints.get("detail")
    if isinstance(detail, dict) and "key_source" in detail:
        key_source = detail.pop("key_source")
        if key_source not in (None, "") and detail.get("decode") is None:
            # ``key_source=id`` is normally a model's label for the template
            # binding, not a response decoder. Preserve it as metadata under
            # the typed decode object without claiming that decoding occurred.
            detail["decode"] = {
                "mode": "none",
                "key_source": str(key_source),
                "confidence": "low",
                "inferred": True,
                "evidence_refs": list(detail.get("evidence_refs") or []),
            }
            corrections.append(
                {
                    "code": "DETAIL_KEY_SOURCE_ALIAS",
                    "from": "endpoints.detail.key_source",
                    "to": "endpoints.detail.decode.key_source",
                    "value": str(key_source),
                }
            )


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
    corrections: list[dict[str, str]] = []
    _normalize_endpoint_aliases(normalized, corrections)
    pagination = normalized.get("pagination")
    if not isinstance(pagination, dict):
        return normalized, corrections

    mode = str(pagination.get("mode") or "").strip().lower()
    if mode not in {"page", "offset"}:
        return normalized, []

    observed = _observed_pagination_keys(normalized, observation)
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
