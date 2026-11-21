"""Dashboard metrics: funnel, volume, bands, top articles, failing sources, spend; rule counts."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.dashboard import metrics
from app.dashboard.metrics import spend
from app.llm.pricing import Price
from app.main import create_app
from app.models import Article, DetectionRule, PipelineRun, Source, StageResult
from app.pipeline.scoring import score_articles
from app.profiles.seed import latest_version, seed_profiles

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
HIGH = "Operators ran rundll32.exe and powershell.exe, wrote HKLM keys and used certutil.exe for the campaign. " * 6
LOW = "Our community meetup covers industry trends and team best practices. " * 6


@pytest.fixture
def api(test_settings, db_session, monkeypatch):
    monkeypatch.setattr(metrics, "today_utc", lambda: NOW.date())  # the fixtures are dated relative to NOW
    with TestClient(create_app(test_settings)) as client:
        yield client


@pytest.fixture
def data(db_session):
    healthy = Source(slug="ok", name="Healthy", url="https://ok.example.com/", rss_url="https://ok.example.com/f",
                     active=True, check_frequency_seconds=3600, config={}, origin="user")
    broken = Source(slug="broken", name="Broken Feed", url="https://b.example.com/", rss_url="https://b.example.com/f",
                    active=True, check_frequency_seconds=3600, config={}, origin="user",
                    last_error="Feed returned HTTP 404.", last_checked_at=NOW)
    db_session.add_all([healthy, broken])
    db_session.flush()
    articles = []
    for n, text in enumerate([HIGH, HIGH + " lsass.exe", LOW, LOW + " x"]):
        item = Article(source_id=healthy.id, url=f"https://ok.example.com/{n}", title=f"Article {n}", content_text=text,
                       content_hash=f"h{n}", word_count=50, fetched_at=NOW - timedelta(days=n % 2))
        db_session.add(item)
        articles.append(item)
    db_session.commit()
    seed_profiles(db_session)
    version = latest_version(db_session, "detection")
    score_articles(db_session, version)

    def run(article, status, relevance, rules=(), tokens=(1000, 100), model="claude-haiku-5-5"):
        item = PipelineRun(article_id=article.id, profile_version_id=version.id, status=status, trigger="auto")
        db_session.add(item)
        db_session.flush()
        db_session.add(StageResult(run_id=item.id, stage="relevance", status=relevance, output={}, model=model,
                                   input_tokens=tokens[0], output_tokens=tokens[1], started_at=NOW, finished_at=NOW))
        for position, rule_status in enumerate(rules):
            db_session.add(DetectionRule(
                id=str(uuid.uuid4()), run_id=item.id, article_id=article.id, position=position,
                group_key={"platform": "windows", "telemetry_category": "process_creation"}, observable_ids=[],
                title="r", level="high", sigma_yaml="title: r", sigma_hash="0" * 64, status=rule_status,
            ))
        db_session.commit()

    run(articles[0], "succeeded", "passed", rules=("approved", "pending_review"))
    run(articles[1], "succeeded", "passed", rules=("rejected",))
    run(articles[2], "filtered_out", "filtered")
    return articles


def test_funnel_counts_articles_at_each_stage(api, data):
    funnel = api.get("/api/dashboard").json()["funnel"]

    assert funnel == {"fetched": 4, "above_threshold": 2, "run": 3, "relevant": 2, "with_rules": 2, "approved": 1,
                      "threshold": 37.5}


def test_daily_volume_is_zero_filled(api, data):
    volume = api.get("/api/dashboard?days=3").json()["volume"]

    assert volume == [{"date": "2026-11-19", "articles": 0}, {"date": "2026-11-20", "articles": 2},
                      {"date": "2026-11-21", "articles": 2}]


@pytest.fixture
def api_new_york(test_settings, db_session, monkeypatch):
    """An app whose database sessions run in America/New_York, so date() without the UTC conversion would differ."""
    monkeypatch.setattr(metrics, "today_utc", lambda: NOW.date())
    separator = "&" if "?" in test_settings.database_url else "?"
    settings = test_settings.model_copy(update={
        "database_url": f"{test_settings.database_url}{separator}options=-c%20TimeZone%3DAmerica%2FNew_York"
    })
    with TestClient(create_app(settings)) as client:
        yield client


def test_volume_buckets_days_in_utc(api_new_york, data, db_session):
    late_evening_east_coast = datetime(2026, 11, 20, 23, 30, tzinfo=timezone(timedelta(hours=-5)))  # 04:30 UTC on the 21st
    db_session.add(Article(source_id=data[0].source_id, url="https://ok.example.com/late", title="Late", content_text="t",
                           content_hash="late", word_count=1, fetched_at=late_evening_east_coast))
    db_session.commit()

    volume = {day["date"]: day["articles"] for day in api_new_york.get("/api/dashboard?days=2").json()["volume"]}

    assert volume == {"2026-11-20": 2, "2026-11-21": 3}


def test_band_histogram_and_top_articles(api, data):
    body = api.get("/api/dashboard").json()

    assert sum(body["bands"].values()) == 4
    assert list(body["bands"]) == ["Excellent", "Good", "Fair", "Poor", "Very Poor"]
    assert [item["title"] for item in body["top"][:2]] == ["Article 1", "Article 0"]
    assert body["top"][0]["pipeline"] == "rules"


def test_failing_sources_are_listed(api, data):
    failing = api.get("/api/dashboard").json()["failing_sources"]

    assert [(item["name"], item["last_error"]) for item in failing] == [("Broken Feed", "Feed returned HTTP 404.")]


def test_spend_is_tokens_times_prices_and_unknown_models_have_no_cost(db_session, data):
    from sqlalchemy import select

    run = db_session.scalars(select(PipelineRun)).first()
    db_session.add(StageResult(run_id=run.id, stage="extraction", status="passed", output={}, model="mystery-model",
                               input_tokens=5000, output_tokens=500, started_at=NOW, finished_at=NOW))
    db_session.commit()
    prices = {"claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01)}

    result = spend(db_session, prices)

    haiku = next(row for row in result["by_model"] if row["model"] == "claude-haiku-5-5")
    assert (haiku["input_tokens"], haiku["output_tokens"]) == (3000, 300)
    assert haiku["cost"] == pytest.approx((3000 * 0.10 + 300 * 0.50) / 1_000_000)
    mystery = next(row for row in result["by_model"] if row["model"] == "mystery-model")
    assert mystery["cost"] is None
    assert result["total"] == pytest.approx(haiku["cost"])
    assert result["unpriced_models"] == ["mystery-model"]
    assert result["by_stage"] == {"relevance": pytest.approx(haiku["cost"])}  # unpriced stages are left out


def test_spend_prices_cache_reads(db_session, data):
    from sqlalchemy import select

    run = db_session.scalars(select(PipelineRun)).first()
    db_session.add(StageResult(run_id=run.id, stage="extraction", status="passed", output={}, model="claude-opus-5-5",
                               input_tokens=1000, output_tokens=0, cache_read_tokens=200_000, started_at=NOW, finished_at=NOW))
    db_session.commit()
    prices = {"claude-opus-5-5": Price(input=4.0, output=20.0, cache_read=0.20)}

    result = spend(db_session, prices)

    assert result["by_stage"]["extraction"] == pytest.approx((1000 * 4.0 + 200_000 * 0.20) / 1_000_000)


def test_rule_counts_by_status(api, data):
    assert api.get("/api/rules/counts").json() == {"pending_review": 1, "approved": 1, "rejected": 1}


def test_empty_database(api, db_session):
    seed_profiles(db_session)
    body = api.get("/api/dashboard").json()

    assert body["funnel"]["fetched"] == 0 and body["top"] == [] and body["spend"]["total"] == 0


def test_dashboard_before_any_profile_is_seeded(api, db_session):
    source = Source(slug="b", name="Broken", url="https://b.example.com/", rss_url="https://b.example.com/f",
                    active=True, check_frequency_seconds=3600, config={}, origin="user", last_error="HTTP 500")
    db_session.add(source)
    db_session.flush()
    db_session.add(Article(source_id=source.id, url="https://b.example.com/1", title="A", content_text="t",
                           content_hash="a", word_count=1, fetched_at=NOW))
    db_session.commit()

    body = api.get("/api/dashboard").json()

    assert body["profile"] is None
    assert body["funnel"]["fetched"] == 1 and body["funnel"]["threshold"] is None
    assert set(body["bands"].values()) == {0} and body["top"] == []
    assert [item["name"] for item in body["failing_sources"]] == ["Broken"]
    assert sum(day["articles"] for day in body["volume"]) == 1
    assert api.get("/api/dashboard?profile=nope").status_code == 404
