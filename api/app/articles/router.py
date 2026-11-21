from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer, joinedload

from app.db import get_session
from app.models import Article

router = APIRouter(prefix="/api/articles", tags=["articles"])

SessionDep = Annotated[Session, Depends(get_session)]
EXCERPT_LENGTH = 280


class SourceRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str


class ArticleSummary(BaseModel):
    id: int
    url: str
    title: str
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    word_count: int
    excerpt: str
    source: SourceRef


class ArticleDetail(ArticleSummary):
    content_text: str


def _summary_fields(article: Article, excerpt: str) -> dict:
    return {
        "id": article.id,
        "url": article.url,
        "title": article.title,
        "author": article.author,
        "published_at": article.published_at,
        "fetched_at": article.fetched_at,
        "word_count": article.word_count,
        "excerpt": excerpt,
        "source": SourceRef.model_validate(article.source),
    }


@router.get("", response_model=list[ArticleSummary])
def list_articles(
    session: SessionDep,
    source_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ArticleSummary]:
    # Full-content feeds can store very long text; the list only needs an excerpt, cut in the database.
    excerpt = func.substr(Article.content_text, 1, EXCERPT_LENGTH)
    query = select(Article, excerpt).options(joinedload(Article.source), defer(Article.content_text))
    if source_id is not None:
        query = query.where(Article.source_id == source_id)
    query = query.order_by(Article.published_at.desc().nulls_last(), Article.id.desc()).limit(limit)
    return [ArticleSummary(**_summary_fields(article, text)) for article, text in session.execute(query)]


@router.get("/{article_id}", response_model=ArticleDetail)
def get_article(article_id: int, session: SessionDep) -> ArticleDetail:
    article = session.get(Article, article_id, options=[joinedload(Article.source)])
    if article is None:
        raise HTTPException(status_code=404, detail={"errors": [f"Article {article_id} not found."]})
    return ArticleDetail(**_summary_fields(article, article.content_text[:EXCERPT_LENGTH]), content_text=article.content_text)
