from __future__ import annotations

from typing import Any
from uuid import uuid4

from auto_spider.config import get_settings
from auto_spider.schemas import (
    AdapterFamily,
    AdapterSpec,
    Confidence,
    DecodeMode,
    DecodeSpec,
    EndpointSpec,
    EndpointsSpec,
    FieldSpec,
    FieldsSpec,
    GenerationSpec,
    IdentitySpec,
    InternshipFilterSpec,
    PaginationMode,
    PaginationSpec,
    PlatformSpec,
    SelectorKind,
    SelectorSource,
    SelectorSpec,
    SpecStatus,
)


def build_platform_spec(
    *,
    task_id: str,
    platform_key: str,
    source_name: str,
    entry_url: str,
    observation: dict[str, Any],
    evidence_id: str,
) -> PlatformSpec:
    found = observation["list_found"] is True
    detail_found = observation["detail_found"] is True
    evidence_refs = [evidence_id] if found else []
    selector_confidence = Confidence.HIGH if found else Confidence.LOW
    observed_list_url = observation.get("list_endpoint")
    list_method = str(observation.get("list_method") or "GET").upper()
    list_body = (
        observation.get("list_body") if isinstance(observation.get("list_body"), dict) else {}
    )
    list_query = (
        observation.get("list_query")
        if isinstance(observation.get("list_query"), dict)
        else {}
    )
    list_url = str(observed_list_url or f"{entry_url}#list")
    list_confidence = Confidence.HIGH if observed_list_url else selector_confidence
    items_expression = str(observation.get("items_selector") or "$.data.items")
    list_decode = None
    if observation.get("list_response_shape") == "encrypted_data_field":
        list_decode = DecodeSpec(
            mode=DecodeMode.OPAQUE,
            input_selector=SelectorSpec(
                source=SelectorSource.RESPONSE_BODY,
                kind=SelectorKind.JSON_PATH,
                expression="$.data",
                confidence=Confidence.MEDIUM,
                inferred=True,
                inference_reason="浏览器证据显示 data 字段为加密字符串",
                evidence_refs=evidence_refs,
            ),
            helper_required=False,
            evidence_refs=evidence_refs,
            confidence=Confidence.MEDIUM,
            inferred=True,
        )
    list_endpoint = (
        EndpointSpec(
            method=list_method if list_method in {"GET", "POST", "PUT", "PATCH"} else "GET",
            url_template=list_url,
            query=list_query,
            body=list_body,
            response_format="json",
            items_selector=SelectorSpec(
                source=SelectorSource.RESPONSE_BODY,
                kind=SelectorKind.JSON_PATH,
                expression=items_expression,
                multiple=True,
                transforms=["to_list"],
                confidence=list_confidence,
                evidence_refs=evidence_refs,
            ),
            decode=list_decode,
            evidence_refs=evidence_refs,
            confidence=list_confidence,
        )
        if found
        else None
    )
    detail_endpoint = (
        EndpointSpec(
            method="GET",
            url_template=f"{entry_url}#detail/{{source_id}}",
            response_format="json",
            evidence_refs=evidence_refs,
            confidence=selector_confidence,
        )
        if detail_found
        else None
    )

    def field_selector(
        expression: str,
        source: SelectorSource = SelectorSource.DETAIL,
        *,
        confidence: Confidence | None = None,
        inferred: bool = False,
        inference_reason: str | None = None,
    ) -> SelectorSpec:
        return SelectorSpec(
            source=source,
            kind=SelectorKind.JSON_PATH,
            expression=expression,
            transforms=["to_text", "normalize_text"],
            confidence=confidence or selector_confidence,
            inferred=inferred,
            inference_reason=inference_reason,
            evidence_refs=evidence_refs,
        )

    field_source = SelectorSource.DETAIL if detail_found else SelectorSource.LIST_ITEM
    inferred_field = not detail_found
    inferred_reason = "列表响应存在岗位字段，但尚未观察到独立详情请求" if inferred_field else None

    fields = FieldsSpec(
        source_id=FieldSpec(
            required=True,
            selectors=[
                SelectorSpec(
                    source=SelectorSource.LIST_ITEM,
                    kind=SelectorKind.JSON_PATH,
                    expression="$.id",
                    transforms=["to_string", "strip"],
                    confidence=selector_confidence,
                    evidence_refs=evidence_refs,
                )
            ]
            if found
            else [],
        ),
        title=FieldSpec(
            required=True,
            selectors=[
                field_selector(
                    "$.title",
                    field_source,
                    confidence=Confidence.MEDIUM if inferred_field else selector_confidence,
                    inferred=inferred_field,
                    inference_reason=inferred_reason,
                )
            ],
        ),
        source_url=FieldSpec(
            required=True,
            selectors=[
                field_selector(
                    "$.url",
                    field_source,
                    confidence=Confidence.MEDIUM if inferred_field else selector_confidence,
                    inferred=inferred_field,
                    inference_reason=inferred_reason,
                )
            ],
        ),
        location=FieldSpec(selectors=[field_selector("$.location", field_source)]),
        description=FieldSpec(selectors=[field_selector("$.description", field_source)]),
        requirements=FieldSpec(
            selectors=[field_selector("$.requirements", field_source)]
        ),
        publish_time=FieldSpec(selectors=[field_selector("$.publishedAt", field_source)]),
    )
    observed_pagination = str(observation.get("pagination_mode") or "")
    pagination_mode = (
        PaginationMode.OFFSET
        if observed_pagination == "offset"
        else PaginationMode.PAGE
        if found
        else PaginationMode.NONE
    )
    page_param = str(observation.get("pagination_param") or ("page" if found else "")) or None
    size_param = str(observation.get("size_param") or ("limit" if found else "")) or None
    pagination = PaginationSpec(
        mode=pagination_mode,
        page_param=page_param,
        size_param=size_param,
        page_start=0 if pagination_mode == PaginationMode.OFFSET else 1,
        page_size=int((list_body or {}).get(size_param or "limit", 50) or 50),
        max_pages=50,
        termination=["empty_page", "repeated_page_fingerprint", "max_pages"] if found else [],
        evidence_refs=evidence_refs,
        confidence=selector_confidence,
    )
    filters = InternshipFilterSpec(
        evidence_refs=evidence_refs,
        confidence=selector_confidence,
        inferred=not found,
        inference_reason="未发现列表证据" if not found else None,
    )
    generation = GenerationSpec(
        baseline_ref=get_settings().aicoding_baseline_ref,
        allowed_files=[
            f"collectors/{platform_key}.py",
            f"config/platforms/{platform_key}.toml",
            f"tests/test_{platform_key}.py",
            f"tests/fixtures/{platform_key}/*",
        ],
        test_commands=[
            f"python -m compileall collectors/{platform_key}.py",
            f"pytest tests/test_{platform_key}.py",
            (
                "ruff check --isolated --select E,F,I,UP,B --target-version py312 "
                "--line-length 100 "
                f"collectors/{platform_key}.py tests/test_{platform_key}.py"
            ),
        ],
        simulated=False,
    )
    spec = PlatformSpec(
        spec_id=f"spec-{uuid4().hex}",
        spec_revision=1,
        task_id=task_id,
        platform_key=platform_key,
        source_name=source_name,
        identity=IdentitySpec(
            entry_url=entry_url,
            normalized_url=entry_url,
            platform_id=None,
            entity_id=None,
        ),
        adapter=AdapterSpec(
            family=AdapterFamily.CUSTOM_HTTP,
            collector_template="custom_http",
        ),
        endpoints=EndpointsSpec(list=list_endpoint, detail=detail_endpoint),
        pagination=pagination,
        fields=fields,
        filters={"internship": filters},
        generation=generation,
        status=SpecStatus.DRAFT,
        confidence_summary={},
    )
    return spec.model_copy(update={"confidence_summary": spec.confidence_counts()})
