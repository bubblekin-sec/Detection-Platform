"""Regression tests from the increment 1 review: malformed or unsupported Sigma must never 500 or pass as valid."""

import pytest

RULE_HEAD = "title: t\nlogsource:\n  category: process_creation\n  product: windows\n"


def convert(client, sigma_yaml):
    return client.post("/api/detection/convert", json={"sigma_yaml": sigma_yaml})


def assert_sigma_rejected(response, *, mentions: str | None = None):
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert errors and all(isinstance(error, str) for error in errors)
    if mentions:
        assert any(mentions.lower() in error.lower() for error in errors), errors
    return errors


@pytest.mark.parametrize(
    "condition",
    ["selection and missing_sel", "selection |", "selection | count() > 5", "1"],
    ids=["undefined-identifier", "syntax-error", "legacy-aggregation", "non-string"],
)
def test_invalid_condition_is_rejected(client, condition):
    sigma_yaml = RULE_HEAD + f"detection:\n  selection:\n    CommandLine: x\n  condition: {condition}\n"
    assert_sigma_rejected(convert(client, sigma_yaml))


def test_unsupported_modifier_returns_kql_error_not_500(client):
    sigma_yaml = RULE_HEAD + "detection:\n  selection:\n    CommandLine|cased: Foo\n  condition: selection\n"

    response = convert(client, sigma_yaml)

    assert response.status_code == 200
    body = response.json()
    assert body["sigma_valid"] is True
    assert body["kql"] is None
    assert body["kql_error"]


@pytest.mark.parametrize("sigma_yaml", ["- a\n- b\n", "just a string\n"], ids=["list", "scalar"])
def test_non_mapping_document_is_rejected_with_accurate_message(client, sigma_yaml):
    errors = assert_sigma_rejected(convert(client, sigma_yaml), mentions="not a mapping")
    assert not any("YAML" in error for error in errors)


def test_non_mapping_detection_is_rejected_without_blaming_yaml(client):
    errors = assert_sigma_rejected(convert(client, RULE_HEAD + "detection: foo\n"))
    assert not any("Invalid YAML" in error for error in errors)


@pytest.mark.parametrize("sigma_yaml", ["", "---\n---\n"], ids=["empty", "empty-documents"])
def test_input_without_a_rule_is_rejected(client, sigma_yaml):
    assert_sigma_rejected(convert(client, sigma_yaml))


def test_two_rules_are_rejected(client, sigma_fixture):
    two = sigma_fixture("process_creation_rundll32.yml") + "\n---\n" + sigma_fixture("registry_set_run_key.yml")
    assert_sigma_rejected(convert(client, two), mentions="exactly one")


def with_second_document(sigma_fixture, second: str) -> str:
    return sigma_fixture("process_creation_rundll32.yml") + "\n---\n" + sigma_fixture(second)


def test_filter_document_is_applied_to_its_rule(client, sigma_fixture):
    response = convert(client, with_second_document(sigma_fixture, "filter_exclude_admin.yml"))

    assert response.status_code == 200
    kql = response.json()["kql"]
    assert "RunHTMLApplication" in kql
    assert "admin" in kql and "not" in kql.lower()


def test_correlation_document_is_rejected(client, sigma_fixture):
    response = convert(client, with_second_document(sigma_fixture, "correlation_rundll32_count.yml"))

    assert_sigma_rejected(response, mentions="not supported")


def test_duplicate_keys_are_rejected(client):
    sigma_yaml = RULE_HEAD + (
        "detection:\n  selection:\n    CommandLine: a\n  selection:\n    CommandLine: b\n  condition: selection\n"
    )
    assert_sigma_rejected(convert(client, sigma_yaml), mentions="duplicate")


def test_request_validation_errors_use_the_same_shape(client):
    response = client.post("/api/detection/convert", json={"sigma_yaml": 5})
    assert_sigma_rejected(response, mentions="sigma_yaml")


def test_oversized_input_is_rejected(client):
    response = convert(client, "a" * 70_000)
    assert_sigma_rejected(response, mentions="sigma_yaml")
