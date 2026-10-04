"""Bounded, evidence-preserving correction of discovery output before code generation."""

import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.config import get_settings
from auto_spider.schemas import (
    AdapterSpec,
    EndpointsSpec,
    FieldsSpec,
    FiltersSpec,
    PaginationSpec,
)
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.discovery_evidence import verify_discovery_draft
from auto_spider.services.spec_normalizer import normalize_spec_draft


class DiscoverySpecInvalid(ValueError):
    def __init__(self, errors, attempts):
        self.details = {"spec_validation_errors": errors, "spec_repair_attempts": attempts}
        super().__init__(
            "DISCOVERY_SPEC_INVALID: 接入规范经过有限纠错仍未通过，证据已保存；"
            + json.dumps(self.details, ensure_ascii=False)
        )


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    spec_draft: dict


def validation_feedback(error):
    if isinstance(error, ValidationError):
        prefix = {
            "PaginationSpec": ["pagination"],
            "EndpointsSpec": ["endpoints"],
            "FieldsSpec": ["fields"],
            "AdapterSpec": ["adapter"],
        }.get(error.title, [])
        return [
            {
                "loc": prefix + list(item["loc"]),
                "type": item["type"],
                "msg": sanitize_text(item["msg"]),
            }
            for item in error.errors(include_context=False, include_input=False)
        ]
    return [
        {
            "loc": ["spec_draft"],
            "type": type(error).__name__,
            "msg": sanitize_text(str(error))[:3000],
        }
    ]


def apply_correction(analysis, response):
    if not isinstance(response, str):
        raise ValueError("SPEC_CORRECTION_JSON_REQUIRED")
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            raise ValueError("SPEC_CORRECTION_JSON_FENCE_INVALID")
        text = "\n".join(lines[1:-1])
    # Keep the repair contract strict, but accept a model's structured reason as
    # a printable audit note instead of spending another repair attempt on it.
    raw = json.loads(text)
    if isinstance(raw, dict) and not isinstance(raw.get("reason"), str):
        raw["reason"] = json.dumps(raw.get("reason"), ensure_ascii=False)
    proposal = Correction.model_validate(raw)
    if not proposal.reason.strip():
        raise ValueError("SPEC_CORRECTION_REASON_REQUIRED")
    sections = {
        "adapter": AdapterSpec,
        "endpoints": EndpointsSpec,
        "fields": FieldsSpec,
        "pagination": PaginationSpec,
        "filters": FiltersSpec,
    }
    if set(proposal.spec_draft) - sections.keys():
        raise ValueError("ANALYSIS_SPEC_SCOPE")

    def normalize_aliases(value):
        if isinstance(value, dict):
            value = {key: normalize_aliases(child) for key, child in value.items()}
            if "url" in value and "url_template" not in value:
                value["url_template"] = value.pop("url")
            # Older agent templates used these descriptive fields. They do not
            # carry a verified contract value, so discard them explicitly.
            for key in ("entry", "job_url", "response_path"):
                value.pop(key, None)
            return value
        if isinstance(value, list):
            return [normalize_aliases(child) for child in value]
        return value

    proposal.spec_draft = normalize_aliases(proposal.spec_draft)
    # Handle evidence-backed pagination aliases locally before validating the
    # nested models.  A correction agent should not be spent on
    # ``cursor_param=offset`` when the captured request already proves the
    # canonical ``page_param=offset`` value.
    proposal.spec_draft, _ = normalize_spec_draft(
        proposal.spec_draft, observation=analysis
    )
    for name, value in proposal.spec_draft.items():
        proposal.spec_draft[name] = sections[name].model_validate(value).model_dump(mode="json")
    # Observation, input URLs and permission envelope cannot be rewritten by this phase.
    corrected = deepcopy(analysis)
    corrected["spec_draft"] = verify_discovery_draft(
        proposal.spec_draft, analysis["evidence_refs"], get_settings().evidence_root.resolve()
    )
    return corrected


class DiscoverySpecRepairGateway:
    def repair(
        self,
        *,
        analysis,
        errors,
        previous_response,
        attempt,
        state,
        execution,
        task_id,
        run_id,
        event_callback,
    ):
        settings = get_settings()
        workspace = settings.worktree_root / task_id / run_id / "discovery-spec-repair"
        workspace.mkdir(parents=True, exist_ok=True)
        prompt = (
            "修正招聘接入分析草稿的结构。只读取现有证据，不重新访问网站、不修改文件。"
            "原始观察结论、任务标识和权限边界不能改变。只返回 JSON：reason 必须是字符串，"
            "spec_draft 必须包含完整 adapter/endpoints/fields/pagination/filters。"
            "Endpoint 必须使用 url_template（不是 url），只能使用 schema 中的字段；"
            "不要输出 entry、job_url、response_path 等额外键。"
            "证据引用必须来自输入 evidence_refs；无法证明的字段保留空 selectors 和 low。"
            "page/offset 模式使用 page_param，cursor 模式才使用 cursor_param。"
            "response_format 只能 json/html/text；分页 termination 只能使用枚举值。\n"
            + json.dumps(
                {
                    "errors": errors,
                    "current_spec_draft": analysis.get("spec_draft", {}),
                    "evidence_refs": analysis.get("evidence_refs", []),
                    "previous_response_tail": (previous_response or "")[-3000:],
                    "schema_summary": {
                        "response_format": ["json", "html", "text"],
                        "selector_source": [
                            "response_body",
                            "list_item",
                            "detail",
                            "related",
                            "url",
                            "response_header",
                        ],
                        "pagination_mode": ["none", "page", "offset", "cursor", "next_url"],
                        "termination": [
                            "empty_page",
                            "next_missing",
                            "repeated_page_fingerprint",
                            "total_reached",
                            "max_pages",
                        ],
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return (
            OpenHandsGateway()
            .run(
                prompt,
                workspace,
                review_only=True,
                browser_enabled=False,
                execution=execution,
                task_id=task_id,
                step_key=f"discovery_spec_repair:{attempt}",
                event_callback=event_callback,
                spec={
                    "identity": {"entry_url": state["entry_url"]},
                    "generation": {"allowed_files": []},
                    "evidence": {"manifest_ref": state["manifest_ref"]},
                },
            )
            .final_response
        )
