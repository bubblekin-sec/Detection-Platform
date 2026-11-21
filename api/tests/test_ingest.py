from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import Article, Source
from app.sources.fetch import BlockedDestination
from app.sources.ingest import ingest_source
from tests.fakes import FakeFetcher, fixture_bytes

FEED_URL = "https://research.example.com/feed"
HERON_URL = "https://research.example.com/2026/11/quiet-heron"
LANTERN_URL = "https://research.example.com/2026/11/paper-lantern"

RESEARCH_CONFIG = {
    "min_content_length": 500,
    "title_filter_keywords": ["webinar", "careers"],
    "extract": {"body_selectors": [".post-content"]},
}


@pytest.fixture
def fake_fetcher() -> FakeFetcher:
    fetcher = FakeFetcher()
    fetcher.serve(FEED_URL, fixture_bytes("research_blog.rss.xml"))
    fetcher.serve(HERON_URL, fixture_bytes("quiet_heron.html"), content_type="text/html")
    return fetcher


@pytest.fixture
def ingest_client(test_settings, db_session, fake_fetcher):
    with TestClient(create_app(test_settings, fetcher=fake_fetcher)) as client:
        yield client


def add_source(db_session, *, slug="example_research", rss_url=FEED_URL, config=None) -> Source:
    source = Source(
        slug=slug,
        name=slug.replace("_", " ").title(),
        url="https://research.example.com/",
        rss_url=rss_url,
        active=rss_url is not None,
        check_frequency_seconds=3600,
        config=RESEARCH_CONFIG if config is None else config,
        origin="user",
    )
    db_session.add(source)
    db_session.commit()
    return source


def article_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Article))


def test_fetch_now_stores_full_articles_and_filters_junk(ingest_client, db_session, fake_fetcher):
    source = add_source(db_session)

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 200
    assert response.json() == {"new": 2, "duplicates": 0, "too_short": 0, "filtered": 1}
    # The teaser-only item's page was fetched; the long item's page was not.
    assert fake_fetcher.requested == [FEED_URL, HERON_URL]

    articles = {article["url"]: article for article in ingest_client.get("/api/articles").json()}
    assert set(articles) == {LANTERN_URL, HERON_URL}  # tracking params and fragment removed
    lantern = ingest_client.get(f"/api/articles/{articles[LANTERN_URL]['id']}").json()
    assert "schtasks /create /tn Updater" in lantern["content_text"]
    assert "alert(" not in lantern["content_text"]
    assert lantern["author"] == "Research Team"
    assert lantern["published_at"].startswith("2026-11-17T09:00:00")
    assert lantern["source"] == {"id": source.id, "slug": "example_research", "name": "Example Research"}
    heron = ingest_client.get(f"/api/articles/{articles[HERON_URL]['id']}").json()
    assert "sc.exe create HeronUpdate" in heron["content_text"]
    assert "Careers" not in heron["content_text"] and "newsletter" not in heron["content_text"]

    db_session.expire_all()
    refreshed = db_session.get(Source, source.id)
    assert refreshed.last_checked_at is not None and refreshed.last_error is None


def test_fetching_again_reports_duplicates(ingest_client, db_session, fake_fetcher):
    source = add_source(db_session)
    ingest_client.post(f"/api/sources/{source.id}/fetch")

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.json() == {"new": 0, "duplicates": 2, "too_short": 0, "filtered": 1}
    assert article_count(db_session) == 2
    assert fake_fetcher.requested[-1:] == [FEED_URL]  # known URLs never trigger a page fetch


def test_rss_only_source_does_not_fetch_pages_and_counts_teasers_as_too_short(ingest_client, db_session, fake_fetcher):
    source = add_source(db_session, config=RESEARCH_CONFIG | {"rss_only": True})

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.json() == {"new": 1, "duplicates": 0, "too_short": 1, "filtered": 1}
    assert fake_fetcher.requested == [FEED_URL]


def test_a_failed_article_page_counts_as_too_short_without_failing_the_fetch(ingest_client, db_session, fake_fetcher):
    fake_fetcher.fail(HERON_URL, BlockedDestination("research.example.com resolves to a private or reserved address"))
    source = add_source(db_session)

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 200
    assert response.json()["too_short"] == 1


def test_same_content_under_a_different_url_is_a_duplicate(ingest_client, db_session, fake_fetcher):
    mirror_feed = "https://mirror.example.org/feed"
    fake_fetcher.serve(
        mirror_feed,
        fixture_bytes("research_blog.rss.xml").replace(b"https://research.example.com/2026/11/paper-lantern", b"https://mirror.example.org/lantern"),
    )
    first = add_source(db_session)
    mirror = add_source(db_session, slug="mirror", rss_url=mirror_feed, config=RESEARCH_CONFIG | {"rss_only": True})
    ingest_client.post(f"/api/sources/{first.id}/fetch")

    response = ingest_client.post(f"/api/sources/{mirror.id}/fetch")

    # Lantern: new URL, same text (content hash). Heron: same URL as the first source.
    assert response.json() == {"new": 0, "duplicates": 2, "too_short": 0, "filtered": 1}
    assert article_count(db_session) == 2


@pytest.mark.parametrize(
    ("route", "message"),
    [
        ({"status": 404, "body": b"not found"}, "HTTP 404"),
        ({"status": 200, "body": fixture_bytes("not_a_feed.html"), "content_type": "text/html"}, "not a valid RSS or Atom feed"),
    ],
    ids=["http-404", "not-a-feed"],
)
def test_feed_failure_records_last_error_and_stores_nothing(ingest_client, db_session, fake_fetcher, route, message):
    fake_fetcher.serve(FEED_URL, route["body"], status=route["status"], content_type=route.get("content_type", "text/plain"))
    source = add_source(db_session)

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 502
    assert message in response.json()["detail"]["errors"][0]
    assert article_count(db_session) == 0
    db_session.expire_all()
    assert message in db_session.get(Source, source.id).last_error


def test_blocked_feed_url_is_reported(ingest_client, db_session, fake_fetcher):
    fake_fetcher.fail(FEED_URL, BlockedDestination("research.example.com resolves to a private or reserved address and will not be fetched."))
    source = add_source(db_session)

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 502
    assert "private or reserved" in response.json()["detail"]["errors"][0]


def test_a_successful_fetch_clears_a_previous_error(ingest_client, db_session):
    source = add_source(db_session)
    source.last_error = "Feed returned HTTP 500"
    db_session.commit()

    ingest_client.post(f"/api/sources/{source.id}/fetch")

    db_session.expire_all()
    assert db_session.get(Source, source.id).last_error is None


def test_source_without_rss_cannot_be_fetched(ingest_client, db_session):
    source = add_source(db_session, rss_url=None)

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 422
    assert response.json()["detail"]["errors"]


def test_unknown_source_is_404(ingest_client, db_session):
    assert ingest_client.post("/api/sources/999999/fetch").status_code == 404


def test_lookback_days_skips_older_items(db_session, fake_fetcher):
    source = add_source(db_session, config=RESEARCH_CONFIG | {"lookback_days": 1})

    # Cutoff is Nov 17 08:00: Lantern (Nov 17 09:00) is inside it; Heron (Nov 16) and the webinar (Nov 15) are not.
    result = ingest_source(db_session, source, fake_fetcher, now=datetime(2026, 11, 18, 8, 0, tzinfo=UTC))

    assert (result.new, result.filtered) == (1, 2)
    assert fake_fetcher.requested == [FEED_URL]


def test_articles_list_filters_by_source_and_is_newest_first(ingest_client, db_session):
    source = add_source(db_session)
    ingest_client.post(f"/api/sources/{source.id}/fetch")

    listed = ingest_client.get(f"/api/articles?source_id={source.id}").json()

    assert [article["url"] for article in listed] == [LANTERN_URL, HERON_URL]
    assert "content_text" not in listed[0]
    assert listed[0]["excerpt"].startswith("The operators executed")
    assert listed[0]["word_count"] > 50
    assert ingest_client.get("/api/articles?source_id=999999").json() == []


def test_only_the_newest_items_are_processed_per_fetch(ingest_client, db_session, fake_fetcher):
    source = add_source(db_session, config=RESEARCH_CONFIG | {"max_items_per_fetch": 1})

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    # Three items have links; only the newest (Paper Lantern, Nov 17) is processed. The link-less item never counts.
    assert response.json() == {"new": 1, "duplicates": 0, "too_short": 0, "filtered": 2}
    assert fake_fetcher.requested == [FEED_URL]



def test_a_malformed_item_link_is_skipped_not_fatal(ingest_client, db_session, fake_fetcher):
    feed = fixture_bytes("research_blog.rss.xml").replace(
        b"<link>https://research.example.com/events/webinar-101</link>", b"<link>http://[broken/x</link>"
    )
    fake_fetcher.serve(FEED_URL, feed)
    source = add_source(db_session, config=RESEARCH_CONFIG | {"title_filter_keywords": []})

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.status_code == 200
    assert response.json() == {"new": 2, "duplicates": 0, "too_short": 0, "filtered": 1}


def test_nul_bytes_in_a_page_are_stripped(ingest_client, db_session, fake_fetcher):
    fake_fetcher.serve(HERON_URL, fixture_bytes("quiet_heron.html").replace(b"Quiet Heron is", b"Quiet\x00 Heron is"), content_type="text/html")
    source = add_source(db_session)

    response = ingest_client.post(f"/api/sources/{source.id}/fetch")

    assert response.json()["new"] == 2
    heron = db_session.scalars(select(Article).where(Article.url == HERON_URL)).one()
    assert "\x00" not in heron.content_text


def test_no_transaction_or_row_lock_is_held_while_fetching_pages(test_settings, db_session, fake_fetcher, migrated_engine):
    from sqlalchemy import text

    source = add_source(db_session)
    lock_free_during_page_fetch = []
    original = fake_fetcher.fetch

    def fetch_and_probe(url, **kwargs):
        if url == HERON_URL:
            with migrated_engine.connect() as other:
                other.execute(text("SET lock_timeout = '1s'"))
                other.execute(text("SELECT id FROM sources WHERE id = :id FOR UPDATE NOWAIT"), {"id": source.id})
                lock_free_during_page_fetch.append(True)
                other.rollback()
        return original(url, **kwargs)

    fake_fetcher.fetch = fetch_and_probe
    with TestClient(create_app(test_settings, fetcher=fake_fetcher)) as client:
        assert client.post(f"/api/sources/{source.id}/fetch").status_code == 200

    assert lock_free_during_page_fetch == [True]


def test_future_publication_dates_are_clamped_to_fetch_time(db_session, fake_fetcher):
    fake_fetcher.serve(FEED_URL, fixture_bytes("research_blog.rss.xml").replace(b"Tue, 17 Nov 2026", b"Tue, 17 Nov 2099"))
    source = add_source(db_session)
    now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

    ingest_source(db_session, source, fake_fetcher, now=now)

    lantern = db_session.scalars(select(Article).where(Article.url == LANTERN_URL)).one()
    assert lantern.published_at == now


def test_article_detail_404_and_limit_bounds(ingest_client, db_session):
    assert ingest_client.get("/api/articles/999999").json() == {"detail": {"errors": ["Article 999999 not found."]}}
    assert ingest_client.get("/api/articles?limit=0").status_code == 422
    assert ingest_client.get("/api/articles?limit=201").status_code == 422
