"""Highlight spans: keyword matches and observable evidence, as UTF-16 offsets for the browser."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Article, ArticleScore, Observable, PipelineRun, Source
from app.pipeline.priority import FoldedText, PriorityScorer, locate, utf16_offsets
from app.pipeline.scoring import score_articles
from app.profiles.schema import PriorityConfig
from app.profiles.seed import latest_version, seed_profiles


def scorer(**buckets) -> PriorityScorer:
    caps = {"perfect": 75, "lolbas": 10, "intelligence": 10, "good": 5, "negative": 15}
    return PriorityScorer(PriorityConfig.model_validate({
        "buckets": {name: {"max_points": caps[name], "terms": [{"term": t} for t in terms]} for name, terms in buckets.items()},
    }))


def js_slice(text: str, start: int, end: int) -> str:
    """What JavaScript's text.slice(start, end) returns, given UTF-16 offsets."""
    return text.encode("utf-16-le")[start * 2 : end * 2].decode("utf-16-le")


def test_keyword_spans_slice_back_to_their_terms_in_any_case():
    text = "They ran Rundll32.exe, then rundll32.exe again under HKLM."
    spans, truncated = scorer(perfect=["rundll32.exe", "hklm"]).spans(text)

    assert [(js_slice(text, s["start"], s["end"]), s["bucket"], s["term"]) for s in spans] == [
        ("Rundll32.exe", "perfect", "rundll32.exe"),
        ("rundll32.exe", "perfect", "rundll32.exe"),
        ("HKLM", "perfect", "hklm"),
    ]
    assert truncated is False


def test_spans_use_utf16_offsets_so_emoji_do_not_shift_them():
    text = "🔥 Alert: certutil.exe downloaded a payload"
    (span,), _ = scorer(lolbas=["certutil.exe"]).spans(text)

    assert js_slice(text, span["start"], span["end"]) == "certutil.exe"
    assert span["start"] == text.index("certutil.exe") + 1  # the emoji is two UTF-16 units


def test_spans_agree_with_scoring_when_case_folding_changes_length():
    text = "Seen on STRASSE 1 and Straße 2 hosts."
    rules = scorer(good=["straße"])

    spans, _ = rules.spans(text)

    assert rules.score("", text).buckets["good"]["terms"] == ["strasse"]
    assert [js_slice(text, s["start"], s["end"]) for s in spans] == ["STRASSE", "Straße"]


def test_spans_beyond_the_cap_are_dropped_and_flagged():
    text = "hklm " * 5

    spans, truncated = scorer(perfect=["hklm"]).spans(text, limit=3)

    assert [s["start"] for s in spans] == [0, 5, 10]
    assert truncated is True


def test_folded_text_answers_many_quotes_from_one_index():
    folded = FoldedText("Straße  one\ntwo", collapse_whitespace=True)

    assert folded.find("STRASSE ONE two") == (0, 15)
    assert folded.find("SE one") == (4, 11)  # a match starting inside "ß" -> "ss" rounds out to the whole "ß"
    assert folded.find("three") is None


def test_utf16_offsets_helper():
    assert utf16_offsets("a😀b", [0, 1, 2, 3]) == [0, 1, 3, 4]


def test_locate_finds_evidence_despite_collapsed_whitespace_and_case():
    text = "The operators ran\n  schtasks /create   /tn Updater\non each host."

    start, end = locate("RAN schtasks /create /tn updater", text)

    assert js_slice(text, start, end) == "ran\n  schtasks /create   /tn Updater"


def test_locate_returns_none_when_the_quote_is_not_there():
    assert locate("never said this", "Some article text.") is None


@pytest.fixture
def article(db_session) -> Article:
    source = Source(slug="s", name="S", url="https://s.example.com/", rss_url="https://s.example.com/f",
                    active=True, check_frequency_seconds=3600, config={}, origin="user")
    db_session.add(source)
    db_session.flush()
    text = "Intro 🔥.\nThe operators ran  rundll32.exe   javascript: to start.\nThey also wrote HKLM run keys."
    item = Article(source_id=source.id, url="https://s.example.com/a", title="Lantern", content_text=text,
                   content_hash="h", word_count=20)
    db_session.add(item)
    db_session.commit()
    seed_profiles(db_session)
    score_articles(db_session, latest_version(db_session, "detection"))
    return item


@pytest.fixture
def api(test_settings, db_session):
    with TestClient(create_app(test_settings)) as client:
        yield client


def test_article_detail_includes_the_priority_breakdown_and_spans(api, article):
    body = api.get(f"/api/articles/{article.id}").json()

    priority = body["priority"]
    assert priority["threshold"] == 37.5 and priority["profile_version"] == 2
    assert priority["buckets"]["perfect"]["terms"] == ["hklm", "rundll32.exe"]
    assert priority["buckets"]["perfect"]["max_points"] == 75
    assert priority["score"] == pytest.approx(priority["buckets"]["perfect"]["points"] + priority["buckets"]["good"]["points"]
                                              + priority["buckets"]["lolbas"]["points"] + priority["buckets"]["intelligence"]["points"]
                                              - priority["buckets"]["negative"]["points"], abs=0.1)
    found = {js_slice(article.content_text, s["start"], s["end"]) for s in priority["spans"]}
    assert {"rundll32.exe", "HKLM"} <= found
    assert priority["spans_truncated"] is False and priority["stale"] is False
    assert priority["terms"]["perfect"] == ["hklm", "rundll32.exe"]  # the same chips as the list
    assert body["pipeline"] == {"status": "not_run", "run_id": None, "rules": 0}


def test_article_detail_reports_the_stored_score_not_a_recomputed_one(api, article, db_session):
    stored = db_session.query(ArticleScore).filter_by(article_id=article.id).one()
    stored.score, stored.scorer = 12.3, "regex-v0"
    stored.breakdown = {**stored.breakdown, "perfect": {"terms": ["hklm"], "points": 37.5}}
    db_session.commit()

    priority = api.get(f"/api/articles/{article.id}").json()["priority"]

    assert priority["score"] == 12.3 and priority["band"] == "Very Poor"
    assert priority["buckets"]["perfect"]["terms"] == ["hklm"]
    assert priority["stale"] is True


def test_article_detail_has_no_priority_until_the_article_is_scored(api, article, db_session):
    db_session.query(ArticleScore).delete()
    db_session.commit()

    assert api.get(f"/api/articles/{article.id}").json()["priority"] is None


def test_score_missing_refreshes_scores_from_an_older_scorer(article, db_session):
    stored = db_session.query(ArticleScore).filter_by(article_id=article.id).one()
    stored.score, stored.scorer = 12.3, "regex-v0"
    db_session.commit()

    assert score_articles(db_session, latest_version(db_session, "detection"), missing_only=True) == 1
    db_session.refresh(stored)
    assert stored.score != 12.3


def test_verified_observables_carry_an_evidence_span(api, article, db_session):
    version = latest_version(db_session, "detection")
    run = PipelineRun(article_id=article.id, profile_version_id=version.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    db_session.add_all([
        Observable(run_id=run.id, field_name="cmdline", value="rundll32.exe javascript:",
                   evidence="ran rundll32.exe javascript: to start", evidence_verified=True, tags={}),
        # Present in the text but unverified: only verified evidence is highlighted.
        Observable(run_id=run.id, field_name="cmdline", value="hklm", evidence="wrote HKLM run keys",
                   evidence_verified=False, tags={}),
    ])
    db_session.commit()

    observables = api.get(f"/api/runs/{run.id}").json()["observables"]

    span = observables[0]["evidence_span"]
    assert js_slice(article.content_text, span["start"], span["end"]) == "ran  rundll32.exe   javascript: to start"
    assert observables[1]["evidence_span"] is None
