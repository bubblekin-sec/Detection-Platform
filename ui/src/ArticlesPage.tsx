import { useEffect, useRef, useState, type MouseEvent } from 'react'

import {
  describeError,
  getArticle,
  listArticles,
  listSources,
  type ArticleDetail,
  type ArticleSummary,
  type Band,
  type PipelineState,
  type Source,
} from './api'
import { BANDS, PAGE_SIZE, SORTS, STATUSES, parseArticleQuery, toApiQuery, toSearch, type ArticleQuery } from './articleQuery'
import { KeywordChips, PipelineBadge, ScoreBadge } from './badges'
import { PIPELINE_LABELS } from './labels'
import { followLink } from './routes'
import { RunPanel } from './RunPanel'

const DATE_FORMAT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

function formatDate(value: string | null): string | null {
  return value ? DATE_FORMAT.format(new Date(value)) : null
}

function Meta({ article }: { article: Pick<ArticleSummary, 'source' | 'published_at' | 'author' | 'word_count'> }) {
  const published = formatDate(article.published_at)
  return (
    <p className="article-meta">
      <span className="tag tag-user">{article.source.name}</span>
      {published && <span>{published}</span>}
      {article.author && <span>{article.author}</span>}
      <span>{article.word_count.toLocaleString('en-US')} words</span>
    </p>
  )
}

const SORT_LABELS: Record<ArticleQuery['sort'], string> = {
  published: 'Newest',
  priority: 'Priority score',
  source: 'Source',
}
function describe(query: ArticleQuery): string {
  const order = {
    published: query.order === 'asc' ? 'oldest first' : 'newest first',
    priority: query.order === 'asc' ? 'lowest priority first' : 'highest priority first',
    source: query.order === 'desc' ? 'by source, Z to A' : 'by source, A to Z',
  }[query.sort]
  return `Sorted ${order}. Only articles at or above the priority threshold are sent to models automatically.`
}

export function ArticlesPage({
  search = '',
  onOpen,
  onQueryChange = () => {},
}: {
  search?: string
  onOpen: (id: number) => void
  onQueryChange?: (search: string) => void
}) {
  const query = parseArticleQuery(search)
  const apiQuery = toApiQuery(query)
  // Each result remembers the view it answers, so a new view shows "loading" until its own result arrives.
  const [loaded, setLoaded] = useState<{ query: string; articles: ArticleSummary[] | null; error: string | null }>()
  const [sources, setSources] = useState<Source[]>([])
  const articles = loaded?.query === apiQuery ? loaded.articles : null
  const error = loaded?.query === apiQuery ? loaded.error : null
  // After the user changes the view, move focus to the results once they arrive: the control they used may be gone
  // (the pager hides while loading) and the page scrolls to the top.
  const results = useRef<HTMLHeadingElement>(null)
  const focusResults = useRef(false)

  useEffect(() => {
    if (articles !== null && focusResults.current) {
      focusResults.current = false
      results.current?.focus()
    }
  }, [articles])

  useEffect(() => {
    listSources()
      .then((all) => setSources([...all].sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => setSources([]))
  }, [])

  useEffect(() => {
    let current = true // ignore a slow response for a view the user has already left
    listArticles(apiQuery)
      .then((result) => current && setLoaded({ query: apiQuery, articles: result, error: null }))
      .catch(
        (failure) =>
          current &&
          setLoaded({ query: apiQuery, articles: null, error: `Could not load articles: ${describeError(failure)}` }),
      )
    return () => {
      current = false
    }
  }, [apiQuery])

  function go(next: ArticleQuery) {
    focusResults.current = true
    onQueryChange(toSearch(next))
  }

  function change(update: Partial<ArticleQuery>) {
    go({ ...query, page: 0, ...update }) // any filter change starts again from the first page
  }

  function open(event: MouseEvent<HTMLAnchorElement>, id: number) {
    followLink(event, () => onOpen(id))
  }

  const filtered = query.band !== undefined || query.status !== undefined || query.sourceId !== undefined

  return (
    <section className="page">
      <header className="page-header">
        <h1>Articles</h1>
        <p className="muted">{describe(query)}</p>
      </header>

      <div className="queue-controls" role="group" aria-label="Sort and filter articles">
        <div className="field">
          <label htmlFor="article-sort">Sort by</label>
          <select
            id="article-sort"
            value={query.sort}
            onChange={(event) => change({ sort: event.target.value as ArticleQuery['sort'], order: undefined })}
          >
            {SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {SORT_LABELS[sort]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="article-band">Priority band</label>
          <select
            id="article-band"
            value={query.band ?? ''}
            onChange={(event) => change({ band: (event.target.value || undefined) as Band | undefined })}
          >
            <option value="">Any</option>
            {BANDS.map((band) => (
              <option key={band} value={band}>
                {band}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="article-status">Pipeline</label>
          <select
            id="article-status"
            value={query.status ?? ''}
            onChange={(event) => change({ status: (event.target.value || undefined) as PipelineState | undefined })}
          >
            <option value="">Any</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {PIPELINE_LABELS[status]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="article-source">Source</label>
          <select
            id="article-source"
            value={query.sourceId ?? ''}
            onChange={(event) => change({ sourceId: event.target.value ? Number(event.target.value) : undefined })}
          >
            <option value="">All sources</option>
            {query.sourceId !== undefined && !sources.some((source) => source.id === query.sourceId) && (
              <option value={query.sourceId}>Source #{query.sourceId}</option> // a stale link or an unloaded list
            )}
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </div>
        {filtered && (
          <button
            type="button"
            className="link-button"
            onClick={() => change({ band: undefined, status: undefined, sourceId: undefined })}
          >
            Clear filters
          </button>
        )}
      </div>

      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      <h2 className="visually-hidden" tabIndex={-1} ref={results}>
        Results, page {query.page + 1}
      </h2>
      <p className="visually-hidden" role="status">
        {articles === null ? '' : `${articles.length} article${articles.length === 1 ? '' : 's'} on page ${query.page + 1}.`}
      </p>
      {articles === null && !error && <p className="muted">Loading articles…</p>}
      {articles?.length === 0 &&
        (filtered || query.page > 0 ? (
          <p className="muted">No articles match this view.</p>
        ) : (
          <p className="muted">No articles yet. Fetch a source to collect some.</p>
        ))}
      {articles && articles.length > 0 && (
        <ul className="article-list" aria-label="Articles">
          {articles.map((article) => (
            <li key={article.id} className="panel article-card article-row">
              <div className="article-score">
                {article.priority ? (
                  <ScoreBadge score={article.priority.score} band={article.priority.band} />
                ) : (
                  <span className="score-badge score-none" title="Not scored">
                    <span aria-hidden="true">–</span>
                    <span className="visually-hidden">Not scored</span>
                  </span>
                )}
              </div>
              <div className="article-body">
                <h3>
                  <a href={`/articles/${article.id}`} onClick={(event) => open(event, article.id)}>
                    {article.title}
                  </a>
                </h3>
                <Meta article={article} />
                {article.priority && <KeywordChips terms={article.priority.terms} />}
                <p className="excerpt">{article.excerpt}</p>
              </div>
              <div className="article-state">
                {article.pipeline && <PipelineBadge state={article.pipeline.status} />}
                {article.pipeline && article.pipeline.rules > 0 && (
                  <span className="muted small">
                    {article.pipeline.rules} rule{article.pipeline.rules === 1 ? '' : 's'}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {articles && (query.page > 0 || articles.length === PAGE_SIZE) && (
        <nav className="pager" aria-label="Pages">
          <button type="button" disabled={query.page === 0} onClick={() => go({ ...query, page: query.page - 1 })}>
            ← Previous
          </button>
          <span className="muted small">Page {query.page + 1}</span>
          <button
            type="button"
            disabled={articles.length < PAGE_SIZE}
            onClick={() => go({ ...query, page: query.page + 1 })}
          >
            Next →
          </button>
        </nav>
      )}
    </section>
  )
}

export function ArticleReader({ id, onBack }: { id: number; onBack: () => void }) {
  const [article, setArticle] = useState<ArticleDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getArticle(id)
      .then(setArticle)
      .catch((failure) => setError(describeError(failure)))
  }, [id])

  return (
    <article className="page reader">
      <button type="button" className="link-button back" onClick={onBack}>
        ← All articles
      </button>
      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {!article && !error && <p className="muted">Loading article…</p>}
      {article && (
        <>
          <h1>{article.title}</h1>
          <Meta article={article} />
          <p>
            <a href={article.url} target="_blank" rel="noreferrer">
              Open original ↗
            </a>
          </p>
          <RunPanel articleId={article.id} />
          <div className="article-text">
            {article.content_text.split('\n').map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
          </div>
        </>
      )}
    </article>
  )
}
