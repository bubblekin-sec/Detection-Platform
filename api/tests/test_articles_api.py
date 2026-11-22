"""Article list as a priority queue: sort, filter by band and pipeline status, priority and pipeline fields."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Article, DetectionRule, PipelineRun, ProfileVersion, Source
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


def add_run(db_session, article: Article, status: str, *, rules: int = 0, version: ProfileVersion | None = None) -> None:
    version = version or latest_version(db_session, "detection")
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
    assert titles(api.get("/api/articles?band=Very%20Poor")) == ["Low report"]  # the lowest band has no upper bound
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


def test_band_filter_excludes_scores_in_higher_bands(api, articles):
    high_band = api.get("/api/articles").json()[0]["priority"]["band"]
    lower = {"Excellent": "Good", "Good": "Fair"}[high_band]

    assert "High report" not in titles(api.get(f"/api/articles?band={lower}"))


def test_runs_under_an_older_profile_version_still_count(api, articles, db_session):
    current = latest_version(db_session, "detection")
    older = ProfileVersion(profile_id=current.profile_id, version=1, definition=current.definition)
    db_session.add(older)
    db_session.commit()
    add_run(db_session, articles["low"], "succeeded", rules=1, version=older)

    by_title = {item["title"]: item["pipeline"]["status"] for item in api.get("/api/articles").json()}

    assert by_title["Low report"] == "rules"


def test_sort_by_source_is_a_to_z_by_default_and_honours_order(api, articles, db_session):
    other = Source(slug="alpha", name="Alpha Labs", url="https://a.example.com/", rss_url="https://a.example.com/f",
                   active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(other)
    db_session.flush()
    for n in range(3):  # same source, no published date: only the id tie-break orders these
        db_session.add(Article(source_id=other.id, url=f"https://a.example.com/{n}", title=f"Alpha {n}",
                               content_text="text", content_hash=f"a{n}", word_count=1))
    db_session.commit()

    ascending = titles(api.get("/api/articles?sort=source"))
    assert ascending[:3] == ["Alpha 2", "Alpha 1", "Alpha 0"] and ascending[3] == "High report"
    assert titles(api.get("/api/articles?sort=source&order=desc"))[0] == "High report"
    pages = [titles(api.get(f"/api/articles?sort=source&limit=1&offset={n}"))[0] for n in range(7)]
    assert pages == ascending


def test_order_must_be_asc_or_desc(api, articles):
    assert api.get("/api/articles?order=sideways").status_code == 422


def test_list_and_detail_work_before_any_profile_is_seeded(api, db_session):
    source = Source(slug="s", name="S", url="https://s.example.com/", rss_url="https://s.example.com/f",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    item = Article(source_id=source.id, url="https://s.example.com/1", title="Lone", content_text="text",
                   content_hash="x", word_count=1)
    db_session.add(item)
    db_session.commit()

    (listed,) = api.get("/api/articles").json()
    detail = api.get(f"/api/articles/{item.id}").json()

    assert listed["priority"] is None and listed["pipeline"]["status"] == "not_run"
    assert detail["priority"] is None and detail["pipeline"] is None


@pytest.mark.parametrize("later", ["succeeded", "failed", "filtered_out"])
def test_rules_from_an_earlier_run_are_not_hidden_by_a_later_one(api, articles, db_session, later):
    add_run(db_session, articles["high"], "succeeded", rules=2)
    add_run(db_session, articles["high"], later)  # found no rules, failed, or was filtered out

    high = next(item for item in api.get("/api/articles").json() if item["title"] == "High report")

    assert high["pipeline"]["status"] == "rules" and high["pipeline"]["rules"] == 2
    assert "High report" in titles(api.get("/api/articles?status=rules"))


def test_a_run_in_progress_shows_even_when_earlier_rules_exist(api, articles, db_session):
    add_run(db_session, articles["high"], "succeeded", rules=2)
    add_run(db_session, articles["high"], "queued")

    high = next(item for item in api.get("/api/articles").json() if item["title"] == "High report")

    assert high["pipeline"]["status"] == "in_progress" and high["pipeline"]["rules"] == 2

