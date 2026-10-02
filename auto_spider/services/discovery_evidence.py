"""Verify discovery references and lower confidence when selectors have no reproducible sample."""

import json
import re
from copy import deepcopy

from auto_spider.services.spec_builder import DISCOVERY_SECTIONS


def json_values(value, expression):
    """Small deterministic JSONPath subset; unsupported expressions are left unproven."""
    if not expression.startswith("$"):
        return []
    tokens = re.findall(
        r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+|\*)\]|\[['\"]([^'\"]+)['\"]\]", expression[1:]
    )
    rebuilt = "".join(
        f".{key}" if key else f"[{index}]" if index else f"['{quoted}']"
        for key, index, quoted in tokens
    )
    if rebuilt != expression[1:] and expression != "$":
        return []
    values = [value]
    for key, index, quoted in tokens:
        next_values = []
        for item in values:
            if key or quoted:
                if isinstance(item, dict) and (key or quoted) in item:
                    next_values.append(item[key or quoted])
            elif isinstance(item, list):
                if index == "*":
                    next_values.extend(item)
                elif int(index) < len(item):
                    next_values.append(item[int(index)])
        values = next_values
    return values


def body_samples(payload):
    if isinstance(payload.get("response"), (dict, list)):
        return [payload["response"]]
    texts = [payload.get("response"), payload.get("output")]
    samples = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for match in list(re.finditer(r"[\[{]", text))[:100]:
            try:
                value, _ = json.JSONDecoder().raw_decode(text[match.start() :])
                if isinstance(value, (dict, list)):
                    samples.append(value)
                    break
            except ValueError:
                continue
    return samples


def verify_discovery_draft(draft, refs, root):
    if not isinstance(draft, dict) or set(draft) - DISCOVERY_SECTIONS:
        raise ValueError("ANALYSIS_SPEC_SCOPE")
    draft = deepcopy(draft)
    for name in DISCOVERY_SECTIONS:
        if name not in draft:
            continue
        value = draft[name]
        if name in {"adapter", "pagination", "filters"} and not isinstance(value, dict):
            raise ValueError(f"ANALYSIS_SPEC_TYPE: {name} 必须是对象")
        if name == "endpoints":
            if not isinstance(value, dict):
                raise ValueError("ANALYSIS_SPEC_TYPE: endpoints 必须是对象")
            for endpoint_name in ("list", "detail"):
                endpoint = value.get(endpoint_name)
                if endpoint is not None and not isinstance(endpoint, dict):
                    raise ValueError(
                        f"ANALYSIS_SPEC_TYPE: endpoints.{endpoint_name} 必须是对象或 null"
                    )
                if isinstance(endpoint, dict) and endpoint.get("items_selector") is not None:
                    if not isinstance(endpoint["items_selector"], dict):
                        raise ValueError(
                            "ANALYSIS_SPEC_TYPE: "
                            f"endpoints.{endpoint_name}.items_selector 必须是对象或 null"
                        )
            if not isinstance(value.get("related", []), list):
                raise ValueError("ANALYSIS_SPEC_TYPE: endpoints.related 必须是数组")
            if any(not isinstance(item, dict) for item in value.get("related", [])):
                raise ValueError("ANALYSIS_SPEC_TYPE: endpoints.related 元素必须是对象")
        if name == "fields":
            if not isinstance(value, dict):
                raise ValueError("ANALYSIS_SPEC_TYPE: fields 必须是对象")
            for field_name, field in value.items():
                if not isinstance(field, dict):
                    raise ValueError(f"ANALYSIS_SPEC_TYPE: fields.{field_name} 必须是对象")
                if not isinstance(field.get("selectors", []), list):
                    raise ValueError(
                        f"ANALYSIS_SPEC_TYPE: fields.{field_name}.selectors 必须是数组"
                    )
                if any(not isinstance(item, dict) for item in field.get("selectors", [])):
                    raise ValueError(
                        f"ANALYSIS_SPEC_TYPE: fields.{field_name}.selectors 元素必须是对象"
                    )
        if name == "filters":
            for filter_name, filter_value in value.items():
                if not isinstance(filter_value, dict):
                    raise ValueError(f"ANALYSIS_SPEC_TYPE: filters.{filter_name} 必须是对象")
    known, captures = set(refs), {}
    for ref in refs:
        path = (root / ref).resolve()
        if path.suffix == ".json":
            try:
                captures[ref] = json.loads(path.read_text("utf-8"))
            except (ValueError, OSError):
                pass

    def walk(value):
        if isinstance(value, dict):
            evidence = value.get("evidence_refs", [])
            if not isinstance(evidence, list) or any(not isinstance(ref, str) for ref in evidence):
                raise ValueError("ANALYSIS_SPEC_TYPE: evidence_refs 必须是字符串数组")
            if any(ref not in known for ref in evidence):
                raise ValueError("ANALYSIS_SPEC_EVIDENCE_UNCONFIRMED")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(draft)
    for name in ("list", "detail"):
        endpoint = (draft.get("endpoints") or {}).get(name)
        if not endpoint:
            continue
        template = endpoint.get("url_template", "")
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{\\\{.*?\\\}\\\}", r"[^\\s/]+", pattern)
        observed = any(
            re.search(pattern, json.dumps(captures.get(ref, {}), ensure_ascii=False))
            for ref in endpoint.get("evidence_refs", [])
            if template
        )
        if not observed:
            endpoint["confidence"] = "low"
            endpoint["inferred"] = True
    items = ((draft.get("endpoints") or {}).get("list") or {}).get("items_selector") or {}
    for field in (draft.get("fields") or {}).values():
        for selector in field.get("selectors", []):
            if selector.get("confidence") != "high":
                continue
            proved = False
            if selector.get("kind") == "json_path":
                for ref in selector.get("evidence_refs", []):
                    for sample in body_samples(captures.get(ref, {})):
                        candidates = [sample]
                        if selector.get("source") == "list_item":
                            candidates = []
                            for found in json_values(sample, items.get("expression", "")):
                                candidates.extend(found if isinstance(found, list) else [found])
                        if any(
                            any(
                                v not in (None, "", [], {})
                                for v in json_values(item, selector.get("expression", ""))
                            )
                            for item in candidates[:5]
                        ):
                            proved = True
            if not proved:
                selector.update(
                    confidence="low",
                    inferred=True,
                    inference_reason="证据引用已核对，尚无可重放样本证明该提取表达式",
                )
    return draft
