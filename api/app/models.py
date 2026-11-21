from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

SOURCE_SLUG_CONSTRAINT = "uq_sources_slug"


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("origin IN ('huntable_seed', 'user')", name="ck_sources_origin"),
        UniqueConstraint("slug", name=SOURCE_SLUG_CONSTRAINT),
        # The MVP fetches RSS only; the database enforces this even if two requests race past the API check.
        CheckConstraint("NOT active OR rss_url IS NOT NULL", name="ck_sources_active_needs_rss"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2048))
    rss_url: Mapped[str | None] = mapped_column(String(2048))
    active: Mapped[bool] = mapped_column(default=True)
    check_frequency_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    description: Mapped[str | None] = mapped_column(Text)
    # Fetch/scrape settings kept verbatim (Huntable's allow, robots, extract, discovery, ...) for later increments.
    config: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
    origin: Mapped[str] = mapped_column(String(20))  # "huntable_seed" | "user"
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Article(Base):
    """One ingested document. Shared by every profile; runs (increment 4) reference it."""

    __tablename__ = "articles"
    __table_args__ = (
        # Both are unique so concurrent fetches can insert with ON CONFLICT DO NOTHING instead of racing.
        UniqueConstraint("url", name="uq_articles_url"),
        UniqueConstraint("content_hash", name="uq_articles_content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))  # canonical: no fragment or tracking parameters
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    content_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 of whitespace-normalized content_text
    word_count: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[Source] = relationship()
