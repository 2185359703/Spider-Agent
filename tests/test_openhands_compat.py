"""Regression tests for the provider usage compatibility shim."""

from __future__ import annotations

import pytest

from auto_spider.ai.openhands_compat import install_provider_compat


@pytest.fixture
def clean_provider_compat(monkeypatch: pytest.MonkeyPatch):
    """Run each test with the LiteLLM wrapper in its original class state."""
    from litellm.types.utils import PromptTokensDetailsWrapper

    if "__getattr__" in PromptTokensDetailsWrapper.__dict__:
        monkeypatch.delattr(PromptTokensDetailsWrapper, "__getattr__")
    if "_auto_spider_compat" in PromptTokensDetailsWrapper.__dict__:
        monkeypatch.delattr(PromptTokensDetailsWrapper, "_auto_spider_compat")
    yield PromptTokensDetailsWrapper


def _usage(details):
    from litellm.types.utils import Usage

    return Usage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        prompt_tokens_details=details,
    )


def test_compat_handles_missing_cache_write_field(clean_provider_compat):
    from openhands.sdk.llm.utils.telemetry import normalize_usage

    details = clean_provider_compat(cached_tokens=7)
    with pytest.raises(AttributeError, match="cache_creation_tokens"):
        normalize_usage(_usage(details))

    install_provider_compat()
    snapshot = normalize_usage(_usage(details))

    assert snapshot is not None
    assert snapshot.cache_read_tokens == 7
    assert snapshot.cache_write_tokens == 0


def test_compat_preserves_reported_cache_write_tokens(clean_provider_compat):
    from openhands.sdk.llm.utils.telemetry import normalize_usage

    details = clean_provider_compat(cached_tokens=7, cache_creation_tokens=11)
    install_provider_compat()

    snapshot = normalize_usage(_usage(details))

    assert snapshot is not None
    assert snapshot.cache_read_tokens == 7
    assert snapshot.cache_write_tokens == 11


def test_compat_only_handles_the_two_cache_write_names(clean_provider_compat):
    install_provider_compat()
    details = clean_provider_compat(cached_tokens=7)

    assert details.cache_creation_tokens is None
    assert details.cache_write_tokens is None
    with pytest.raises(AttributeError, match="cache_creation_token_details"):
        _ = details.cache_creation_token_details
    with pytest.raises(AttributeError, match="unexpected_field"):
        _ = details.unexpected_field


def test_compat_install_is_idempotent(clean_provider_compat):
    install_provider_compat()
    first_getattr = clean_provider_compat.__dict__["__getattr__"]
    install_provider_compat()

    assert clean_provider_compat.__dict__["__getattr__"] is first_getattr
    assert clean_provider_compat.__dict__["_auto_spider_compat"] is True
