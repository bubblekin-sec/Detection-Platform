import pytest
from sqlalchemy import func, select

from app.models import Source
from app.sources.seed import seed_sources

HUNTABLE_SOURCE_COUNT = 38
HUNTABLE_ACTIVE_RSS_COUNT = 28


def count_sources(session) -> int:
    return session.scalar(select(func.count()).select_from(Source))


def by_slug(session, slug: str) -> Source:
    return session.scalars(select(Source).where(Source.slug == slug)).one()


def test_seeding_twice_adds_each_source_once(db_session):
    first = seed_sources(db_session)
    second = seed_sources(db_session)

    assert first == {"added": HUNTABLE_SOURCE_COUNT, "existing": 0, "skipped_user_owned": []}
    assert second == {"added": 0, "existing": HUNTABLE_SOURCE_COUNT, "skipped_user_owned": []}
    assert count_sources(db_session) == HUNTABLE_SOURCE_COUNT


def test_only_upstream_active_rss_sources_start_active(db_session):
    seed_sources(db_session)

    assert by_slug(db_session, "cisco_talos").active is True
    assert by_slug(db_session, "splunk_security_blog").active is False  # scrape-only
    assert by_slug(db_session, "vmray_blog").active is False  # disabled upstream
    active = db_session.scalar(select(func.count()).select_from(Source).where(Source.active))
    assert active == HUNTABLE_ACTIVE_RSS_COUNT


def test_seed_preserves_scrape_config_and_origin(db_session):
    seed_sources(db_session)

    dfir = by_slug(db_session, "dfir_report")
    assert dfir.origin == "huntable_seed"
    assert dfir.config["archive_url_pattern"] == "https://thedfirreport.com/page/{page}/"
    assert dfir.config["lookback_days"] == 180
    assert dfir.config["upstream_active"] is True
    assert "discovery" in by_slug(db_session, "ncsc_uk").config


def test_reseeding_never_overwrites_an_edited_source(db_session):
    seed_sources(db_session)
    talos = by_slug(db_session, "cisco_talos")
    talos.name = "Talos (renamed by me)"
    talos.active = False
    db_session.commit()

    seed_sources(db_session)
    db_session.expire_all()

    talos = by_slug(db_session, "cisco_talos")
    assert (talos.name, talos.active) == ("Talos (renamed by me)", False)


def test_list_sources(db_client, db_session):
    seed_sources(db_session)

    response = db_client.get("/api/sources")

    assert response.status_code == 200
    sources = response.json()
    assert len(sources) == HUNTABLE_SOURCE_COUNT
    talos = next(source for source in sources if source["slug"] == "cisco_talos")
    assert talos["rss_url"].startswith("https://")
    assert talos["origin"] == "huntable_seed"


def test_add_rss_source(db_client, db_session):
    response = db_client.post(
        "/api/sources",
        json={"name": "Example Threat Blog", "url": "https://blog.example.com/", "rss_url": "https://blog.example.com/feed"},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["slug"] == "example_threat_blog"
    assert (created["origin"], created["active"]) == ("user", True)
    assert seed_sources(db_session)["added"] == HUNTABLE_SOURCE_COUNT  # seed leaves user rows alone
    listed = db_client.get("/api/sources").json()
    assert any(source["slug"] == "example_threat_blog" for source in listed)


def test_add_source_rejects_non_http_urls(db_client):
    for bad in ["ftp://example.com/feed", "javascript:alert(1)", "file:///etc/passwd", "not a url"]:
        response = db_client.post("/api/sources", json={"name": "Bad", "url": "https://ok.example.com", "rss_url": bad})

        assert response.status_code == 422, bad
        errors = response.json()["detail"]["errors"]
        assert any(error.startswith("rss_url") for error in errors), errors


def test_add_source_with_taken_name_conflicts(db_client, db_session):
    seed_sources(db_session)

    response = db_client.post(
        "/api/sources", json={"name": "Cisco Talos", "url": "https://x.example.com", "rss_url": "https://x.example.com/rss"}
    )

    assert response.status_code == 409
    assert "cisco_talos" in response.json()["detail"]["errors"][0]


def test_seed_maps_frequency_and_missing_rss(db_session):
    seed_sources(db_session)

    assert by_slug(db_session, "cisco_talos").check_frequency_seconds == 1800
    splunk = by_slug(db_session, "splunk_security_blog")
    assert splunk.rss_url is None
    assert splunk.config["upstream_active"] is True


def test_seed_skips_and_reports_a_slug_a_user_already_owns(db_client, db_session):
    db_client.post(
        "/api/sources", json={"name": "Cisco Talos", "url": "https://mine.example.com", "rss_url": "https://mine.example.com/rss"}
    )

    result = seed_sources(db_session)

    assert result["added"] == HUNTABLE_SOURCE_COUNT - 1
    assert result["skipped_user_owned"] == ["cisco_talos"]
    talos = by_slug(db_session, "cisco_talos")
    assert (talos.origin, talos.url) == ("user", "https://mine.example.com/")


def test_list_sources_is_ordered_by_name(db_client, db_session):
    for name in ["Zeta Feed", "alpha feed", "Mid Feed"]:
        db_client.post("/api/sources", json={"name": name, "url": "https://x.example.com", "rss_url": "https://x.example.com/rss"})

    names = [source["name"] for source in db_client.get("/api/sources").json()]

    assert names == list(db_session.scalars(select(Source.name).order_by(Source.name)))
    assert names != ["Zeta Feed", "alpha feed", "Mid Feed"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        # Normalization percent-encodes '<' to '%3C', tripling the stored length past the column limit.
        ({"rss_url": "https://a.example/" + "<" * 1000}, "rss_url"),
        ({"name": "a\u0000b"}, "name"),
        ({"description": "bad\u0000"}, "description"),
    ],
    ids=["url-grows-on-normalization", "nul-in-name", "nul-in-description"],
)
def test_add_source_rejects_values_postgres_cannot_store(db_client, payload, field):
    body = {"name": "Fine", "url": "https://ok.example.com", "rss_url": "https://ok.example.com/rss"} | payload

    response = db_client.post("/api/sources", json=body)

    assert response.status_code == 422
    assert any(error.startswith(field) for error in response.json()["detail"]["errors"])


def seeded(db_session, slug: str) -> Source:
    seed_sources(db_session)
    return by_slug(db_session, slug)


def test_patch_toggles_active(db_client, db_session):
    talos = seeded(db_session, "cisco_talos")

    response = db_client.patch(f"/api/sources/{talos.id}", json={"active": False})

    assert response.status_code == 200
    assert response.json()["active"] is False
    db_session.expire_all()
    assert by_slug(db_session, "cisco_talos").active is False


def test_patch_edits_only_the_fields_sent_and_keeps_the_slug(db_client, db_session):
    talos = seeded(db_session, "cisco_talos")

    response = db_client.patch(f"/api/sources/{talos.id}", json={"name": "Talos Intelligence", "check_frequency_seconds": 900})

    body = response.json()
    assert response.status_code == 200
    assert (body["name"], body["slug"], body["check_frequency_seconds"]) == ("Talos Intelligence", "cisco_talos", 900)
    assert body["rss_url"] == talos.rss_url
    assert body["config"] == talos.config


def test_patch_rejects_activating_a_source_without_rss(db_client, db_session):
    splunk = seeded(db_session, "splunk_security_blog")

    response = db_client.patch(f"/api/sources/{splunk.id}", json={"active": True})

    assert response.status_code == 422
    assert any(error.startswith("rss_url") for error in response.json()["detail"]["errors"])


def test_patch_can_add_a_feed_and_activate_in_one_request(db_client, db_session):
    splunk = seeded(db_session, "splunk_security_blog")

    response = db_client.patch(
        f"/api/sources/{splunk.id}", json={"rss_url": "https://www.splunk.com/en_us/blog/feed.xml", "active": True}
    )

    assert response.status_code == 200
    assert response.json()["active"] is True


@pytest.mark.parametrize(
    "payload",
    [{"rss_url": "ftp://x.example.com"}, {"name": ""}, {"name": "a\u0000"}, {"slug": "renamed"}, {"origin": "user"}, {"active": None}],
    ids=["bad-scheme", "empty-name", "nul", "slug-is-read-only", "origin-is-read-only", "null-active"],
)
def test_patch_rejects_invalid_or_read_only_fields(db_client, db_session, payload):
    talos = seeded(db_session, "cisco_talos")
    before = (talos.name, talos.slug, talos.rss_url, talos.active, talos.origin)

    response = db_client.patch(f"/api/sources/{talos.id}", json=payload)

    assert response.status_code == 422
    db_session.expire_all()
    talos = by_slug(db_session, "cisco_talos")
    assert (talos.name, talos.slug, talos.rss_url, talos.active, talos.origin) == before


def test_patch_unknown_source_is_404(db_client, db_session):
    response = db_client.patch("/api/sources/999999", json={"active": False})

    assert response.status_code == 404
    assert response.json() == {"detail": {"errors": ["Source 999999 not found."]}}


@pytest.mark.parametrize("field", ["name", "url", "check_frequency_seconds"])
def test_patch_rejects_explicit_null_for_required_fields(db_client, db_session, field):
    talos = seeded(db_session, "cisco_talos")

    response = db_client.patch(f"/api/sources/{talos.id}", json={field: None})

    assert response.status_code == 422
    assert any(error.startswith(field) for error in response.json()["detail"]["errors"])


def test_patch_cannot_remove_the_feed_of_an_active_source(db_client, db_session):
    talos = seeded(db_session, "cisco_talos")
    original_name = talos.name

    response = db_client.patch(f"/api/sources/{talos.id}", json={"name": "Renamed", "rss_url": None})

    assert response.status_code == 422
    assert response.json() == {"detail": {"errors": ["rss_url: an active source needs an RSS feed URL"]}}
    db_session.expire_all()
    talos = by_slug(db_session, "cisco_talos")
    assert (talos.name, talos.rss_url is not None) == (original_name, True)


def test_database_enforces_active_sources_have_a_feed(db_session):
    from sqlalchemy.exc import IntegrityError

    talos = seeded(db_session, "cisco_talos")
    talos.rss_url = None

    with pytest.raises(IntegrityError, match="ck_sources_active_needs_rss"):
        db_session.commit()
    db_session.rollback()


def test_validator_messages_have_no_pydantic_prefix(db_client):
    response = db_client.post("/api/sources", json={"name": "!!!", "url": "https://x.example.com", "rss_url": "https://x.example.com/f"})

    assert response.json()["detail"]["errors"] == ["name: must contain at least one letter or digit"]


def test_create_still_ignores_unknown_fields(db_client, db_session):
    response = db_client.post(
        "/api/sources",
        json={"name": "Extra Fields", "url": "https://x.example.com", "rss_url": "https://x.example.com/f", "active": False},
    )

    assert response.status_code == 201
    assert response.json()["active"] is True


def test_seed_applies_documented_url_fixes_on_top_of_the_vendored_list(db_session):
    seed_sources(db_session)

    assert by_slug(db_session, "elastic_security_labs").rss_url == "https://www.elastic.co/security-labs/rss/feed.xml"
    sekoia = by_slug(db_session, "sekoia_io_blog")
    assert (sekoia.url, sekoia.rss_url) == ("https://www.sekoia.com/blog", "https://www.sekoia.com/blog/rss.xml")
    assert by_slug(db_session, "cisco_talos").rss_url.startswith("https://blog.talosintelligence.com")
