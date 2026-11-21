"""Anthropic API provider (official SDK), with JSON-schema structured outputs.

Uses `messages.create` with `output_config.format` rather than `messages.parse`: `parse` validates the JSON while
building the response, so a refusal or truncation that leaves partial JSON would raise a pydantic error before
`stop_reason` could be checked. Here `stop_reason` is checked first, then the text is validated.
"""

import anthropic
import httpx2
from pydantic import BaseModel, ValidationError

from app.llm.client import Effort, LLMError, LLMRefusal, LLMResult, LLMUsage

MAX_ERROR_DETAIL = 300
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLMClient:
    provider = "anthropic"

    def __init__(
        self, *, api_key: str, timeout_seconds: float, max_retries: int = 2, http_client: httpx2.Client | None = None
    ) -> None:
        # The SDK retries connection errors, 408/409/429 and 5xx with backoff (max_retries).
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries, http_client=http_client
        )

    def complete_json[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        effort: Effort | None = None,
        max_tokens: int = 16000,
        fallbacks: bool = False,
    ) -> LLMResult[T]:
        output_config: dict = {"format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}}
        if effort:
            output_config["effort"] = effort
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        try:
            if fallbacks:
                # Threat content can trip safety classifiers; on a decline the API re-runs the request on a
                # fallback model chosen by refusal category ("default"), inside the same call.
                response = self._client.beta.messages.create(**request, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                response = self._client.messages.create(**request)
        except anthropic.AuthenticationError as error:
            raise LLMError("Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY in api/.env.") from error
        except anthropic.PermissionDeniedError as error:
            raise LLMError(f"The API key is not allowed to use {model} (403): {_detail(error)}") from error
        except anthropic.NotFoundError as error:
            raise LLMError(f"Model {model} was not found (404). Check the model setting.") from error
        except anthropic.RateLimitError as error:
            raise LLMError("Rate limited by the Anthropic API (429) after retries.", retryable=True) from error
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as error:
            raise LLMError(f"Could not reach the Anthropic API: {type(error).__name__}.", retryable=True) from error
        except anthropic.APIStatusError as error:
            retryable = error.status_code >= 500
            raise LLMError(f"Anthropic API error {error.status_code}: {_detail(error)}", retryable=retryable) from error
        except anthropic.APIError as error:  # e.g. APIResponseValidationError
            raise LLMError(f"Unexpected response from the Anthropic API ({type(error).__name__}).") from error

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise LLMRefusal(f"The model declined this request (category: {category or 'unspecified'}).")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"The response hit the {max_tokens}-token limit before finishing.")
        # After a fallback, content holds the declined model's partial output, a fallback block, then the
        # served model's output: only text after the last fallback block is the answer.
        blocks = list(response.content)
        fallback_indexes = [index for index, block in enumerate(blocks) if block.type == "fallback"]
        answer_blocks = blocks[fallback_indexes[-1] + 1 :] if fallback_indexes else blocks
        text = next((block.text for block in answer_blocks if block.type == "text"), None)
        served_model, fallback_from = response.model, None
        if fallback_indexes:
            last = blocks[fallback_indexes[-1]]
            served_model, fallback_from = _model_of(last, "to"), _model_of(blocks[fallback_indexes[0]], "from")
        if text is None:
            raise LLMError(f"No text in the response (stop_reason={response.stop_reason}).")
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as error:
            raise LLMError(f"The model returned output that does not match the schema ({error.error_count()} errors).") from error

        usage = response.usage
        return LLMResult(
            output=parsed,
            model=served_model,
            fallback_from=fallback_from,
            usage=LLMUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
            ),
            request_id=response._request_id,
        )


def _detail(error: anthropic.APIStatusError) -> str:
    """The API's own error message (never contains the key), shortened for storage."""
    body = error.body if isinstance(error.body, dict) else {}
    message = (body.get("error") or {}).get("message") or error.message or ""
    return message[:MAX_ERROR_DETAIL]


def _model_of(block, side: str) -> str | None:
    """Model id from a fallback block's `from`/`to` (typed on the beta endpoint, a dict if parsed loosely)."""
    info = getattr(block, "from_" if side == "from" else side, None)
    if info is None and isinstance(block, dict):
        info = block.get(side)
    return getattr(info, "model", None) or (info.get("model") if isinstance(info, dict) else None)
