"""Runtime compatibility patches for provider response shapes.

OpenCode Go returns an OpenAI-compatible prompt-token details object that can
advertise a cache field without materializing the optional attribute. Some
OpenHands/LiteLLM versions access that optional field directly and abort an
otherwise successful conversation. Keep the patch narrow and return ``None``
only for the two optional cache-write names.
"""

from __future__ import annotations


def install_provider_compat() -> None:
    try:
        from litellm.types.utils import PromptTokensDetailsWrapper
    except ImportError:
        return
    if getattr(PromptTokensDetailsWrapper, "_auto_spider_compat", False):
        return
    original = PromptTokensDetailsWrapper.__getattr__

    def compat(self, name):
        if name in {"cache_creation_tokens", "cache_write_tokens"}:
            return None
        return original(self, name)

    PromptTokensDetailsWrapper.__getattr__ = compat
    PromptTokensDetailsWrapper._auto_spider_compat = True
