import { useEffect, useState, type MouseEvent } from 'react'

import { describeError, getArticle, listArticles, listSources, type ArticleDetail, type ArticleSummary, type Source } from './api'

const DATE_FORMAT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

function formatDate(value: string | null): string | null {
  return value ? DATE_FORMAT.format(new Date(value)) : null
}

function Meta({ article }: { article: ArticleSummary }) {
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

export function ArticlesPage({ onOpen }: { onOpen: (id: number) => void }) {
  const [articles, setArticles] = useState<ArticleSummary[] | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState<number | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listSources()
      .then((all) => setSources([...all].sort((a, b) => a.name.localeCompare(b.name))))
      .catch(() => setSources([]))
  }, [])

  useEffect(() => {
    let current = true // ignore a slow response for a filter the user has already changed
    listArticles(sourceId)
      .then((result) => {
        if (!current) return
        setArticles(result)
        setError(null)
      })
      .catch((failure) => {
        if (!current) return
        setArticles(null) // don't leave the previous filter's list under the error
        setError(`Could not load articles: ${describeError(failure)}`)
      })
    return () => {
      current = false
    }
  }, [sourceId])

  function open(event: MouseEvent, id: number) {
    event.preventDefault()
    onOpen(id)
  }

  return (
    <section className="page">
      <header className="page-header page-header-row">
        <div>
          <h1>Articles</h1>
          <p className="muted">Newest first. Use “Fetch now” on the Sources page to pull new posts.</p>
        </div>
        <div className="field filter">
          <label htmlFor="article-source">Source</label>
          <select
            id="article-source"
            value={sourceId ?? ''}
            onChange={(event) => {
              setArticles(null) // show loading rather than the previous filter's articles
              setSourceId(event.target.value ? Number(event.target.value) : undefined)
            }}
          >
            <option value="">All sources</option>
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {articles === null && !error && <p className="muted">Loading articles…</p>}
      {articles?.length === 0 && <p className="muted">No articles yet. Fetch a source to collect some.</p>}
      {articles && articles.length > 0 && (
        <ul className="article-list">
          {articles.map((article) => (
            <li key={article.id} className="panel article-card">
              <h3>
                <a href={`/articles/${article.id}`} onClick={(event) => open(event, article.id)}>
                  {article.title}
                </a>
              </h3>
              <Meta article={article} />
              <p className="excerpt">{article.excerpt}</p>
            </li>
          ))}
        </ul>
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
