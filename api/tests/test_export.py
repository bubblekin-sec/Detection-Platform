"""YAML export: approved rules only, as multi-document Sigma that re-validates, with the rules' own ids."""

import uuid

import pytest
import yaml
from fastapi.testclient import TestClient
from sigma.collection import SigmaCollection
from sqlalchemy import select

from app.main import create_app
from app.models import Article, DetectionRule, PipelineRun, ProfileVersion, Source
from app.pipeline.rule_generation import sigma_digest
from app.profiles.seed import seed_profiles

ARTICLE_URL = "https://r.example.com/lantern"
SIGMA = """title: {title}
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
    CommandLine|contains: {value}
  condition: selection
level: high
"""


@pytest.fixture
def api(test_settings, db_session):
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def add_rule(db_session):
    source = Source(slug="ex", name="Example", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    article = Article(source_id=source.id, url=ARTICLE_URL, title="Operation Paper Lantern",
                      content_text="x" * 600, content_hash="h-export", word_count=100)
    db_session.add(article)
    db_session.commit()
    seed_profiles(db_session)
    version = db_session.scalars(select(ProfileVersion)).one()
    run = PipelineRun(article_id=article.id, profile_version_id=version.id, status="succeeded")
    db_session.add(run)
    db_session.commit()
    positions = iter(range(100))

    def add(status: str, title: str = "Rule", sigma: str | None = None, position: int | None = None) -> DetectionRule:
        rule_id = str(uuid.uuid4())
        text = (sigma or SIGMA).format(id=rule_id, title=title, value=f"{title.lower().replace(' ', '-')}.exe")
        rule = DetectionRule(
            id=rule_id, run_id=run.id, article_id=article.id, position=next(positions) if position is None else position,
            group_key={"platform": "windows", "telemetry_category": "process_creation"}, observable_ids=[],
            title=title, level="high", sigma_yaml=text, sigma_hash=sigma_digest(text), rationale="r", status=status,
        )
        db_session.add(rule)
        db_session.commit()
        return rule

    return add


def export(api, **params):
    response = api.get("/api/rules/export", params=params)
    assert response.status_code == 200, response.text
    return response


def test_only_approved_rules_are_exported_and_the_file_revalidates(api, add_rule):
    first = add_rule("approved", "First")
    add_rule("pending_review", "Pending")
    add_rule("rejected", "Rejected")
    second = add_rule("approved", "Second")

    response = export(api)

    assert response.headers["content-type"].startswith("application/yaml")
    assert 'attachment; filename="approved-rules.yaml"' == response.headers["content-disposition"]
    collection = SigmaCollection.from_yaml(response.text, collect_errors=True)
    assert collection.errors == []
    assert [str(rule.id) for rule in collection.rules] == [first.id, second.id]
    assert all(rule.references == [ARTICLE_URL] for rule in collection.rules)


def test_ids_stay_stable_across_exports_and_edits(api, add_rule):
    rule = add_rule("approved", "First")
    before = export(api).text

    assert export(api).text == before  # the same rules give the same bytes
    edited = rule.sigma_yaml.replace("first.exe", "first-renamed.exe")
    assert api.patch(f"/api/rules/{rule.id}", json={"sigma_yaml": edited, "status": "approved"}).status_code == 200

    after = export(api).text
    assert after != before
    (document,) = [doc for doc in yaml.safe_load_all(after) if doc]
    assert document["id"] == rule.id and "first-renamed.exe" in after


def test_a_reviewers_own_text_is_exported_as_written_unless_it_has_document_markers(api, add_rule):
    commented = SIGMA.replace("level: high", "level: high  # tuned for the lab")
    marked = "---\n" + SIGMA + "...\n"
    add_rule("approved", "Commented", commented)
    add_rule("approved", "Marked", marked)

    text = export(api).text

    assert "# tuned for the lab" in text
    collection = SigmaCollection.from_yaml(text, collect_errors=True)
    assert collection.errors == [] and [rule.title for rule in collection.rules] == ["Commented", "Marked"]


def test_with_no_approved_rules_the_export_is_empty_but_valid(api, add_rule):
    add_rule("pending_review")

    text = export(api).text

    assert [doc for doc in yaml.safe_load_all(text) if doc] == []
    assert "0 approved rules" in text


def test_a_stored_rule_that_no_longer_validates_is_left_out_and_named(api, add_rule):
    good = add_rule("approved", "Good")
    broken = add_rule("approved", "Broken", SIGMA.replace("condition: selection", "condition: selection and missing"))

    text = export(api).text

    assert [str(rule.id) for rule in SigmaCollection.from_yaml(text).rules] == [good.id]
    assert f"# Left out (no longer validates): {broken.id}" in text


@pytest.mark.parametrize("status", ["pending_review", "rejected", "anything"])
def test_only_approved_rules_can_be_exported(api, status):
    response = api.get("/api/rules/export", params={"status": status})

    assert response.status_code == 422


GLOBAL_TEMPLATE = "action: global\nfalsepositives:\n- injected\n---\n"


@pytest.mark.parametrize("line_break", ["\n", "\r"])
def test_a_stored_multi_document_rule_is_left_out_without_touching_the_others(api, add_rule, line_break):
    # pySigma merges an `action: global` template into the next document, so this parses as one rule.
    templated = add_rule("approved", "Templated", (GLOBAL_TEMPLATE + SIGMA).replace("\n", line_break))
    after = add_rule("approved", "After")

    text = export(api).text

    collection = SigmaCollection.from_yaml(text, collect_errors=True)
    assert collection.errors == [] and [str(rule.id) for rule in collection.rules] == [after.id]
    assert collection.rules[0].falsepositives == []
    assert f"# Left out (no longer validates): {templated.id}" in text


def test_indented_bom_and_bare_carriage_return_texts_still_give_a_valid_file(api, add_rule):
    indented = add_rule("approved", "Indented", "".join(f"  {line}\n" for line in SIGMA.splitlines()))
    bom = add_rule("approved", "Bom", "﻿" + SIGMA)
    carriage = add_rule("approved", "Carriage", SIGMA.replace("\n", "\r"))
    commented = add_rule("approved", "Commented", SIGMA.replace("level: high", "level: high  # kept"))

    text = export(api).text

    collection = SigmaCollection.from_yaml(text, collect_errors=True)
    assert collection.errors == []
    assert [str(rule.id) for rule in collection.rules] == [indented.id, bom.id, carriage.id, commented.id]
    assert [rule.title for rule in collection.rules] == ["Indented", "Bom", "Carriage", "Commented"]
    assert "﻿" not in text and "\r" not in text and "# kept" in text


def test_rules_are_exported_in_run_order_not_insertion_order(api, add_rule, db_session):
    later = add_rule("approved", "Later", position=5)
    earlier = add_rule("approved", "Earlier", position=1)
    earlier.created_at = later.created_at  # one run's rules are created in one transaction
    db_session.commit()

    ids = [str(rule.id) for rule in SigmaCollection.from_yaml(export(api).text).rules]

    assert ids == [earlier.id, later.id]


def test_a_rule_that_lost_its_article_reference_is_left_out(api, add_rule):
    orphan = add_rule("approved", "Orphan", SIGMA.replace("- https://r.example.com/lantern", "- https://elsewhere.example"))

    text = export(api).text

    assert SigmaCollection.from_yaml(text).rules == []
    assert orphan.id in text


@pytest.mark.parametrize(
    "text",
    [
        "---\n" + SIGMA + "...\n",
        ("---\n" + SIGMA + "...\n").replace("\n", "\r"),  # markers the parser sees whatever the line break
        "%YAML 1.1\n---\n" + SIGMA,
    ],
)
def test_a_rule_carrying_its_own_markers_is_one_document_in_the_file(api, add_rule, text):
    first = add_rule("approved", "First")
    marked = add_rule("approved", "Marked", text)

    documents = list(yaml.load_all(export(api).text, Loader=yaml.BaseLoader))

    assert [document["id"] for document in documents] == [first.id, marked.id]  # no empty documents in between
