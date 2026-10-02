from auto_spider.services.evidence import sanitize


def test_business_text_and_error_explanations_survive_redaction():
    data = {
        "title": "Session security intern",
        "description": "维护 cookie 生命周期与 token 验证逻辑",
        "error_msg": "字段 token_type 未提供",
        "headers": {"Cookie": "do-not-save"},
        "api_key": "do-not-save",
        "trace": "Authorization: Bearer do-not-save",
    }
    cleaned = sanitize(data)
    for key in ("title", "description", "error_msg"):
        assert cleaned[key] == data[key]
    assert "do-not-save" not in str(cleaned)
