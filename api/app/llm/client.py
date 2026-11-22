"""Provider-neutral LLM interface. Pipeline stages depend only on this module."""

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMError(Exception):
    """A call failed. The message is safe to store and show (never contains credentials)."""

    def __init__(self, message: str, *, retryable: bool = False, systemic: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        # True when every further call would fail the same way (credentials, model access, rate limit, outage),
        # as opposed to a problem with this one response (token limit, malformed output).
        self.systemic = systemic


class LLMRefusal(LLMError):
    """The model declined the request (for example a safety classifier on malware content)."""


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    model: str
    usage: LLMUsage
    request_id: str | None = None
    fallback_from: str | None = None  # the requested model, when a fallback model served the response


class LLMClient(Protocol):
    provider: str

    def complete_json(
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
        """One request; returns `schema` validated from the model's structured output, or raises LLMError.

        `fallbacks`: let the provider retry a safety refusal on a fallback model, where it supports that.
        """
        ...
