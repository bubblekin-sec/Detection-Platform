"""The review queue by article: which articles have rules in a status, with the context to pick the next one."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Article, ArticleScore, DetectionRule, PipelineRun, ProfileVersion, Source, StageResult
from app.profiles.seed import seed_profiles


@pytest.fixture
def api(test_settings, db_session):
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def make(db_session):
    """Builds articles with a run, an optional priority score and relevance verdict, and rules in given statuses."""
    seed_profiles(db_session)
    version = db_session.scalars(select(ProfileVersion)).one()
    source = Source(slug="ex", name="Example", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.commit()

    def build(title, statuses, *, score=None, relevance=None, duplicate=False, reviewed_at=None):
        article = Article(source_id=source.id, url=f"https://r.example.com/{title}", title=title, content_text="x" * 600,
                          content_hash=f"h-{title}", word_count=100)
        db_session.add(article)
        db_session.flush()
        if score is not None:
            db_session.add(ArticleScore(article_id=article.id, profile_version_id=version.id, score=score, breakdown={},
                                        scorer="regex-v1"))
        run = PipelineRun(article_id=article.id, profile_version_id=version.id, status="succeeded")
        db_session.add(run)
        db_session.flush()
        if relevance is not None:
            now = datetime.now(UTC)
            db_session.add(StageResult(run_id=run.id, stage="relevance", status="passed",
                                       output={"score": relevance, "min_score": 6, "rationale": "r", "signals": []},
                                       started_at=now, finished_at=now))
        ids = []
        for position, status in enumerate(statuses):
            rule_id = str(uuid.uuid4())
            db_session.add(DetectionRule(
                id=rule_id, run_id=run.id, article_id=article.id, position=position, group_key={}, observable_ids=[],
                title=f"{title} rule {position}", sigma_yaml="title: x", sigma_hash="h", status=status,
                reviewed_at=reviewed_at if status != "pending_review" else None,
            ))
            ids.append(rule_id)
        db_session.commit()
        if duplicate:
            db_session.get(DetectionRule, ids[0]).duplicate_of = ids[1]
            db_session.commit()
        return article

    return build


def test_pending_articles_come_highest_priority_first_with_their_counts(api, make):
    low = make("low", ["pending_review"], score=20.0)
    high = make("high", ["pending_review", "pending_review", "approved"], score=90.0, relevance=8, duplicate=True)
    unscored = make("unscored", ["pending_review"])
    make("done", ["approved", "rejected"], score=99.0)

    rows = api.get("/api/rules/by-article").json()

    assert [row["article"]["id"] for row in rows] == [high.id, low.id, unscored.id]
    top = rows[0]
    assert top["article"]["title"] == "high" and top["article"]["source"]["name"] == "Example"
    assert top["priority"] == {"score": 90.0, "band": "Excellent"}
    assert top["relevance"] == {"score": 8, "min_score": 6}
    assert top["rules"] == {"pending_review": 2, "approved": 1, "rejected": 0}
    assert top["duplicates"] == 1
    assert rows[2]["priority"] is None and rows[2]["relevance"] is None


def test_decided_articles_come_most_recently_decided_first(api, make):
    older = make("older", ["approved"], reviewed_at=datetime(2026, 11, 1, tzinfo=UTC))
    newer = make("newer", ["approved", "rejected"], reviewed_at=datetime(2026, 11, 20, tzinfo=UTC))

    rows = api.get("/api/rules/by-article?status=approved").json()

    assert [row["article"]["id"] for row in rows] == [newer.id, older.id]
    assert rows[0]["last_decided_at"].startswith("2026-11-20")
    assert [row["article"]["id"] for row in api.get("/api/rules/by-article?status=rejected").json()] == [newer.id]


def test_the_queue_rejects_an_unknown_status(api):
    assert api.get("/api/rules/by-article?status=bogus").status_code == 422


def test_relevance_and_run_come_from_the_newest_run_and_duplicates_count_the_requested_status(api, make, db_session):
    article = make("rerun", ["pending_review", "approved"], relevance=3, duplicate=True)  # the duplicate is the pending one
    version = db_session.scalars(select(ProfileVersion)).one()
    newer = PipelineRun(article_id=article.id, profile_version_id=version.id, status="succeeded")
    db_session.add(newer)
    db_session.flush()
    now = datetime.now(UTC)
    db_session.add(StageResult(run_id=newer.id, stage="relevance", status="passed",
                               output={"score": 8, "min_score": 6, "rationale": "r", "signals": []},
                               started_at=now, finished_at=now))
    db_session.add(DetectionRule(id=str(uuid.uuid4()), run_id=newer.id, article_id=article.id, position=0, group_key={},
                                 observable_ids=[], title="newer", sigma_yaml="title: x", sigma_hash="h", status="pending_review"))
    db_session.commit()

    (pending,) = api.get("/api/rules/by-article").json()
    (approved,) = api.get("/api/rules/by-article?status=approved").json()

    assert (pending["run_id"], pending["relevance"]["score"]) == (newer.id, 8)
    assert pending["rules"] == {"pending_review": 2, "approved": 1, "rejected": 0}
    assert (pending["duplicates"], approved["duplicates"]) == (1, 0)
