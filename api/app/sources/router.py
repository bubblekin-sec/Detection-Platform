import re
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import SOURCE_SLUG_CONSTRAINT, Source

router = APIRouter(prefix="/api/sources", tags=["sources"])

SessionDep = Annotated[Session, Depends(get_session)]


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


def _stored_url_fits(value: HttpUrl) -> HttpUrl:
    # Normalization (percent-encoding, punycode) can make the stored form longer than the input.
    if len(str(value)) > MAX_URL_LENGTH:
        raise ValueError(f"must be at most {MAX_URL_LENGTH} characters after normalization")
    return value


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # HttpUrl accepts only http and https.
    url: HttpUrl = Field(max_length=MAX_URL_LENGTH)
    rss_url: HttpUrl = Field(max_length=MAX_URL_LENGTH)
    description: str | None = Field(default=None, max_length=2000)
    check_frequency_seconds: int = Field(default=3600, ge=300, le=7 * 86400)

    _text_has_no_nul = field_validator("name", "description")(_no_nul)
    _urls_fit = field_validator("url", "rss_url")(_stored_url_fits)

    @field_validator("name")
    @classmethod
    def name_has_a_slug(cls, name: str) -> str:
        if not slugify(name):
            raise ValueError("must contain at least one letter or digit")
        return name.strip()


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


def violated_constraint(error: IntegrityError) -> str | None:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
