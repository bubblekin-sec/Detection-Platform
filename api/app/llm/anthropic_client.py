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
    ) -> LLMResult[T]:
        output_config: dict = {"format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}}
        if effort:
            output_config["effort"] = effort
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
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
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise LLMError(f"No text in the response (stop_reason={response.stop_reason}).")
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as error:
            raise LLMError(f"The model returned output that does not match the schema ({error.error_count()} errors).") from error

        usage = response.usage
        return LLMResult(
            output=parsed,
            model=response.model,
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
