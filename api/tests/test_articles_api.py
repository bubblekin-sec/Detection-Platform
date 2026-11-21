"""Article list as a priority queue: sort, filter by band and pipeline status, priority and pipeline fields."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Article, DetectionRule, PipelineRun, Source
from app.pipeline.scoring import score_articles
from app.profiles.seed import latest_version, seed_profiles

HIGH = "Operators ran rundll32.exe and powershell.exe, wrote HKLM keys and used certutil.exe for the threat actor campaign. " * 6
MID = "The threat actor used schtasks.exe and certutil.exe during the campaign, dumped lsass.exe, then lateral movement. " * 6
LOW = "Our community meetup covers industry trends and team best practices. " * 6


@pytest.fixture
def api(test_settings, db_session):
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def articles(db_session) -> dict[str, Article]:
    source = Source(slug="research", name="Research", url="https://r.example.com/", rss_url="https://r.example.com/feed",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    now = datetime(2026, 11, 20, tzinfo=UTC)
    items = {}
    for n, (key, text) in enumerate([("high", HIGH), ("mid", MID), ("low", LOW), ("unscored", LOW + " x")]):
        item = Article(source_id=source.id, url=f"https://r.example.com/{key}", title=f"{key.title()} report",
                       content_text=text, content_hash=f"h-{key}", word_count=len(text.split()),
                       published_at=now - timedelta(days=n))
        db_session.add(item)
        items[key] = item
    db_session.commit()
    seed_profiles(db_session)
    version = latest_version(db_session, "detection")
    score_articles(db_session, version, article_ids=[items[k].id for k in ("high", "mid", "low")])
    return items


def add_run(db_session, article: Article, status: str, *, rules: int = 0) -> None:
    version = latest_version(db_session, "detection")
    run = PipelineRun(article_id=article.id, profile_version_id=version.id, status=status, trigger="auto")
    db_session.add(run)
    db_session.flush()
    for position in range(rules):
        db_session.add(DetectionRule(
            id=str(uuid.uuid4()), run_id=run.id, article_id=article.id, position=position,
            group_key={"platform": "windows", "telemetry_category": "process_creation"}, observable_ids=[],
            title="r", level="high", sigma_yaml="title: r", sigma_hash="0" * 64, status="pending_review",
        ))
    db_session.commit()


def titles(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()]


def test_default_order_and_shape_are_unchanged(api, articles):
    body = api.get("/api/articles").json()

    assert [item["title"] for item in body] == ["High report", "Mid report", "Low report", "Unscored report"]
    assert {"id", "url", "title", "excerpt", "word_count", "source"} <= set(body[0])


def test_each_article_carries_its_priority_and_keyword_chips(api, articles):
    high = api.get("/api/articles").json()[0]

    assert high["priority"]["score"] >= 60 and high["priority"]["band"] in {"Good", "Excellent"}
    assert "rundll32.exe" in high["priority"]["terms"]["perfect"]
    assert set(high["priority"]["terms"]) == {"perfect", "lolbas", "intelligence"}  # chips: strong buckets only
    assert api.get("/api/articles").json()[3]["priority"] is None


def test_sort_by_priority_puts_unscored_articles_last(api, articles):
    assert titles(api.get("/api/articles?sort=priority")) == ["High report", "Mid report", "Low report", "Unscored report"]
    assert titles(api.get("/api/articles?sort=priority&order=asc"))[:3] == ["Low report", "Mid report", "High report"]


def test_filter_by_band_and_minimum_score(api, articles):
    high_band = api.get("/api/articles").json()[0]["priority"]["band"]

    assert titles(api.get(f"/api/articles?band={high_band.replace(' ', '%20')}")) == ["High report"]
    assert titles(api.get("/api/articles?min_score=37.5&sort=priority")) == ["High report", "Mid report"]
    assert api.get("/api/articles?band=Bogus").status_code == 422


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("not_run", ["Low report", "Unscored report"]),
        ("in_progress", ["Mid report"]),
        ("rules", ["High report"]),
    ],
)
def test_filter_by_pipeline_status(api, articles, db_session, status, expected):
    add_run(db_session, articles["high"], "filtered_out")
    add_run(db_session, articles["high"], "succeeded", rules=2)  # the latest run decides the status
    add_run(db_session, articles["mid"], "queued")

    assert titles(api.get(f"/api/articles?status={status}")) == expected


def test_pipeline_field_summarises_the_latest_run(api, articles, db_session):
    add_run(db_session, articles["high"], "succeeded", rules=2)
    add_run(db_session, articles["mid"], "succeeded", rules=0)

    by_title = {item["title"]: item["pipeline"] for item in api.get("/api/articles").json()}

    assert by_title["High report"]["status"] == "rules" and by_title["High report"]["rules"] == 2
    assert by_title["Mid report"]["status"] == "no_rules"
    assert by_title["Low report"] == {"status": "not_run", "run_id": None, "rules": 0}


def test_paging(api, articles):
    assert titles(api.get("/api/articles?sort=priority&limit=2&offset=1")) == ["Mid report", "Low report"]


def test_unknown_profile_or_sort_is_rejected(api, articles):
    assert api.get("/api/articles?sort=bogus").status_code == 422
    assert api.get("/api/articles?profile=nope").status_code == 404
