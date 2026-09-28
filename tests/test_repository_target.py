from pathlib import Path

from auto_spider.git.target import CollectorRepository


def test_collector_target_uses_committed_baseline_without_dirty_files() -> None:
    target = CollectorRepository(
        Path(r"C:\Users\ASUS\Desktop\jichu-v5-sync"),
        "d1f3c72ec5e10041f32914d465464808f5c18d9a",
    )
    context = target.candidate_context()
    assert context["is_git_repository"] is True
    assert context["baseline_available"] is True
    assert context["dirty_state_excluded"] is True
    assert "collectors/public_html_career.py" in context["dirty_files"]
    assert "tests/test_platforms.py" in context["dirty_files"]
