"""`cli sync-rules`: approved rules into a rules folder, deterministic, and never touching files it didn't write."""

import json
import uuid

import pytest
from sigma.collection import SigmaCollection
from sqlalchemy import select

from app.models import Article, DetectionRule, PipelineRun, ProfileVersion, RuleQuery, Source, StageResult
from app.pipeline.rule_generation import sigma_digest
from app.profiles.seed import seed_profiles
from app.rules.sync import MANIFEST, RulesDirMissing, sync_rules

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
def add_rule(db_session):
    source = Source(slug="ex", name="Example", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    article = Article(source_id=source.id, url=ARTICLE_URL, title="Operation Paper Lantern",
                      content_text="x" * 600, content_hash="h-sync", word_count=100)
    db_session.add(article)
    db_session.commit()
    seed_profiles(db_session)
    version = db_session.scalars(select(ProfileVersion).order_by(ProfileVersion.version.desc())).first()
    run = PipelineRun(article_id=article.id, profile_version_id=version.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    db_session.add(StageResult(run_id=run.id, stage="rule_generation", status="passed", output={}, model="claude-opus-5-5",
                               started_at=run.created_at, finished_at=run.created_at))
    db_session.commit()
    positions = iter(range(100))

    def add(status="approved", title="Scheduled Task Rule", sigma=None, queries=()):
        rule_id = str(uuid.uuid4())
        text = (sigma or SIGMA).format(id=rule_id, title=title, value="schtasks.exe")
        rule = DetectionRule(
            id=rule_id, run_id=run.id, article_id=article.id, position=next(positions), group_key={}, observable_ids=[],
            title=title, level="high", sigma_yaml=text, sigma_hash=sigma_digest(text), rationale="r", status=status,
        )
        for origin, status_, query_text, stale in queries:
            rule.queries.append(RuleQuery(
                target="defender_xdr", origin=origin, status=status_, query_text=query_text, warnings=[],
                source_sigma_hash="old" if stale else rule.sigma_hash,
            ))
        db_session.add(rule)
        db_session.commit()
        return rule

    return add


def files(root):
    return sorted(str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*") if path.is_file())


def test_an_approved_rule_becomes_sigma_kql_and_provenance_and_a_second_sync_writes_nothing(db_session, add_rule, tmp_path):
    rule = add_rule(queries=[("converted", "ok", "DeviceProcessEvents | where ProcessCommandLine has 'x'", False)])
    add_rule(status="pending_review", title="Pending")

    first = sync_rules(db_session, tmp_path)

    base = f"windows/process_creation/scheduled-task-rule-{rule.id[:8]}"
    assert files(tmp_path) == sorted([MANIFEST, f"{base}.kql", f"{base}.meta.json", f"{base}.yml"])
    assert first.written == [f"{base}.kql", f"{base}.meta.json", f"{base}.yml"]
    meta = json.loads((tmp_path / f"{base}.meta.json").read_text(encoding="utf-8"))
    assert meta["article"] == {"id": rule.article_id, "url": ARTICLE_URL, "title": "Operation Paper Lantern"}
    assert (meta["profile"]["slug"], meta["rules_model"], meta["kql"]["origin"]) == ("detection", "claude-opus-5-5", "converted")
    assert meta["kql"]["tables_read"] == ["DeviceProcessEvents"]
    collection = SigmaCollection.from_yaml((tmp_path / f"{base}.yml").read_text(encoding="utf-8"), collect_errors=True)
    assert collection.errors == [] and str(collection.rules[0].id) == rule.id
    assert all(b"\r" not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())

    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()}
    second = sync_rules(db_session, tmp_path)

    assert (second.written, second.removed, second.unchanged) == ([], [], 3)
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tmp_path.rglob("*") if path.is_file()} == before


def test_a_rule_no_longer_approved_loses_only_its_files_and_hand_added_files_survive(db_session, add_rule, tmp_path):
    keep, drop = add_rule(title="Keep"), add_rule(title="Drop")
    sync_rules(db_session, tmp_path)
    (tmp_path / "windows" / "process_creation" / "team-rule.yml").write_text("title: ours\n", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "README.md").write_text("hand written\n", encoding="utf-8")

    drop.status = "rejected"
    db_session.commit()
    result = sync_rules(db_session, tmp_path)

    assert result.removed == [f"windows/process_creation/drop-{drop.id[:8]}.meta.json", f"windows/process_creation/drop-{drop.id[:8]}.yml"]
    remaining = files(tmp_path)
    assert f"windows/process_creation/keep-{keep.id[:8]}.yml" in remaining
    assert "windows/process_creation/team-rule.yml" in remaining and "notes/README.md" in remaining


def test_a_hand_edited_managed_file_is_a_conflict_and_is_never_overwritten(db_session, add_rule, tmp_path):
    rule = add_rule()
    sync_rules(db_session, tmp_path)
    path = tmp_path / "windows" / "process_creation" / f"scheduled-task-rule-{rule.id[:8]}.yml"
    path.write_text(path.read_text(encoding="utf-8") + "# tuned by hand\n", encoding="utf-8", newline="\n")
    rule.sigma_yaml = rule.sigma_yaml.replace("schtasks.exe", "schtasks2.exe")
    rule.sigma_hash = sigma_digest(rule.sigma_yaml)
    db_session.commit()

    first = sync_rules(db_session, tmp_path)
    second = sync_rules(db_session, tmp_path)  # still detected on the next run

    assert len(first.conflicts) == 1 and "edited by hand" in first.conflicts[0]
    assert second.conflicts == first.conflicts
    assert path.read_text(encoding="utf-8").endswith("# tuned by hand\n")


def test_a_hand_added_file_in_a_rules_place_is_a_conflict(db_session, add_rule, tmp_path):
    rule = add_rule()
    target = tmp_path / "windows" / "process_creation" / f"scheduled-task-rule-{rule.id[:8]}.yml"
    target.parent.mkdir(parents=True)
    target.write_text("title: someone else's\n", encoding="utf-8")

    result = sync_rules(db_session, tmp_path)

    assert any("didn't write" in conflict for conflict in result.conflicts)
    assert target.read_text(encoding="utf-8") == "title: someone else's\n"


@pytest.mark.parametrize("title", ["../../etc/passwd", "Ünïcødé 🚨 Rule", "a" * 200, "(%) ~~"])
def test_any_title_gives_a_safe_path_inside_the_folder(db_session, add_rule, tmp_path, title):
    rule = add_rule(title=title)

    sync_rules(db_session, tmp_path)

    (sigma_path,) = [path for path in tmp_path.rglob("*.yml")]
    assert tmp_path in sigma_path.resolve().parents
    assert sigma_path.name.endswith(f"-{rule.id[:8]}.yml")
    assert all(character in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in sigma_path.name)


def test_the_deployable_query_is_the_current_one_preferring_a_hand_edit(db_session, add_rule, tmp_path):
    stale_edit = add_rule(title="Stale Edit", queries=[
        ("converted", "ok", "DeviceProcessEvents | take 1", False),
        ("human_edit", "ok", "DeviceProcessEvents | take 2", True),  # written for an older Sigma
    ])
    edited = add_rule(title="Edited", queries=[
        ("converted", "ok", "DeviceProcessEvents | take 1", False),
        ("llm", "ok", "DeviceProcessEvents | take 3", False),
        ("human_edit", "ok", "DeviceProcessEvents | take 4", False),
    ])
    none = add_rule(title="None", queries=[("converted", "failed", None, False)])

    sync_rules(db_session, tmp_path)

    folder = tmp_path / "windows" / "process_creation"
    assert (folder / f"stale-edit-{stale_edit.id[:8]}.kql").read_text(encoding="utf-8") == "DeviceProcessEvents | take 1\n"
    assert (folder / f"edited-{edited.id[:8]}.kql").read_text(encoding="utf-8") == "DeviceProcessEvents | take 4\n"
    assert not (folder / f"none-{none.id[:8]}.kql").exists()
    meta = json.loads((folder / f"none-{none.id[:8]}.meta.json").read_text(encoding="utf-8"))
    assert meta["kql"] is None and "No current Defender XDR query" in meta["kql_missing_reason"]


def test_an_invalid_rule_is_reported_and_not_written(db_session, add_rule, tmp_path):
    broken = add_rule(sigma=SIGMA.replace("condition: selection", "condition: selection and missing"))

    result = sync_rules(db_session, tmp_path)

    assert result.left_out == [broken.id]
    assert files(tmp_path) == [MANIFEST]


def test_a_missing_folder_is_refused(db_session, tmp_path):
    with pytest.raises(RulesDirMissing):
        sync_rules(db_session, tmp_path / "typo")
    assert not (tmp_path / "typo").exists()


def test_the_cli_exits_1_on_a_conflict_and_2_on_a_missing_folder(db_session, add_rule, tmp_path, monkeypatch, test_settings):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_settings.database_url)
    rule = add_rule()
    assert cli.main(["sync-rules", "--dir", str(tmp_path)]) == 0
    path = tmp_path / "windows" / "process_creation" / f"scheduled-task-rule-{rule.id[:8]}.yml"
    path.write_text("edited\n", encoding="utf-8")
    rule.title = "Renamed"  # the rule moves; its old, edited file stays and is reported
    db_session.commit()

    assert cli.main(["sync-rules", "--dir", str(tmp_path)]) == 1
    assert cli.main(["sync-rules", "--dir", str(tmp_path / "missing")]) == 2


@pytest.mark.parametrize("manifest", ['<<<<<<< HEAD\n{"files": {}}\n', '{"files": {"../outside.yml": "0"}}', '{"nope": 1}'])
def test_an_untrustworthy_manifest_stops_the_sync_before_anything_is_written(db_session, add_rule, tmp_path, manifest):
    add_rule()
    (tmp_path / MANIFEST).write_text(manifest, encoding="utf-8")

    with pytest.raises(RulesDirMissing):
        sync_rules(db_session, tmp_path)

    assert files(tmp_path) == [MANIFEST]


def test_a_conflict_on_one_of_a_rules_files_leaves_all_its_files_alone(db_session, add_rule, tmp_path):
    rule = add_rule(queries=[("converted", "ok", "DeviceProcessEvents | take 1", False)])
    sync_rules(db_session, tmp_path)
    folder = tmp_path / "windows" / "process_creation"
    base = f"scheduled-task-rule-{rule.id[:8]}"
    (folder / f"{base}.yml").write_text("# hand edit\n", encoding="utf-8", newline="\n")
    before = {name: (folder / name).read_bytes() for name in (f"{base}.kql", f"{base}.meta.json")}
    rule.queries[0].query_text = "DeviceProcessEvents | take 2"
    rule.reviewer_note = "changed"
    db_session.commit()

    result = sync_rules(db_session, tmp_path)

    assert len(result.conflicts) == 1 and result.written == []
    assert {name: (folder / name).read_bytes() for name in before} == before


def test_kql_line_endings_and_review_times_are_normalised(db_session, add_rule, tmp_path):
    from datetime import datetime, timedelta, timezone

    rule = add_rule(queries=[("human_edit", "ok", "DeviceProcessEvents\r\n| take 1\r\n", False)])
    rule.reviewed_at = datetime(2026, 11, 22, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    db_session.commit()

    sync_rules(db_session, tmp_path)

    base = tmp_path / "windows" / "process_creation" / f"scheduled-task-rule-{rule.id[:8]}"
    assert base.with_suffix(".kql").read_bytes() == b"DeviceProcessEvents\n| take 1\n"
    meta = json.loads(base.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["reviewed_at"] == "2026-11-22T12:00:00+00:00"
    assert not list(tmp_path.rglob("*.tmp"))  # no temp files left behind


def test_reviewer_notes_can_be_left_out(db_session, add_rule, tmp_path):
    rule = add_rule()
    rule.reviewer_note = "internal only"
    db_session.commit()

    sync_rules(db_session, tmp_path, include_notes=False)

    (meta_path,) = tmp_path.rglob("*.meta.json")
    assert json.loads(meta_path.read_text(encoding="utf-8"))["reviewer_note"] is None
