"""Rule review: list, approve or reject, edit Sigma (revalidated, KQL regenerated), add hand-written KQL."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import get_session
from app.detection.lint import lint_kql
from app.detection.rules import identity_error, pin_platform_fields
from app.detection.sigma import parse_sigma
from app.models import RULE_STATUSES, Article, DetectionRule, RuleQuery
from app.pipeline.rule_generation import converted_query, rule_level, sigma_digest
from app.rules.duplicates import release_duplicates_of
from app.rules.export import export_rules
from app.runs.router import RuleOut, TelemetryDep, rule_out

router = APIRouter(prefix="/api/rules", tags=["rules"])
SessionDep = Annotated[Session, Depends(get_session)]
RuleStatus = Literal["pending_review", "approved", "rejected"]
MAX_TEXT = 64 * 1024


class ArticleRef(BaseModel):
    id: int
    title: str
    url: str


class RuleDetail(RuleOut):
    reviewer_note: str | None
    reviewed_at: datetime | None
    created_at: datetime
    article: ArticleRef


class RuleUpdate(BaseModel):
    """Partial update. Omitted fields are unchanged; an explicit null is rejected except for reviewer_note."""

    model_config = ConfigDict(extra="forbid")

    status: RuleStatus = Field(default=None)  # type: ignore[assignment]
    reviewer_note: str | None = Field(default=None, max_length=5000)
    sigma_yaml: str = Field(default=None, min_length=1, max_length=MAX_TEXT)  # type: ignore[assignment]
    kql: str = Field(default=None, min_length=1, max_length=MAX_TEXT)  # type: ignore[assignment]
    # The sigma_hash the reviewer was looking at. If the rule changed since, the update is refused (409).
    expected_sigma_hash: str = Field(default=None, min_length=64, max_length=64)  # type: ignore[assignment]

    @field_validator("sigma_yaml", "kql")
    @classmethod
    def not_blank_and_within_bytes(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        if len(value.encode()) > MAX_TEXT:
            raise ValueError(f"must be at most {MAX_TEXT} bytes")
        return value

    @model_validator(mode="after")
    def something_to_change(self) -> "RuleUpdate":
        if not self.model_fields_set - {"expected_sigma_hash"}:
            raise ValueError("send at least one of status, reviewer_note, sigma_yaml, kql")
        return self


def _detail(rule: DetectionRule, article: Article, inventory: frozenset[str]) -> RuleDetail:
    return RuleDetail(
        **rule_out(rule, inventory).model_dump(),
        reviewer_note=rule.reviewer_note,
        reviewed_at=rule.reviewed_at,
        created_at=rule.created_at,
        article=ArticleRef(id=article.id, title=article.title, url=article.url),
    )


def _load(session: Session, rule_id: str, *, lock: bool = False) -> DetectionRule:
    query = select(DetectionRule).where(DetectionRule.id == rule_id).options(selectinload(DetectionRule.queries))
    if lock:
        query = query.with_for_update(of=DetectionRule)
    rule = session.scalars(query.execution_options(populate_existing=True)).first()
    if rule is None:
        raise HTTPException(status_code=404, detail={"errors": ["Rule not found."]})
    return rule


@router.get("", response_model=list[RuleDetail])
def list_rules(
    session: SessionDep,
    inventory: TelemetryDep,
    status: RuleStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[RuleDetail]:
    query = select(DetectionRule, Article).join(Article, Article.id == DetectionRule.article_id)
    query = query.options(selectinload(DetectionRule.queries))
    if status is not None:
        query = query.where(DetectionRule.status == status)
    query = query.order_by(DetectionRule.created_at.desc(), DetectionRule.run_id.desc(), DetectionRule.position).limit(limit)
    return [_detail(rule, article, inventory) for rule, article in session.execute(query)]


@router.get("/counts")
def rule_counts(session: SessionDep) -> dict[str, int]:
    """Rules per review status (declared before /{rule_id} so "counts" is not read as an id)."""
    counts = dict(session.execute(select(DetectionRule.status, func.count()).group_by(DetectionRule.status)).all())
    return {status: counts.get(status, 0) for status in RULE_STATUSES}


@router.get("/export", response_class=Response, responses={200: {"content": {"application/yaml": {}}}})
def export_approved_rules(session: SessionDep, status: Literal["approved"] = "approved") -> Response:
    """Approved rules as one multi-document Sigma YAML file (declared before /{rule_id})."""
    query = select(DetectionRule, Article.url).join(Article, Article.id == DetectionRule.article_id)
    query = query.where(DetectionRule.status == status)
    query = query.order_by(DetectionRule.created_at, DetectionRule.run_id, DetectionRule.position, DetectionRule.id)
    return Response(
        content=export_rules(session.execute(query)).text(),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="approved-rules.yaml"'},
    )


@router.get("/{rule_id}", response_model=RuleDetail)
def get_rule(rule_id: str, session: SessionDep, inventory: TelemetryDep) -> RuleDetail:
    rule = _load(session, rule_id)
    return _detail(rule, session.get(Article, rule.article_id), inventory)


@router.patch("/{rule_id}", response_model=RuleDetail)
def update_rule(rule_id: str, body: RuleUpdate, session: SessionDep, inventory: TelemetryDep) -> RuleDetail:
    rule = _load(session, rule_id, lock=True)
    changes = body.model_fields_set
    now = datetime.now(UTC)
    if "expected_sigma_hash" in changes and body.expected_sigma_hash != rule.sigma_hash:
        session.rollback()
        raise HTTPException(
            status_code=409, detail={"errors": ["The rule changed since you loaded it. Reload and review it again."]}
        )
    content_changed = False

    if "sigma_yaml" in changes:
        article = session.get(Article, rule.article_id)
        sigma = pin_platform_fields(body.sigma_yaml, rule_id=rule.id, reference_url=article.url)
        if sigma != rule.sigma_yaml:  # saving unchanged text is a no-op
            parsed = parse_sigma(sigma)
            errors = parsed.errors if parsed.rule is None else [e for e in [identity_error(parsed.rule, rule.id, sigma)] if e]
            if errors:
                session.rollback()
                raise HTTPException(status_code=422, detail={"errors": [f"sigma_yaml: {error}" for error in errors]})
            rule.sigma_yaml, rule.sigma_hash = sigma, sigma_digest(sigma)
            rule.title, rule.level = parsed.rule.title or rule.title, rule_level(parsed.rule)
            # The converted artifact always follows the Sigma; model-written and hand-written ones become stale.
            rule.queries = [query for query in rule.queries if query.origin != "converted"]
            rule.queries.append(converted_query(parsed.rule, sigma, rule.sigma_hash))
            content_changed = True

    if "kql" in changes:
        rule.queries = [query for query in rule.queries if not (query.origin == "human_edit" and query.target == "defender_xdr")]
        rule.queries.append(RuleQuery(
            target="defender_xdr", origin="human_edit", query_text=body.kql, status="ok",
            warnings=lint_kql(body.kql, sigma_yaml=rule.sigma_yaml), source_sigma_hash=rule.sigma_hash,
        ))
        content_changed = True  # the hand-written query is what would be deployed

    if content_changed and "status" not in changes:
        rule.status, rule.reviewed_at = "pending_review", None  # changed content needs review again

    if "status" in changes:
        rule.status = body.status
        rule.reviewed_at = now if body.status != "pending_review" else None
        if body.status == "rejected":
            release_duplicates_of(session, rule.id)
    if "reviewer_note" in changes:
        rule.reviewer_note = body.reviewer_note

    session.commit()
    rule = _load(session, rule_id)
    return _detail(rule, session.get(Article, rule.article_id), inventory)


assert set(RuleStatus.__args__) == set(RULE_STATUSES)  # keep the API and the DB constraint in step
