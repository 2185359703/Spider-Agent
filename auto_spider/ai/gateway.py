"""Safe, switchable LLM gateway profiles.

Secrets stay in environment variables. Runtime selection is persisted only as a
profile name; every AgentExecution stores a non-secret configuration snapshot.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from auto_spider.config import Settings, get_settings
from auto_spider.db.models import RuntimeSetting

ACTIVE_KEY = "ai_gateway_profile"
PROFILE_NAMES = ("default", "opencode_zen", "custom")


@dataclass(frozen=True)
class GatewayConfig:
    profile: str
    provider: str
    model: str
    base_url: str | None
    api_mode: str
    api_key: str | None

    def snapshot(self) -> dict[str, str | None]:
        return {
            "profile": self.profile,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_mode": self.api_mode,
        }


def _selected_profile(settings: Settings, *, session=None, factory=None) -> str:
    if factory is not None:
        with factory() as owned:
            return _selected_profile(settings, session=owned)
    if session is not None:
        row = session.get(RuntimeSetting, ACTIVE_KEY)
        if row and isinstance(row.value_json, dict) and row.value_json.get("profile"):
            return str(row.value_json["profile"])
    return settings.ai_gateway_profile


def resolve_gateway_config(
    settings: Settings | None = None,
    *,
    session=None,
    factory=None,
    snapshot=None,
    profile_override: str | None = None,
):
    settings = settings or get_settings()
    if snapshot:
        profile = str(snapshot.get("profile") or "default")
    else:
        profile = profile_override or _selected_profile(settings, session=session, factory=factory)
    if profile not in PROFILE_NAMES:
        raise ValueError(f"未知 AI 网关 profile: {profile}")
    if profile == "opencode_zen":
        return GatewayConfig(
            profile=profile,
            provider="opencode_zen",
            model=str((snapshot or {}).get("model") or settings.opencode_model),
            base_url=(snapshot or {}).get("base_url") or settings.opencode_base_url,
            api_mode=str((snapshot or {}).get("api_mode") or settings.opencode_api_mode),
            api_key=settings.opencode_api_key,
        )
    return GatewayConfig(
        profile=profile,
        provider="custom" if profile == "custom" else "openhands_default",
        model=str((snapshot or {}).get("model") or settings.openhands_llm_model),
        base_url=(snapshot or {}).get("base_url") or settings.openhands_llm_base_url,
        api_mode=str((snapshot or {}).get("api_mode") or settings.openhands_llm_api_mode),
        api_key=settings.openhands_llm_api_key,
    )


def public_gateway(config: GatewayConfig) -> dict[str, Any]:
    base_url = config.base_url
    if base_url:
        parsed = urlsplit(base_url)
        base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    return {
        **config.snapshot(),
        "provider": config.provider,
        "base_url": base_url,
        "configured": bool(
            config.model and (config.api_key or config.provider == "openhands_default")
        ),
        "credential_source": "environment",
    }


def available_gateways(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    return [
        public_gateway(resolve_gateway_config(settings, profile_override=profile_override))
        for profile_override in PROFILE_NAMES
    ]
