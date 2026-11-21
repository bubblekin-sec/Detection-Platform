"""Stored priority scores: profile v2, scoring at ingest, rescoring."""

import pytest

from app.config import Settings
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import Article, ArticleScore, Profile, ProfileVersion, Source
from app.pipeline.scoring import score_articles
from app.profiles.seed import latest_version, seed_profiles
from tests.fakes import FakeFetcher, fixture_bytes

FEED_URL = "https://research.example.com/feed"
HERON_URL = "https://research.example.com/2026/11/quiet-heron"
CONFIG = {"min_content_length": 500, "title_filter_keywords": ["webinar"], "extract": {"body_selectors": [".post-content"]}}


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
    return item


def count_scores(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ArticleScore))


def test_seeding_creates_the_detection_profile_with_priority(db_session):
    assert seed_profiles(db_session) == {"added": 1, "upgraded": 0, "existing": 0}

    version = latest_version(db_session, "detection")
    assert version.version == 2
    assert version.definition["priority"]["threshold"] == 37.5
    assert len(version.definition["priority"]["buckets"]["perfect"]["terms"]) > 100


def test_seeding_adds_v2_beside_an_existing_v1_and_never_changes_v1(db_session):
    profile = Profile(slug="detection", name="Detection engineering")
    db_session.add(profile)
    db_session.flush()
    v1_definition = {"audience": "x", "relevant_if": "y", "min_score": 6, "output_kind": "sigma_kql",
                     "extract_fields": [{"name": "cmdline", "description": "d"}]}
    db_session.add(ProfileVersion(profile_id=profile.id, version=1, definition=v1_definition))
    db_session.commit()

    assert seed_profiles(db_session) == {"added": 0, "upgraded": 1, "existing": 0}
    assert seed_profiles(db_session) == {"added": 0, "upgraded": 0, "existing": 1}

    versions = {v.version: v.definition for v in db_session.scalars(select(ProfileVersion))}
    assert versions[1] == v1_definition
    assert versions[2]["priority"]["threshold"] == 37.5


def test_fetching_scores_new_articles_for_each_profile_with_priority(test_settings, db_session, source, fetcher):
    seed_profiles(db_session)
    with TestClient(create_app(test_settings, fetcher=fetcher)) as client:
        summary = client.post(f"/api/sources/{source.id}/fetch").json()
        again = client.post(f"/api/sources/{source.id}/fetch").json()

    assert summary["new"] == 2 and summary["scored"] == 2
    assert again["scored"] == 0  # duplicates are not rescored on fetch
    lantern = db_session.scalars(select(Article).where(Article.title.like("Operation Paper Lantern%"))).one()
    row = db_session.scalars(select(ArticleScore).where(ArticleScore.article_id == lantern.id)).one()
    assert row.score > 37.5
    assert "rundll32.exe" in row.breakdown["perfect"]["terms"]
    assert row.scorer == "regex-v1"


def test_rescoring_is_idempotent_and_only_missing_can_be_targeted(db_session, source, fetcher):
    from app.sources.ingest import ingest_source

    ingest_source(db_session, source, fetcher, settings=Settings(_env_file=None))  # no profile yet: nothing scored
    assert count_scores(db_session) == 0
    seed_profiles(db_session)
    version = latest_version(db_session, "detection")

    assert score_articles(db_session, version, missing_only=True) == 2
    assert score_articles(db_session, version, missing_only=True) == 0
    assert score_articles(db_session, version) == 2
    assert count_scores(db_session) == 2


def test_rescore_endpoint(test_settings, db_session, source, fetcher):
    from app.sources.ingest import ingest_source

    ingest_source(db_session, source, fetcher, settings=Settings(_env_file=None))
    seed_profiles(db_session)
    with TestClient(create_app(test_settings)) as client:
        response = client.post("/api/profiles/detection/rescore")
        missing = client.post("/api/profiles/nope/rescore")

    assert response.json() == {"profile": "detection", "version": 2, "scored": 2}
    assert missing.status_code == 404
    assert missing.json() == {"detail": {"errors": ["Profile 'nope' not found."]}}


def test_a_profile_without_priority_is_not_scored(db_session, source, fetcher):
    from app.sources.ingest import ingest_source

    profile = Profile(slug="plain", name="Plain")
    db_session.add(profile)
    db_session.flush()
    db_session.add(ProfileVersion(profile_id=profile.id, version=1, definition={
        "audience": "x", "relevant_if": "y", "min_score": 6, "output_kind": "sigma_kql",
        "extract_fields": [{"name": "cmdline", "description": "d"}],
    }))
    db_session.commit()

    result = ingest_source(db_session, source, fetcher, settings=Settings(_env_file=None))

    assert (result.new, result.scored) == (2, 0)


def test_rescore_rejects_a_profile_without_priority(test_settings, db_session):
    profile = Profile(slug="plain", name="Plain")
    db_session.add(profile)
    db_session.flush()
    db_session.add(ProfileVersion(profile_id=profile.id, version=1, definition={
        "audience": "x", "relevant_if": "y", "min_score": 6, "output_kind": "sigma_kql",
        "extract_fields": [{"name": "cmdline", "description": "d"}],
    }))
    db_session.commit()
    with TestClient(create_app(test_settings)) as client:
        response = client.post("/api/profiles/plain/rescore")

    assert response.status_code == 422
    assert "no priority scoring" in response.json()["detail"]["errors"][0]
