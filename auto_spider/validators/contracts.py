from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from auto_spider.git.policy import validate_changed_files


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_changed_file_scope(platform_key: str, changed_files: list[str]) -> ValidationResult:
    invalid = validate_changed_files(changed_files, platform_key)
    return ValidationResult(
        passed=not invalid,
        errors=[f"CODE_SCOPE_VIOLATION: {path}" for path in invalid],
    )


def validate_nullable_business_ids(record: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    for field_name in ("platform_id", "entity_id"):
        value = record.get(field_name)
        if value == 0:
            errors.append(f"{field_name} 不能使用 0 作为占位值")
        if isinstance(value, str) and value.strip():
            errors.append(f"{field_name} 必须是整数或 NULL")
    return ValidationResult(passed=not errors, errors=errors)


def validate_report_counts(report: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    list_count = report.get("list_count")
    internship_count = report.get("internship_count")
    if list_count is not None and list_count < 0:
        errors.append("list_count 不能小于 0")
    if internship_count is not None and internship_count < 0:
        errors.append("internship_count 不能小于 0")
    if list_count is not None and internship_count is not None and internship_count > list_count:
        errors.append("internship_count 不能大于 list_count")
    return ValidationResult(passed=not errors, errors=errors)
