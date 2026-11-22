import { useEffect, useState, type MouseEvent } from 'react'

import {
  describeError,
  listReviewArticles,
  listRules,
  RULES_EXPORT_URL,
  type DetectionRule,
  type ReviewArticle,
  type RuleDetail,
} from './api'
import { ScoreBadge } from './badges'
import { ReviewItem } from './ReviewItem'
import { parseReviewQuery, toReviewSearch, type ReviewQuery } from './reviewQuery'
import { followLink } from './routes'

type Status = DetectionRule['status']

const FILTERS: { status: Status; label: string; empty: string; emptyArticles: string }[] = [
  {
    status: 'pending_review',
    label: 'Pending',
    empty: 'Nothing waiting for review. Run detection on an article to generate rules.',
    emptyArticles: 'No articles waiting for review. Run detection on an article to generate rules.',
  },
  { status: 'approved', label: 'Approved', empty: 'No approved rules yet.', emptyArticles: 'No articles with approved rules yet.' },
  { status: 'rejected', label: 'Rejected', empty: 'No rejected rules.', emptyArticles: 'No articles with rejected rules.' },
]

const VIEWS: { view: ReviewQuery['view']; label: string }[] = [
  { view: 'articles', label: 'By article' },
  { view: 'rules', label: 'By rule' },
]

export function ReviewPage({
  search = '',
  onQueryChange = () => {},
  onOpenArticle,
  onRulesChanged = () => {},
}: {
  search?: string // ?view=rules&status=approved; the defaults (by article, pending) are left out
  onQueryChange?: (search: string) => void
  onOpenArticle: (id: number, view?: 'rules') => void
  onRulesChanged?: () => void // e.g. so the sidebar's pending count follows decisions made here
}) {
  // Follows the URL (back and forward) but also works on its own, e.g. in tests.
  const [query, setQuery] = useState(() => parseReviewQuery(search))
  const [urlSearch, setUrlSearch] = useState(search)
  if (urlSearch !== search) {
    setUrlSearch(search)
    setQuery(parseReviewQuery(search))
  }

  function change(update: Partial<ReviewQuery>) {
    const next = { ...query, ...update }
    if (next.view === query.view && next.status === query.status) return
    setQuery(next)
    onQueryChange(toReviewSearch(next))
  }

  const filter = FILTERS.find((candidate) => candidate.status === query.status)!

  return (
    <section className="page">
      <header className="page-header">
        <h1>Review</h1>
        <p className="muted">
          {query.view === 'articles'
            ? 'Open an article to review its rules beside the evidence they came from.'
            : 'Approve, reject or correct generated rules. Edits are revalidated and the KQL is regenerated.'}
        </p>
      </header>

      <div className="review-controls">
        <div className="segmented" role="group" aria-label="Review by">
          {VIEWS.map((candidate) => (
            <button
              key={candidate.view}
              type="button"
              aria-pressed={candidate.view === query.view}
              className={candidate.view === query.view ? 'is-selected' : undefined}
              onClick={() => change({ view: candidate.view })}
            >
              {candidate.label}
            </button>
          ))}
        </div>
        <div className="tabs" role="group" aria-label="Show rules">
          {FILTERS.map((candidate) => (
            <button
              key={candidate.status}
              type="button"
              aria-pressed={candidate.status === query.status}
              className={candidate.status === query.status ? 'tab is-selected' : 'tab'}
              onClick={() => change({ status: candidate.status })}
            >
              {candidate.label}
            </button>
          ))}
        </div>
      </div>

      {/* key: another view or status starts from a clean list, and late answers for the old one are dropped */}
      {query.view === 'articles' ? (
        <ArticleQueue
          key={query.status}
          status={query.status}
          label={`Articles with ${filter.label.toLowerCase()} rules`}
          empty={filter.emptyArticles}
          onOpenArticle={onOpenArticle}
        />
      ) : (
        <RuleList
          key={query.status}
          status={query.status}
          empty={filter.empty}
          onOpenArticle={onOpenArticle}
          onRulesChanged={onRulesChanged}
        />
      )}
    </section>
  )
}

function ExportBar() {
  return (
    <div className="export-bar">
      <p className="muted">One Sigma YAML file with every approved rule, each keeping its id and article reference.</p>
      <a className="button secondary" href={RULES_EXPORT_URL} download>
        Download approved rules (YAML)
      </a>
    </div>
  )
}

const COUNT_LABELS: [Status, string][] = [
  ['pending_review', 'pending'],
  ['approved', 'approved'],
  ['rejected', 'rejected'],
]

/** Articles with rules in one status, to open and review in place. */
const QUEUE_LIMIT = 100 // the API's default

function ArticleQueue({
  status,
  label,
  empty,
  onOpenArticle,
}: {
  status: Status
  label: string
  empty: string
  onOpenArticle: (id: number, view?: 'rules') => void
}) {
  const [rows, setRows] = useState<ReviewArticle[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    listReviewArticles(status)
      .then((result) => current && setRows(result))
      .catch((failure) => current && setError(`Could not load the review queue: ${describeError(failure)}`))
    return () => {
      current = false
    }
  }, [status])

  function open(event: MouseEvent<HTMLAnchorElement>, id: number) {
    followLink(event, () => onOpenArticle(id, 'rules'))
  }

  return (
    <>
      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {rows === null && !error && <p className="muted">Loading the review queue…</p>}
      {rows?.length === 0 && <p className="muted">{empty}</p>}
      {status === 'approved' && rows !== null && rows.length > 0 && <ExportBar />}
      {rows && rows.length > 0 && (
        <ul className="queue-list" aria-label={label}>
          {rows.map((row) => (
            <li key={row.article.id} className="queue-row">
              <div className="queue-score">
                {row.priority ? (
                  <ScoreBadge score={row.priority.score} band={row.priority.band} />
                ) : (
                  <span className="score-badge score-none" title="Not scored">
                    <span aria-hidden="true">–</span>
                    <span className="visually-hidden">Not scored</span>
                  </span>
                )}
              </div>
              <div className="queue-main">
                <a href={`/articles/${row.article.id}?view=rules`} onClick={(event) => open(event, row.article.id)}>
                  {row.article.title}
                </a>
                <span className="muted small">
                  {row.article.source.name} · run #{row.run_id}
                </span>
              </div>
              <div className="queue-relevance small">
                {row.relevance ? (
                  <>
                    Relevance {row.relevance.score}/10
                  </>
                ) : (
                  <span className="muted">No relevance verdict</span>
                )}
              </div>
              <div className="queue-counts small">
                {COUNT_LABELS.map(([key, label], index) => (
                  <span key={key}>
                    {index > 0 && ' · '}
                    {key === status ? (
                      <strong>
                        {row.rules[key]} {label}
                      </strong>
                    ) : (
                      `${row.rules[key]} ${label}`
                    )}
                  </span>
                ))}
                {row.duplicates > 0 && (
                  <span className="queue-duplicates">
                    {row.duplicates} likely duplicate{row.duplicates === 1 ? '' : 's'}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {rows?.length === QUEUE_LIMIT && <p className="muted small">Showing the first {QUEUE_LIMIT} articles.</p>}
    </>
  )
}

/** Every rule in one status, newest first, each with its review actions. */
function RuleList({
  status,
  empty,
  onOpenArticle,
  onRulesChanged,
}: {
  status: Status
  empty: string
  onOpenArticle: (id: number) => void
  onRulesChanged: () => void
}) {
  const [rules, setRules] = useState<RuleDetail[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    listRules({ status })
      .then((result) => {
        if (!current) return
        setRules(result)
        setError(null)
      })
      .catch((failure) => {
        if (!current) return
        setRules(null)
        setError(`Could not load rules: ${describeError(failure)}`)
      })
    return () => {
      current = false
    }
  }, [status])

  function applyUpdate(updated: RuleDetail) {
    if (updated.status !== status) {
      setRules((current) => current && current.filter((rule) => rule.id !== updated.id))
      const verb = { approved: 'Approved', rejected: 'Rejected', pending_review: 'Returned to review' }[updated.status]
      setMessage(`${verb} “${updated.title}”.`)
    } else {
      setRules((current) => current && current.map((rule) => (rule.id === updated.id ? updated : rule)))
    }
  }

  return (
    <>
      {message && (
        <p className="banner banner-ok" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {rules === null && !error && <p className="muted">Loading rules…</p>}
      {rules?.length === 0 && <p className="muted">{empty}</p>}
      {status === 'approved' && rules !== null && rules.length > 0 && <ExportBar />}
      {rules?.map((rule) => (
        <ReviewItem
          key={rule.id}
          rule={rule}
          onUpdated={(updated) => {
            onRulesChanged()
            applyUpdate(updated)
          }}
          onOpenArticle={onOpenArticle}
        />
      ))}
    </>
  )
}
