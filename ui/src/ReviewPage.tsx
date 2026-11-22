import { useEffect, useRef, useState } from 'react'

import { describeError, listRules, RULES_EXPORT_URL, type DetectionRule, type RuleDetail } from './api'
import { ReviewItem } from './ReviewItem'

type Status = DetectionRule['status']

const FILTERS: { status: Status; label: string; empty: string }[] = [
  { status: 'pending_review', label: 'Pending', empty: 'Nothing waiting for review. Run detection on an article to generate rules.' },
  { status: 'approved', label: 'Approved', empty: 'No approved rules yet.' },
  { status: 'rejected', label: 'Rejected', empty: 'No rejected rules.' },
]

export function ReviewPage({
  onOpenArticle,
  onRulesChanged = () => {},
}: {
  onOpenArticle: (id: number) => void
  onRulesChanged?: () => void // e.g. so the sidebar's pending count follows decisions made here
}) {
  const [status, setStatus] = useState<Status>('pending_review')
  const [rules, setRules] = useState<RuleDetail[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  // The filter currently shown, readable from callbacks that started under another filter.
  const shown = useRef<Status>('pending_review')

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

  function selectFilter(next: Status) {
    if (next === status) return
    shown.current = next
    setRules(null)
    setMessage(null)
    setStatus(next)
  }

  function applyUpdate(updated: RuleDetail, startedUnder: Status) {
    if (startedUnder !== shown.current) return // the list on screen is another filter's: leave it alone
    if (updated.status !== startedUnder) {
      setRules((current) => current && current.filter((rule) => rule.id !== updated.id))
      const verb = { approved: 'Approved', rejected: 'Rejected', pending_review: 'Returned to review' }[updated.status]
      setMessage(`${verb} “${updated.title}”.`)
    } else {
      setRules((current) => current && current.map((rule) => (rule.id === updated.id ? updated : rule)))
    }
  }

  const filter = FILTERS.find((candidate) => candidate.status === status)!

  return (
    <section className="page">
      <header className="page-header">
        <h1>Review</h1>
        <p className="muted">Approve, reject or correct generated rules. Edits are revalidated and the KQL is regenerated.</p>
      </header>

      <div className="tabs" role="group" aria-label="Show rules">
        {FILTERS.map((candidate) => (
          <button
            key={candidate.status}
            type="button"
            aria-pressed={candidate.status === status}
            className={candidate.status === status ? 'tab is-selected' : 'tab'}
            onClick={() => selectFilter(candidate.status)}
          >
            {candidate.label}
          </button>
        ))}
      </div>

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
      {rules?.length === 0 && <p className="muted">{filter.empty}</p>}
      {status === 'approved' && rules !== null && rules.length > 0 && (
        <div className="export-bar">
          <p className="muted">
            One Sigma YAML file with every approved rule, each keeping its id and article reference.
          </p>
          <a className="button secondary" href={RULES_EXPORT_URL} download>
            Download approved rules (YAML)
          </a>
        </div>
      )}
      {rules?.map((rule) => (
        <ReviewItem
          key={rule.id}
          rule={rule}
          onUpdated={(updated) => {
            onRulesChanged()
            applyUpdate(updated, status)
          }}
          onOpenArticle={onOpenArticle}
        />
      ))}
    </section>
  )
}
