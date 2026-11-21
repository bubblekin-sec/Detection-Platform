from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
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


JSONType = JSON().with_variant(JSONB(), "postgresql")
RUN_STATUSES = ("queued", "running", "filtered_out", "succeeded", "failed")
ACTIVE_RUN_CONSTRAINT = "uq_pipeline_runs_one_active"


class Profile(Base):
    """A team's view of the pipeline (detection first; vuln management, IR, leadership later)."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileVersion(Base):
    """Immutable: editing a profile creates a new version, so past runs keep the settings they used."""

    __tablename__ = "profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_profile_versions_profile_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profile: Mapped[Profile] = relationship()


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint(f"status IN {RUN_STATUSES}", name="ck_pipeline_runs_status"),
        # At most one queued/running run per article and profile version.
        Index(
            ACTIVE_RUN_CONSTRAINT, "article_id", "profile_version_id", unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_pipeline_runs_version_article", "profile_version_id", "article_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    profile_version_id: Mapped[int] = mapped_column(ForeignKey("profile_versions.id"))
    status: Mapped[str] = mapped_column(String(20), index=True)
    current_step: Mapped[str | None] = mapped_column(String(50))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last progress, for stall detection
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile_version: Mapped[ProfileVersion] = relationship()
    stages: Mapped[list["StageResult"]] = relationship(order_by="StageResult.id", cascade="all, delete-orphan")
    observables: Mapped[list["Observable"]] = relationship(order_by="Observable.id", cascade="all, delete-orphan")
    # Rules are records that outlive their run: no delete cascade from runs.
    rules: Mapped[list["DetectionRule"]] = relationship(order_by="DetectionRule.position")


class StageResult(Base):
    __tablename__ = "stage_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))  # passed | filtered | failed
    output: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    compiled_prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Observable(Base):
    """One extracted item (command line, registry key, ...) with the quote that supports it."""

    __tablename__ = "observables"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(50))
    value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    # False when the quote could not be found in the article: shown as a warning, excluded from rule generation.
    evidence_verified: Mapped[bool] = mapped_column()
    tags: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


RULE_STATUSES = ("pending_review", "approved", "rejected")


class DetectionRule(Base):
    """A Sigma rule of record. Its id is the Sigma `id`, stable across edits and exports."""

    __tablename__ = "detection_rules"
    __table_args__ = (CheckConstraint(f"status IN {RULE_STATUSES}", name="ck_detection_rules_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID text, also the Sigma id
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"), index=True)  # no cascade: rules are records
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order within the run (largest group first)
    group_key: Mapped[dict[str, Any]] = mapped_column(JSONType)  # {"platform": ..., "telemetry_category": ...}
    observable_ids: Mapped[list[int]] = mapped_column(JSONType, default=list)
    title: Mapped[str] = mapped_column(Text)
    level: Mapped[str | None] = mapped_column(String(20))
    sigma_yaml: Mapped[str] = mapped_column(Text)
    sigma_hash: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending_review", index=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    queries: Mapped[list["RuleQuery"]] = relationship(order_by="RuleQuery.id", cascade="all, delete-orphan")


class RuleQuery(Base):
    """A query artifact for one SIEM target. New targets or strategies add rows, not columns."""

    __tablename__ = "rule_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("detection_rules.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(50))  # defender_xdr; later sentinel, splunk
    origin: Mapped[str] = mapped_column(String(20))  # converted | llm | human_edit
    query_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # ok | failed
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSONType, default=list)
    source_sigma_hash: Mapped[str] = mapped_column(String(64))  # stale when it differs from the rule's sigma_hash
    llm_model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
