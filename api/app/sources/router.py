import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import SOURCE_SLUG_CONSTRAINT, Source
from app.sources.fetch import Fetcher
from app.sources.ingest import SourceFetchFailed, ingest_source

router = APIRouter(prefix="/api/sources", tags=["sources"])

SessionDep = Annotated[Session, Depends(get_session)]


def get_fetcher(request: Request) -> Fetcher:
    return request.app.state.fetcher


FetcherDep = Annotated[Fetcher, Depends(get_fetcher)]


class FetchSummary(BaseModel):
    new: int
    duplicates: int
    too_short: int
    filtered: int


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    url: str
    rss_url: str | None
    active: bool
    check_frequency_seconds: int
    description: str | None
    origin: str
    config: dict[str, Any]
    last_checked_at: datetime | None
    last_error: str | None


MAX_URL_LENGTH = 2048  # matches the sources.url / rss_url columns


def _no_nul(value: str | None) -> str | None:
    if value is not None and "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


def _stored_url_fits(value: HttpUrl | None) -> HttpUrl | None:
    # Normalization (percent-encoding, punycode) can make the stored form longer than the input.
    if value is not None and len(str(value)) > MAX_URL_LENGTH:
        raise ValueError(f"must be at most {MAX_URL_LENGTH} characters after normalization")
    return value


def _name_has_a_slug(name: str) -> str:
    if not slugify(name):
        raise ValueError("must contain at least one letter or digit")
    return name.strip()


class SourceFieldRules(BaseModel):
    """Validation shared by create and update. HttpUrl accepts only http and https."""

    _text_has_no_nul = field_validator("name", "description", check_fields=False)(_no_nul)
    _urls_fit = field_validator("url", "rss_url", check_fields=False)(_stored_url_fits)
    _name_slug = field_validator("name", check_fields=False)(_name_has_a_slug)


class SourceCreate(SourceFieldRules):
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl = Field(max_length=MAX_URL_LENGTH)
    rss_url: HttpUrl = Field(max_length=MAX_URL_LENGTH)
    description: str | None = Field(default=None, max_length=2000)
    check_frequency_seconds: int = Field(default=3600, ge=300, le=7 * 86400)


class SourceUpdate(SourceFieldRules):
    """Partial update: only fields present in the request change. Defaults are never validated or applied,
    so an explicit null is rejected for fields that cannot be empty, while omitting them leaves them unchanged.
    Unknown or read-only fields (slug, origin) are rejected rather than silently ignored."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]
    url: HttpUrl = Field(default=None, max_length=MAX_URL_LENGTH)  # type: ignore[assignment]
    rss_url: HttpUrl | None = Field(default=None, max_length=MAX_URL_LENGTH)
    description: str | None = Field(default=None, max_length=2000)
    active: bool = Field(default=None)  # type: ignore[assignment]
    check_frequency_seconds: int = Field(default=None, ge=300, le=7 * 86400)  # type: ignore[assignment]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:100]


@router.get("", response_model=list[SourceOut])
def list_sources(session: SessionDep) -> list[Source]:
    return list(session.scalars(select(Source).order_by(Source.name)))


@router.post("", response_model=SourceOut, status_code=201)
def create_source(body: SourceCreate, session: SessionDep) -> Source:
    slug = slugify(body.name)
    source = Source(
        slug=slug,
        name=body.name,
        url=str(body.url),
        rss_url=str(body.rss_url),
        description=body.description,
        check_frequency_seconds=body.check_frequency_seconds,
        active=True,
        config={},
        origin="user",
    )
    session.add(source)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if violated_constraint(error) != SOURCE_SLUG_CONSTRAINT:
            raise
        raise HTTPException(status_code=409, detail={"errors": [f"A source with slug '{slug}' already exists."]})
    return source


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, body: SourceUpdate, session: SessionDep) -> Source:
    # Row lock: the active/rss_url rule spans two columns, so concurrent PATCHes must not interleave.
    source = session.get(Source, source_id, with_for_update=True)
    if source is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Source {source_id} not found."]})
    # The slug is a stable identifier and stays the same when a source is renamed.
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(source, field, str(value) if field in ("url", "rss_url") and value is not None else value)
    if source.active and not source.rss_url:
        session.rollback()
        # The MVP fetches RSS only, so an active source must have a feed.
        raise HTTPException(status_code=422, detail={"errors": ["rss_url: an active source needs an RSS feed URL"]})
    session.commit()
    return source


@router.post("/{source_id}/fetch", response_model=FetchSummary)
def fetch_source(source_id: int, session: SessionDep, fetcher: FetcherDep) -> dict[str, int]:
    """Fetch the feed now (works for inactive sources too, as a manual check)."""
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Source {source_id} not found."]})
    if not source.rss_url:
        raise HTTPException(status_code=422, detail={"errors": ["rss_url: this source has no RSS feed to fetch"]})
    try:
        return ingest_source(session, source, fetcher).as_dict()
    except SourceFetchFailed as error:
        raise HTTPException(status_code=502, detail={"errors": [str(error)]})


def violated_constraint(error: IntegrityError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
