from auto_spider.git.policy import validate_changed_files
from auto_spider.schemas import NextAction, ObservationCode
from auto_spider.services.policies import classify_observation, next_action_for


def test_policy_distinguishes_empty_jobs_from_missing_list() -> None:
    empty = classify_observation("https://example.com/jobs?fixture=empty")
    missing = classify_observation("https://example.com/jobs")
    assert empty["observation_code"] == ObservationCode.NO_JOBS_OBSERVED
    assert missing["observation_code"] == ObservationCode.NO_JOB_LIST_FOUND
    assert next_action_for(ObservationCode.NO_JOBS_OBSERVED, validation_passed=True) == (
        NextAction.CREATE_CANDIDATE
    )


def test_changed_file_policy_blocks_shared_files() -> None:
    invalid = validate_changed_files(
        [
            "collectors/example_company.py",
            "services/crawler_service.py",
            "config/platforms/example_company.toml",
        ],
        "example_company",
    )
    assert invalid == ["services/crawler_service.py"]
