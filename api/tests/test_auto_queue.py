"""The priority gate: only articles at or above the profile threshold are ever sent to a model, automatically."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.main import create_app
from app.models import Article, ArticleScore, PipelineRun, Source
from app.pipeline.auto_queue import auto_queue
from app.pipeline.run import claim_next_run, process_next_run, queue_run
from app.pipeline.scoring import score_articles
from app.profiles.seed import latest_version, seed_profiles
from app.sources.ingest import ingest_source
from tests.fakes import FakeFetcher, ScriptedLLM, fixture_bytes

FEED_URL = "https://research.example.com/feed"
HERON_URL = "https://research.example.com/2026/11/quiet-heron"
CONFIG = {"min_content_length": 500, "title_filter_keywords": ["webinar"], "extract": {"body_selectors": [".post-content"]}}
HIGH = "The operators ran rundll32.exe and wrote HKLM run keys via schtasks.exe for persistence. " * 10
LOW = "Join our community meetup next week to discuss industry trends and best practices for teams. " * 10


@pytest.fixture
def fetcher() -> FakeFetcher:
    fake = FakeFetcher()
    fake.serve(FEED_URL, fixture_bytes("research_blog.rss.xml"))
    fake.serve(HERON_URL, fixture_bytes("quiet_heron.html"), content_type="text/html")
    return fake


@pytest.fixture
def source(db_session) -> Source:
    item = Source(slug="research", name="Research", url="https://research.example.com/", rss_url=FEED_URL,
                  active=True, check_frequency_seconds=3600, config=CONFIG, origin="user")
    db_session.add(item)
    db_session.commit()
    seed_profiles(db_session)
    return item


def add_article(db_session, source, title: str, text: str, n: int) -> Article:
    article = Article(source_id=source.id, url=f"https://research.example.com/a{n}", title=title,
                      content_text=text + f" #{n}", content_hash=f"hash-{n}", word_count=len(text.split()))
    db_session.add(article)
    db_session.commit()
    return article


def runs(db_session) -> list[PipelineRun]:
    db_session.expire_all()
    return list(db_session.scalars(select(PipelineRun).order_by(PipelineRun.id)))


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_fetch_auto_queues_only_articles_above_the_threshold(test_settings, db_session, source, fetcher):
    with TestClient(create_app(test_settings, fetcher=fetcher)) as client:
        summary = client.post(f"/api/sources/{source.id}/fetch").json()

    queued = runs(db_session)
    scores = {row.article_id: row.score for row in db_session.scalars(select(ArticleScore))}
    above = {article_id for article_id, score in scores.items() if score >= 37.5}
    assert len(scores) == 2 and above  # both fixture articles are scored; at least one clears the bar
    assert {run.article_id for run in queued} == above
    assert {(run.trigger, run.status) for run in queued} == {("auto", "queued")}
    assert (summary["auto_queued"], summary["deferred"]) == (len(above), 0)


def test_below_threshold_articles_never_reach_a_model(db_session, source, migrated_engine):
    low = add_article(db_session, source, "Community meetup", LOW, 1)
    version = latest_version(db_session, "detection")
    score_articles(db_session, version, article_ids=[low.id])

    result = auto_queue(db_session, [low.id], settings())
    llm = ScriptedLLM()  # no responses queued: any model call would fail the test
    processed = process_next_run(sessionmaker(migrated_engine, expire_on_commit=False), llm, settings())

    assert (result.queued, processed, llm.calls) == (0, None, [])


def test_an_article_with_any_existing_run_is_never_auto_queued_again(db_session, source):
    high = add_article(db_session, source, "Intrusion report", HIGH, 1)
    version = latest_version(db_session, "detection")
    score_articles(db_session, version, article_ids=[high.id])
    run, _ = queue_run(db_session, high.id, version)
    run.status = "failed"
    db_session.commit()

    assert auto_queue(db_session, [high.id], settings()).queued == 0
    assert len(runs(db_session)) == 1


def test_rescoring_never_queues(db_session, source):
    high = add_article(db_session, source, "Intrusion report", HIGH, 1)

    score_articles(db_session, latest_version(db_session, "detection"))

    assert runs(db_session) == []


def test_daily_cap_queues_the_highest_scores_and_defers_the_rest(db_session, source):
    texts = [HIGH, HIGH + " Also lsass.exe and FromBase64String and wmic.exe.", HIGH + " And mshta.exe."]
    articles = [add_article(db_session, source, f"Report {n}", text, n) for n, text in enumerate(texts)]
    score_articles(db_session, latest_version(db_session, "detection"), article_ids=[a.id for a in articles])
    scores = {row.article_id: row.score for row in db_session.scalars(select(ArticleScore))}

    result = auto_queue(db_session, [a.id for a in articles], settings(auto_queue_max_per_day=2))

    queued_ids = {run.article_id for run in runs(db_session)}
    expected = set(sorted(scores, key=lambda article_id: -scores[article_id])[:2])
    assert (result.queued, result.deferred) == (2, 1)
    assert queued_ids == expected


def test_the_cap_counts_runs_already_auto_queued_today(db_session, source):
    first = add_article(db_session, source, "Report 1", HIGH, 1)
    second = add_article(db_session, source, "Report 2", HIGH + " lsass.exe", 2)
    score_articles(db_session, latest_version(db_session, "detection"))

    assert auto_queue(db_session, [first.id], settings(auto_queue_max_per_day=1)).queued == 1
    assert auto_queue(db_session, [second.id], settings(auto_queue_max_per_day=1)).deferred == 1


def test_a_cap_of_zero_turns_auto_queueing_off(db_session, source):
    high = add_article(db_session, source, "Intrusion report", HIGH, 1)
    score_articles(db_session, latest_version(db_session, "detection"))

    assert auto_queue(db_session, [high.id], settings(auto_queue_max_per_day=0)).queued == 0


def test_a_profile_with_no_threshold_queues_nothing(db_session, source):
    from app.models import ProfileVersion

    version = latest_version(db_session, "detection")
    definition = {**version.definition, "priority": {**version.definition["priority"], "threshold": None}}
    db_session.add(ProfileVersion(profile_id=version.profile_id, version=3, definition=definition))
    db_session.commit()
    high = add_article(db_session, source, "Intrusion report", HIGH, 1)
    score_articles(db_session, latest_version(db_session, "detection"))

    assert auto_queue(db_session, [high.id], settings()).queued == 0


def test_manual_runs_are_claimed_before_older_automatic_ones(db_session, source):
    first = add_article(db_session, source, "Report 1", HIGH, 1)
    second = add_article(db_session, source, "Report 2", HIGH + " lsass.exe", 2)
    version = latest_version(db_session, "detection")
    auto_run, _ = queue_run(db_session, first.id, version, trigger="auto")
    manual_run, _ = queue_run(db_session, second.id, version)

    assert claim_next_run(db_session, datetime.now(UTC)) == manual_run.id


def test_queue_runs_cli_can_target_the_backlog_by_priority(db_session, source, monkeypatch, capsys):
    from app import cli

    add_article(db_session, source, "Community meetup", LOW, 1)
    high = add_article(db_session, source, "Intrusion report", HIGH, 2)
    score_articles(db_session, latest_version(db_session, "detection"))
    monkeypatch.setattr(cli, "Settings", lambda: Settings(_env_file=None, database_url=db_session.get_bind().url.render_as_string(hide_password=False)))

    assert cli.main(["queue-runs", "--min-priority", "37.5", "--limit", "10"]) == 0

    assert [(run.article_id, run.trigger) for run in runs(db_session)] == [(high.id, "batch")]


def cli_settings(db_session):
    return lambda: Settings(_env_file=None, database_url=db_session.get_bind().url.render_as_string(hide_password=False))


def test_queue_runs_takes_the_highest_scores_first(db_session, source, monkeypatch):
    from app import cli

    texts = [HIGH, HIGH + " lsass.exe", HIGH + " lsass.exe FromBase64String wmic.exe"]
    articles = [add_article(db_session, source, f"Report {n}", text, n) for n, text in enumerate(texts)]
    score_articles(db_session, latest_version(db_session, "detection"))
    monkeypatch.setattr(cli, "Settings", cli_settings(db_session))

    assert cli.main(["queue-runs", "--min-priority", "37.5", "--limit", "2"]) == 0

    assert {run.article_id for run in runs(db_session)} == {articles[2].id, articles[1].id}


def test_queue_runs_skips_failed_and_earlier_version_runs_unless_asked(db_session, source, monkeypatch):
    from app import cli
    from app.models import ProfileVersion

    failed = add_article(db_session, source, "Failed before", HIGH, 1)
    done_on_v1 = add_article(db_session, source, "Done on v1", HIGH + " lsass.exe", 2)
    v2 = latest_version(db_session, "detection")
    v1 = ProfileVersion(profile_id=v2.profile_id, version=1, definition={k: v for k, v in v2.definition.items() if k != "priority"})
    db_session.add(v1)
    db_session.flush()
    db_session.add(PipelineRun(article_id=failed.id, profile_version_id=v2.id, status="failed", trigger="batch"))
    db_session.add(PipelineRun(article_id=done_on_v1.id, profile_version_id=v1.id, status="succeeded", trigger="manual"))
    db_session.commit()
    score_articles(db_session, v2)
    monkeypatch.setattr(cli, "Settings", cli_settings(db_session))

    assert cli.main(["queue-runs", "--min-priority", "0"]) == 0
    assert [run.status for run in runs(db_session)] == ["failed", "succeeded"]  # nothing new

    assert cli.main(["queue-runs", "--min-priority", "0", "--retry-failed"]) == 0
    assert [(run.article_id, run.status) for run in runs(db_session)][-1] == (failed.id, "queued")


def test_the_cap_only_counts_todays_automatic_runs(db_session, source):
    first = add_article(db_session, source, "Report 1", HIGH, 1)
    second = add_article(db_session, source, "Report 2", HIGH + " lsass.exe", 2)
    version = latest_version(db_session, "detection")
    score_articles(db_session, version)
    queue_run(db_session, first.id, version)  # manual: never counts toward the cap
    yesterday = PipelineRun(article_id=first.id, profile_version_id=version.id, status="succeeded", trigger="auto",
                            created_at=datetime(2026, 11, 20, 23, 0, tzinfo=UTC))
    db_session.add(yesterday)
    db_session.commit()

    result = auto_queue(db_session, [second.id], settings(auto_queue_max_per_day=1), now=datetime(2026, 11, 21, 9, 0, tzinfo=UTC))

    assert result.queued == 1


def test_a_scoring_or_gate_failure_never_fails_the_fetch(test_settings, db_session, source, fetcher, monkeypatch, caplog):
    from app.sources import ingest

    def broken(*args, **kwargs):
        raise RuntimeError("gate bug")

    monkeypatch.setattr(ingest, "auto_queue", broken)
    with TestClient(create_app(test_settings, fetcher=fetcher)) as client:
        response = client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 200
    assert (response.json()["new"], response.json()["scored"], response.json()["auto_queued"]) == (2, 2, 0)
    assert "queue-runs --min-priority" in caplog.text


def test_run_trigger_values_are_constrained(db_session, source):
    from sqlalchemy.exc import IntegrityError

    article = add_article(db_session, source, "Report", HIGH, 1)
    db_session.add(PipelineRun(article_id=article.id, profile_version_id=latest_version(db_session, "detection").id,
                               status="queued", trigger="bogus"))

    with pytest.raises(IntegrityError, match="ck_pipeline_runs_trigger"):
        db_session.commit()
    db_session.rollback()
