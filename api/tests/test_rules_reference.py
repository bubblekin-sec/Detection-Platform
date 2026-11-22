"""Reference rulesets: loading untrusted Sigma folders safely and finding the closest rule."""

import os

import pytest

from app.detection.dedup import fingerprint
from app.rules.reference import MAX_FILE_BYTES, best_match, load_reference_rules

RULE = """title: {title}
id: {id}
logsource:
  category: process_creation
  product: windows
detection:
  selection:
    CommandLine|contains:
{values}
  condition: selection
"""


def rule(title: str, rule_id: str, values: list[str]) -> str:
    return RULE.format(title=title, id=rule_id, values="\n".join(f"      - '{value}'" for value in values))


@pytest.fixture
def ruleset(tmp_path):
    root = tmp_path / "team-rules"
    (root / "windows").mkdir(parents=True)
    (root / "windows" / "close.yml").write_text(rule("Team Close", "team-1", ["a", "b", "c", "d", "e"]), encoding="utf-8")
    (root / "windows" / "far.yml").write_text(rule("Team Far", "team-2", ["x", "y", "z"]), encoding="utf-8")
    (root / "windows" / "own.yml").write_text(rule("Own Export", "own-id", ["a", "b", "c", "d", "e"]), encoding="utf-8")
    multi = "action: global\ntitle: shared\n---\n" + rule("Multi Second", "team-3", ["q", "r", "s"])
    (root / "multi.yml").write_text(multi, encoding="utf-8")
    (root / "notes.txt").write_text("title: not a rule", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "hidden.yml").write_text(rule("Hidden", "git-1", ["a", "b", "c", "d", "e"]), encoding="utf-8")
    (root / "bomb.yml").write_text("a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n", encoding="utf-8")
    (root / "huge.yml").write_text("title: x\n" + "#" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    (root / "broken.yml").write_text("title: [unclosed\n", encoding="utf-8")
    return root


def test_rules_are_loaded_and_unsafe_files_are_skipped_with_a_reason(ruleset):
    loaded = load_reference_rules([ruleset])

    assert sorted(reference.rule_id for reference in loaded.rules) == ["own-id", "team-1", "team-2", "team-3"]
    reasons = dict(loaded.skipped)
    assert reasons["team-rules/bomb.yml"] == "uses YAML aliases"
    assert reasons["team-rules/huge.yml"] == "larger than 64 KB"
    assert reasons["team-rules/broken.yml"].startswith("unreadable")
    assert not any(".git" in path or "notes.txt" in path for path, _ in loaded.skipped)


def test_the_closest_reference_rule_is_found_but_never_the_rule_itself(ruleset):
    loaded = load_reference_rules([ruleset])
    own = fingerprint(rule("Own Export", "own-id", ["a", "b", "c", "d", "e"]))

    match = best_match(own, "own-id", loaded)

    assert (match.path, match.rule_id, match.title, match.similarity) == ("team-rules/windows/close.yml", "team-1", "Team Close", 1.0)
    assert best_match(fingerprint(rule("New", "n", ["m", "n", "o"])), "n", loaded) is None


def test_a_symlink_out_of_the_folder_is_skipped(tmp_path):
    outside = tmp_path / "outside.yml"
    outside.write_text(rule("Outside", "out-1", ["a", "b", "c"]), encoding="utf-8")
    root = tmp_path / "ruleset"
    root.mkdir()
    try:
        os.symlink(outside, root / "link.yml")
    except OSError:
        pytest.skip("this account can't create symlinks (Windows without developer mode)")

    loaded = load_reference_rules([root])

    assert loaded.rules == []
    assert dict(loaded.skipped)["ruleset/link.yml"] == "points outside the folder"


def test_a_missing_reference_folder_is_reported_not_fatal(tmp_path):
    loaded = load_reference_rules([tmp_path / "nope"])

    assert loaded.rules == [] and loaded.skipped == [(str(tmp_path / "nope"), "not a folder")]


def test_the_cli_reports_matches_as_json(db_session, ruleset, monkeypatch, test_settings, capsys):
    import json
    import uuid

    from app import cli
    from app.models import Article, DetectionRule, PipelineRun, ProfileVersion, Source
    from app.profiles.seed import seed_profiles
    from sqlalchemy import select

    source = Source(slug="ex", name="Example", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    article = Article(source_id=source.id, url="https://r.example.com/a", title="A", content_text="x" * 600,
                      content_hash="h-ref", word_count=100)
    db_session.add(article)
    db_session.commit()
    seed_profiles(db_session)
    run = PipelineRun(article_id=article.id, profile_version_id=db_session.scalars(select(ProfileVersion.id)).first(),
                      status="succeeded")
    db_session.add(run)
    db_session.flush()
    rule_id = str(uuid.uuid4())
    db_session.add(DetectionRule(id=rule_id, run_id=run.id, article_id=article.id, position=0, group_key={}, observable_ids=[],
                                 title="Generated", sigma_yaml=rule("Generated", rule_id, ["A", "B", "C", "D", "E"]),
                                 sigma_hash="h", status="pending_review"))
    db_session.commit()
    monkeypatch.setenv("DATABASE_URL", test_settings.database_url)

    assert cli.main(["crossref-rules", "--dir", str(ruleset), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    (row,) = report["rules"]
    assert row["match"]["rule_id"] in {"team-1", "own-id"} and row["match"]["similarity"] == 1.0
    assert report["reference_rules"] == 4


def test_deeply_nested_yaml_is_skipped_without_stopping_the_rest(ruleset):
    (ruleset / "deep.yml").write_text("[" * 2000 + "]" * 2000, encoding="utf-8")

    loaded = load_reference_rules([ruleset])

    assert dict(loaded.skipped)["team-rules/deep.yml"] == "nested too deeply"
    assert len(loaded.rules) == 4


def test_crossref_exits_2_when_a_reference_folder_is_missing(db_session, tmp_path, monkeypatch, test_settings):
    from app import cli

    monkeypatch.setenv("DATABASE_URL", test_settings.database_url)

    assert cli.main(["crossref-rules", "--dir", str(tmp_path / "typo")]) == 2
