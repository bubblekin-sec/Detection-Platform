"""Review: list rules, approve or reject, edit Sigma (revalidated, KQL regenerated), add a hand-written KQL."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Article, DetectionRule, PipelineRun, ProfileVersion, RuleQuery, Source
from app.pipeline.rule_generation import sigma_digest
from app.profiles.seed import seed_profiles

SIGMA = """title: Scheduled Task Created Pointing to Public Folder
id: {id}
status: experimental
references:
- https://r.example.com/lantern
date: '2026-11-21'
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    CommandLine|contains: schtasks /create
  condition: selection
level: high
"""


@pytest.fixture
def api(test_settings, db_session):
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def rule(db_session) -> DetectionRule:
    source = Source(slug="ex", name="Example", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    article = Article(source_id=source.id, url="https://r.example.com/lantern", title="Operation Paper Lantern",
                      content_text="x" * 600, content_hash="h-review", word_count=100)
    db_session.add(article)
    db_session.commit()
    seed_profiles(db_session)
    version = db_session.scalars(select(ProfileVersion)).one()
    run = PipelineRun(article_id=article.id, profile_version_id=version.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    rule_id = str(uuid.uuid4())
    sigma = SIGMA.format(id=rule_id)
    item = DetectionRule(
        id=rule_id, run_id=run.id, article_id=article.id, position=0,
        group_key={"platform": "windows", "telemetry_category": "process_creation"}, observable_ids=[],
        title="Scheduled Task Created Pointing to Public Folder", level="high", sigma_yaml=sigma,
        sigma_hash=sigma_digest(sigma), rationale="r", status="pending_review",
        queries=[RuleQuery(target="defender_xdr", origin="converted", query_text="DeviceProcessEvents | take 1",
                           status="ok", warnings=[], source_sigma_hash=sigma_digest(sigma))],
    )
    db_session.add(item)
    db_session.commit()
    return item


def test_list_pending_rules_with_their_article(api, rule):
    listed = api.get("/api/rules?status=pending_review").json()

    assert [item["id"] for item in listed] == [rule.id]
    assert listed[0]["article"] == {"id": rule.article_id, "title": "Operation Paper Lantern", "url": "https://r.example.com/lantern"}
    assert api.get("/api/rules?status=approved").json() == []


def test_list_rejects_an_unknown_status(api, rule):
    assert api.get("/api/rules?status=bogus").status_code == 422


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_approve_or_reject_with_a_note(api, rule, status):
    response = api.patch(f"/api/rules/{rule.id}", json={"status": status, "reviewer_note": "Checked against the article."})

    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["reviewer_note"]) == (status, "Checked against the article.")
    assert body["reviewed_at"] is not None


def test_editing_sigma_revalidates_regenerates_the_converted_query_and_marks_others_stale(api, rule, db_session):
    db_session.add(RuleQuery(rule_id=rule.id, target="defender_xdr", origin="llm", query_text="DeviceEvents | take 1",
                             status="ok", warnings=[], source_sigma_hash=rule.sigma_hash, llm_model="claude-opus-5-5"))
    db_session.commit()
    edited = SIGMA.format(id=rule.id).replace("schtasks /create", "schtasks.exe /create /ru SYSTEM")

    response = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": edited})

    body = response.json()
    assert response.status_code == 200
    assert "schtasks.exe /create /ru SYSTEM" in body["sigma_yaml"]
    by_origin = {query["origin"]: query for query in body["queries"]}
    assert by_origin["converted"]["stale"] is False
    assert "schtasks.exe /create /ru SYSTEM" in by_origin["converted"]["query_text"]
    assert by_origin["llm"]["stale"] is True
    assert len([query for query in body["queries"] if query["origin"] == "converted"]) == 1


def test_invalid_sigma_edits_are_rejected_and_nothing_changes(api, rule):
    before = api.get(f"/api/rules/{rule.id}").json()

    response = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": "title: x\ndetection:\n  sel:\n    a: b\n  condition: sel and nope\n"})

    assert response.status_code == 422
    assert any(error.startswith("sigma_yaml:") for error in response.json()["detail"]["errors"])
    assert api.get(f"/api/rules/{rule.id}").json() == before


def test_the_rule_id_cannot_be_changed_by_an_edit(api, rule):
    edited = SIGMA.format(id="11111111-1111-4111-8111-111111111111")

    body = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": edited}).json()

    assert f"id: {rule.id}" in body["sigma_yaml"]
    assert "11111111-1111-4111-8111-111111111111" not in body["sigma_yaml"]


def test_hand_written_kql_is_stored_as_a_human_edit_with_lint(api, rule):
    kql = 'DeviceRegistryEvents\n| where RegistryKey endswith "\\\\Run*"'

    body = api.patch(f"/api/rules/{rule.id}", json={"kql": kql}).json()
    again = api.patch(f"/api/rules/{rule.id}", json={"kql": kql + "\n| take 10"}).json()

    human = [query for query in body["queries"] if query["origin"] == "human_edit"]
    assert len(human) == 1 and human[0]["query_text"] == kql and human[0]["warnings"]
    assert [query["query_text"] for query in again["queries"] if query["origin"] == "human_edit"] == [kql + "\n| take 10"]


@pytest.mark.parametrize(
    "payload",
    [{"status": "maybe"}, {"kql": ""}, {"sigma_yaml": ""}, {"reviewer_note": "x" * 5001}, {"id": "nope"}, {}],
    ids=["bad-status", "empty-kql", "empty-sigma", "long-note", "unknown-field", "empty-body"],
)
def test_invalid_review_updates_are_422(api, rule, payload):
    assert api.patch(f"/api/rules/{rule.id}", json=payload).status_code == 422


def test_unknown_rule_is_404(api, rule):
    assert api.patch(f"/api/rules/{uuid.uuid4()}", json={"status": "approved"}).json() == {
        "detail": {"errors": ["Rule not found."]}
    }
    assert api.get(f"/api/rules/{uuid.uuid4()}").status_code == 404


def test_editing_an_approved_rule_sends_it_back_to_review(api, rule):
    api.patch(f"/api/rules/{rule.id}", json={"status": "approved"})
    edited = SIGMA.format(id=rule.id).replace("level: high", "level: medium")

    body = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": edited}).json()

    assert (body["status"], body["reviewed_at"], body["level"]) == ("pending_review", None, "medium")


def test_a_multi_document_edit_cannot_change_the_rule_identity(api, rule):
    before = api.get(f"/api/rules/{rule.id}").json()
    sneaky = (
        "action: global\nid: 11111111-1111-4111-8111-111111111111\n---\n"
        "title: x\nlogsource: {category: process_creation, product: windows}\n"
        "detection: {sel: {CommandLine|contains: a}, condition: sel}\n"
    )

    response = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": sneaky})

    assert response.status_code == 422
    assert api.get(f"/api/rules/{rule.id}").json() == before


def test_approving_content_that_changed_since_it_was_loaded_is_a_conflict(api, rule):
    loaded_hash = api.get(f"/api/rules/{rule.id}").json()["sigma_hash"]
    api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": SIGMA.format(id=rule.id).replace("level: high", "level: low")})

    response = api.patch(f"/api/rules/{rule.id}", json={"status": "approved", "expected_sigma_hash": loaded_hash})

    assert response.status_code == 409
    assert "changed" in response.json()["detail"]["errors"][0]
    assert api.get(f"/api/rules/{rule.id}").json()["status"] == "pending_review"


def test_editing_the_kql_of_an_approved_rule_sends_it_back_to_review(api, rule):
    api.patch(f"/api/rules/{rule.id}", json={"status": "approved"})

    body = api.patch(f"/api/rules/{rule.id}", json={"kql": "DeviceProcessEvents | take 5"}).json()

    assert (body["status"], body["reviewed_at"]) == ("pending_review", None)


def test_an_edit_with_a_status_keeps_that_status(api, rule):
    body = api.patch(
        f"/api/rules/{rule.id}",
        json={"sigma_yaml": SIGMA.format(id=rule.id).replace("level: high", "level: medium"), "status": "approved"},
    ).json()

    assert body["status"] == "approved" and body["reviewed_at"] is not None


def test_a_sigma_edit_keeps_the_text_as_written_and_the_article_reference(api, rule):
    with_comment = SIGMA.format(id=rule.id).replace("level: high", "level: high  # reviewer comment")
    kept = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": with_comment}).json()
    assert kept["sigma_yaml"] == with_comment

    no_refs = SIGMA.format(id=rule.id).replace("references:\n- https://r.example.com/lantern\n", "")
    restored = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": no_refs}).json()
    assert "https://r.example.com/lantern" in restored["sigma_yaml"]


def test_saving_unchanged_sigma_is_a_no_op(api, rule):
    api.patch(f"/api/rules/{rule.id}", json={"status": "approved"})
    current = api.get(f"/api/rules/{rule.id}").json()

    body = api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": current["sigma_yaml"]}).json()

    assert body["status"] == "approved"
    assert body["queries"] == current["queries"]


def test_back_to_pending_clears_reviewed_at_and_a_note_can_be_cleared(api, rule):
    api.patch(f"/api/rules/{rule.id}", json={"status": "rejected", "reviewer_note": "nope"})

    body = api.patch(f"/api/rules/{rule.id}", json={"status": "pending_review", "reviewer_note": None}).json()

    assert (body["status"], body["reviewed_at"], body["reviewer_note"]) == ("pending_review", None, None)


@pytest.mark.parametrize(
    "payload",
    [{"status": None}, {"sigma_yaml": None}, {"kql": None}, {"kql": "   "}, {"kql": "\U0001F600" * 16385}],
    ids=["null-status", "null-sigma", "null-kql", "blank-kql", "too-many-bytes"],
)
def test_more_invalid_updates_are_422_in_the_standard_shape(api, rule, payload):
    response = api.patch(f"/api/rules/{rule.id}", json=payload)

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert errors and all(isinstance(error, str) for error in errors)
