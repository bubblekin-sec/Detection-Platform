import pytest


def convert(client, sigma_yaml: str):
    return client.post("/api/detection/convert", json={"sigma_yaml": sigma_yaml})


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_process_creation_rule_converts_to_device_process_events(client, sigma_fixture):
    response = convert(client, sigma_fixture("process_creation_rundll32.yml"))

    assert response.status_code == 200
    body = response.json()
    assert body["sigma_valid"] is True
    assert body["kql_error"] is None
    assert body["kql"].startswith("DeviceProcessEvents")
    assert "ProcessCommandLine" in body["kql"]
    assert "RunHTMLApplication" in body["kql"]


def test_registry_set_rule_converts_to_device_registry_events(client, sigma_fixture):
    response = convert(client, sigma_fixture("registry_set_run_key.yml"))

    assert response.status_code == 200
    body = response.json()
    assert body["sigma_valid"] is True
    assert body["kql"].startswith("DeviceRegistryEvents")
    assert "RegistryKey" in body["kql"]


def test_unsupported_logsource_is_valid_sigma_without_kql(client, sigma_fixture):
    response = convert(client, sigma_fixture("aws_cloudtrail.yml"))

    assert response.status_code == 200
    body = response.json()
    assert body["sigma_valid"] is True
    assert body["kql"] is None
    assert "table name" in body["kql_error"]


def test_rule_without_detection_is_rejected(client, sigma_fixture):
    response = convert(client, sigma_fixture("missing_detection.yml"))

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert any("detection" in error for error in errors)
    assert len(errors) == len(set(errors))


def test_malformed_yaml_is_rejected(client):
    response = convert(client, "title: [unclosed\n  detection: :")

    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0].startswith("Invalid YAML")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "pysigma-backend-kusto 1.0.1's XDR registry-key handling turns a trailing backslash plus the "
        "implied wildcard into `endswith ...Run*` with a literal asterisk, which never matches. "
        "Non-registry fields are unaffected. Remove this marker when an upgrade fixes it."
    ),
)
def test_contains_value_with_trailing_backslash_keeps_contains_semantics(client, sigma_fixture):
    kql = convert(client, sigma_fixture("registry_trailing_backslash.yml")).json()["kql"]

    assert kql is not None
    assert "contains" in kql
    assert "*" not in kql
