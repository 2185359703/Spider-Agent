from __future__ import annotations

from typing import Any

from auto_spider.schemas import NextAction, ObservationCode, TechnicalStatus


def classify_observation(url: str) -> dict[str, Any]:
    lowered = url.lower()
    if "fixture=jobs" in lowered:
        return {
            "observation_code": ObservationCode.INTERNSHIPS_FOUND,
            "list_found": True,
            "detail_found": True,
            "pagination_verified": True,
            "list_count": 3,
            "internship_count": 2,
            "valid_record_count": 2,
        }
    if "fixture=empty" in lowered:
        return {
            "observation_code": ObservationCode.NO_JOBS_OBSERVED,
            "list_found": True,
            "detail_found": True,
            "pagination_verified": True,
            "list_count": 0,
            "internship_count": 0,
            "valid_record_count": 0,
        }
    if "fixture=no-internship" in lowered:
        return {
            "observation_code": ObservationCode.NO_INTERNSHIPS_OBSERVED,
            "list_found": True,
            "detail_found": True,
            "pagination_verified": True,
            "list_count": 2,
            "internship_count": 0,
            "valid_record_count": 2,
        }
    if "fixture=blocked" in lowered:
        return {
            "observation_code": ObservationCode.ACCESS_RESTRICTED,
            "list_found": None,
            "detail_found": None,
            "pagination_verified": None,
            "list_count": None,
            "internship_count": None,
            "valid_record_count": 0,
        }
    return {
        "observation_code": ObservationCode.NO_JOB_LIST_FOUND,
        "list_found": False,
        "detail_found": False,
        "pagination_verified": False,
        "list_count": None,
        "internship_count": None,
        "valid_record_count": 0,
    }


def next_action_for(observation: ObservationCode, *, validation_passed: bool) -> NextAction:
    if (
        observation
        in {
            ObservationCode.INTERNSHIPS_FOUND,
            ObservationCode.NO_INTERNSHIPS_OBSERVED,
            ObservationCode.NO_JOBS_OBSERVED,
        }
        and validation_passed
    ):
        return NextAction.CREATE_CANDIDATE
    if observation in {ObservationCode.ACCESS_RESTRICTED, ObservationCode.INCONCLUSIVE}:
        return NextAction.REQUEST_INPUT
    return NextAction.CLOSE_WITH_REPORT


def technical_status_for(observation: ObservationCode) -> TechnicalStatus:
    if observation in {
        ObservationCode.INTERNSHIPS_FOUND,
        ObservationCode.NO_INTERNSHIPS_OBSERVED,
        ObservationCode.NO_JOBS_OBSERVED,
    }:
        return TechnicalStatus.PASS
    if observation == ObservationCode.ACCESS_RESTRICTED:
        return TechnicalStatus.PARTIAL
    return TechnicalStatus.FAIL
