"""The only place an LLM client is constructed. The enterprise provider switch happens here."""

from pydantic import BaseModel

from app.config import Settings
from app.llm.client import Effort, LLMClient, LLMError, LLMResult, LLMUsage


class LLMNotConfigured(Exception):
    """Startup error with an actionable, secret-free message."""


class OfflineLLMClient:
    """LLM_PROVIDER=offline: lets the UI and worker run end to end without credentials or cost.

    It never calls a model; every stage fails with an explanatory message so nothing looks like real output.
    """

    provider = "offline"

    def complete_json[T: BaseModel](
        self, *, model: str, system: str, user: str, schema: type[T], effort: Effort | None = None, max_tokens: int = 16000
    ) -> LLMResult[T]:
        raise LLMError("LLM_PROVIDER is 'offline', so no model was called. Add ANTHROPIC_API_KEY to api/.env.")


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "offline":
        return OfflineLLMClient()
    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None or not settings.anthropic_api_key.get_secret_value().strip():
            raise LLMNotConfigured(
                "ANTHROPIC_API_KEY is not set. Add it to api/.env (never commit it), "
                "or set LLM_PROVIDER=offline to run without a model."
            )
        from app.llm.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(
            api_key=settings.anthropic_api_key.get_secret_value(), timeout_seconds=settings.llm_timeout_seconds
        )
    raise LLMNotConfigured(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Use 'anthropic' or 'offline'.")


__all__ = ["LLMNotConfigured", "LLMUsage", "build_llm_client"]
