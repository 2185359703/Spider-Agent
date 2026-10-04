import pytest
from sqlalchemy import select

from auto_spider.db.models import OnboardingBatch, OnboardingTask
from auto_spider.services.company_import import preview_company_import, submit_company_import


def test_preview_handles_chinese_csv_quotes_duplicates_and_errors(db_session):
    text = (
        '\ufeff公司名称,招聘链接\n"公司,甲",https://app.mokahr.com/jobs/a\n'
        "公司乙\thttps://app.mokahr.com/jobs/b\n"
        "公司丙 https://app.mokahr.com/jobs/a\n缺链接\n"
    )
    result = preview_company_import(db_session, text)
    assert result["total"] == 4 and result["valid"] == 2
    assert result["duplicates"] == result["invalid"] == 1
    assert result["rows"][0]["company_name"] == "公司,甲"
    assert result["rows"][0]["platform_key"] != result["rows"][1]["platform_key"]


def test_large_import_is_one_batch_idempotent_and_keeps_ids_null(db_session):
    text = "\n".join(f"公司{index}\thttps://example.com/careers/{index}" for index in range(150))
    first = submit_company_import(db_session, text, "large-import-0001", "operator")
    second = submit_company_import(db_session, text, "large-import-0001", "operator")
    assert first == second and first["accepted_count"] == 150
    assert db_session.query(OnboardingBatch).count() == 1
    tasks = list(db_session.scalars(select(OnboardingTask)))
    assert all(t.platform_id is None and t.entity_id is None for t in tasks)
    assert len({t.platform_key for t in tasks}) == 150
    assert len({t.batch_id for t in tasks}) == 1
    assert [t.browser_lane for t in tasks[:4]] == [0, 1, 0, 1]
    assert preview_company_import(db_session, text)["duplicates"] == 150
    with pytest.raises(ValueError, match="不一致"):
        submit_company_import(
            db_session, text + "\n新增 https://new.example.com", "large-import-0001", "operator"
        )


def test_invalid_only_batch_has_no_queued_tasks(db_session):
    result = submit_company_import(db_session, "公司甲 无链接", "invalid-import-0001", "operator")
    assert result["accepted_count"] == 0 and result["rejected_count"] == 1
    assert db_session.query(OnboardingTask).count() == 0


def test_nine_company_user_matrix_preserves_spa_routes_and_query_filters(db_session):
    from pathlib import Path

    text = (Path(__file__).parent / "fixtures/company_matrix.txt").read_text("utf-8")
    result = preview_company_import(db_session, text)
    assert result["valid"] == 9 and result["invalid"] == result["duplicates"] == 0
    original = {line.split("\t", 1)[0]: line.split("\t", 1)[1] for line in text.splitlines()}
    assert {row["company_name"]: row["entry_url"] for row in result["rows"]} == original
