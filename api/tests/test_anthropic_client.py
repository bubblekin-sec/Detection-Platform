"""AnthropicLLMClient against canned Messages API responses (no network, no key)."""

import json

import httpx2
import pytest

from app.llm.anthropic_client import AnthropicLLMClient
from app.llm.client import LLMError, LLMRefusal
from app.profiles.schema import RelevanceVerdict

VALID = json.dumps({"score": 7, "rationale": "Concrete persistence detail.", "signals": ["schtasks"]})


def message(text: str | None, stop_reason: str = "end_turn", **extra) -> dict:
    content = [{"type": "text", "text": text}] if text is not None else []
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 900, "output_tokens": 80, "cache_read_input_tokens": 0},
        **extra,
    }


def client_returning(status: int, body: dict, seen: list | None = None) -> AnthropicLLMClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(status, json=body, headers={"request-id": "req_123"})

    return AnthropicLLMClient(
        api_key="test-key-not-real",
        timeout_seconds=5,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def call(client: AnthropicLLMClient):
    return client.complete_json(model="claude-haiku-5-5", system="sys", user="usr", schema=RelevanceVerdict, effort="low")


def test_valid_structured_output_is_parsed_with_usage():
    seen: list = []

    result = call(client_returning(200, message(VALID), seen))

    assert result.output == RelevanceVerdict(score=7, rationale="Concrete persistence detail.", signals=["schtasks"])
    assert (result.usage.input_tokens, result.usage.output_tokens, result.request_id) == (900, 80, "req_123")
    sent = seen[0]
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["messages"] == [{"role": "user", "content": "usr"}]


def test_refusal_with_partial_json_is_a_refusal_not_a_crash():
    body = message('{"score": 3, "ratio', stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})

    with pytest.raises(LLMRefusal, match="cyber"):
        call(client_returning(200, body))


def test_truncated_output_is_a_readable_error():
    with pytest.raises(LLMError, match="token limit"):
        call(client_returning(200, message('{"score": 3, "rat', stop_reason="max_tokens")))


def test_output_that_does_not_match_the_schema_is_a_readable_error():
    with pytest.raises(LLMError, match="does not match"):
        call(client_returning(200, message('{"score": "high"}')))


def test_authentication_failure_points_at_the_key_setting_without_echoing_it():
    body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}

    with pytest.raises(LLMError) as raised:
        call(client_returning(401, body))

    assert "ANTHROPIC_API_KEY" in str(raised.value)
    assert "test-key-not-real" not in str(raised.value)


def test_fallbacks_use_the_beta_endpoint_with_default_routing():
    seen_bodies: list = []
    seen_headers: list = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_bodies.append(json.loads(request.content))
        seen_headers.append(request.headers.get("anthropic-beta", ""))
        return httpx2.Response(200, json=message(VALID), headers={"request-id": "req_9"})

    client = AnthropicLLMClient(
        api_key="test-key-not-real", timeout_seconds=5, max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )

    result = client.complete_json(
        model="claude-opus-5-5", system="sys", user="usr", schema=RelevanceVerdict, effort="medium", fallbacks=True
    )

    assert result.output.score == 7
    assert seen_bodies[0]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen_headers[0]


def test_without_fallbacks_no_beta_header_is_sent():
    seen_headers: list = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_headers.append(request.headers.get("anthropic-beta"))
        return httpx2.Response(200, json=message(VALID))

    client = AnthropicLLMClient(
        api_key="test-key-not-real", timeout_seconds=5, max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    call(client)

    assert seen_headers == [None]


def test_api_error_messages_keep_their_reason():
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long: 1200000 tokens > 1000000"}}

    with pytest.raises(LLMError, match="400.*prompt is too long"):
        call(client_returning(400, body))


def test_after_a_fallback_the_final_models_text_is_used():
    declined_then_served = message(None)
    declined_then_served["content"] = [
        {"type": "text", "text": '{"score": 2, "ratio'},
        {
            "type": "fallback",
            "from": {"model": "claude-opus-5-5"},
            "to": {"model": "claude-opus-4-8"},
            "trigger": {"type": "refusal", "category": "cyber"},
        },
        {"type": "text", "text": VALID},
    ]

    client = client_returning(200, declined_then_served)
    result = client.complete_json(
        model="claude-opus-5-5", system="sys", user="usr", schema=RelevanceVerdict, fallbacks=True
    )

    assert result.output.score == 7
    assert (result.model, result.fallback_from) == ("claude-opus-4-8", "claude-opus-5-5")


def test_the_generated_extraction_schema_is_accepted_by_the_sdk_transform():
    import anthropic
    import yaml

    from app.profiles.schema import ProfileDefinition, extraction_schema

    definition = ProfileDefinition.model_validate(
        yaml.safe_load(open("app/profiles/seed_data/detection.yaml", encoding="utf-8"))["definition"]
    )
    schema = anthropic.transform_schema(extraction_schema(definition))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for name, item in schema["$defs"].items():
        assert item["additionalProperties"] is False, name
        assert set(item["required"]) == set(item["properties"]), name
        assert item["properties"]["platform"]["enum"] == ["windows", "linux", "macos", "unknown"]
