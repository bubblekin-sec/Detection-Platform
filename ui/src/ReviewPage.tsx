import { useEffect, useRef, useState, type MouseEvent } from 'react'

import { ApiError, describeError, listRules, RULES_EXPORT_URL, updateRule, type DetectionRule, type RuleDetail, type RuleUpdate } from './api'
import { RuleCard } from './RuleCard'
import { followLink } from './routes'

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
    listRules(status)
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

type Editor = 'none' | 'sigma' | 'kql'

function ReviewItem({
  rule,
  onUpdated,
  onOpenArticle,
}: {
  rule: RuleDetail
  onUpdated: (rule: RuleDetail) => void
  onOpenArticle: (id: number) => void
}) {
  const [note, setNote] = useState(rule.reviewer_note ?? '')
  const [editor, setEditor] = useState<Editor>('none')
  const [draft, setDraft] = useState('')
  const [editorErrors, setEditorErrors] = useState<string[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const currentKql = rule.queries.find((query) => query.origin === 'human_edit')?.query_text ?? ''

  function messages(failure: unknown, field: string): string[] {
    if (failure instanceof ApiError && failure.errors.length) {
      return failure.errors.map((error) => error.replace(new RegExp(`^${field}: `), ''))
    }
    return [describeError(failure)]
  }

  async function decide(status: Status) {
    setSaving(true)
    setActionError(null)
    // expected_sigma_hash: the decision only applies to the content shown on this card.
    const update: RuleUpdate = { status, ...(note.trim() ? { reviewer_note: note.trim() } : {}), expected_sigma_hash: rule.sigma_hash }
    try {
      onUpdated(await updateRule(rule.id, update))
    } catch (failure) {
      setActionError(messages(failure, 'status').join(' '))
    } finally {
      setSaving(false)
    }
  }

  async function saveEdit() {
    const field = editor === 'sigma' ? 'sigma_yaml' : 'kql'
    setSaving(true)
    setEditorErrors([])
    try {
      onUpdated(await updateRule(rule.id, editor === 'sigma' ? { sigma_yaml: draft } : { kql: draft }))
      setEditor('none')
    } catch (failure) {
      setEditorErrors(messages(failure, field))
    } finally {
      setSaving(false)
    }
  }

  function open(next: Editor) {
    setEditorErrors([])
    setDraft(next === 'sigma' ? rule.sigma_yaml : currentKql)
    setEditor(next)
  }

  function followArticle(event: MouseEvent<HTMLAnchorElement>) {
    followLink(event, () => onOpenArticle(rule.article.id))
  }

  const header = (
    <p className="muted small">
      From{' '}
      <a href={`/articles/${rule.article.id}`} onClick={followArticle}>
        {rule.article.title}
      </a>
      {rule.reviewer_note && <> · Note: {rule.reviewer_note}</>}
    </p>
  )

  return (
    <RuleCard rule={rule} header={header}>
      <div className="review-actions">
        <label className="field">
          <span>Reviewer note</span>
          <textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} maxLength={5000} />
        </label>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
        <div className="form-actions">
          {rule.status !== 'approved' && (
            <button type="button" disabled={saving} onClick={() => decide('approved')}>
              Approve
            </button>
          )}
          {rule.status !== 'rejected' && (
            <button type="button" className="secondary" disabled={saving} onClick={() => decide('rejected')}>
              Reject
            </button>
          )}
          {rule.status !== 'pending_review' && (
            <button type="button" className="secondary" disabled={saving} onClick={() => decide('pending_review')}>
              Back to pending
            </button>
          )}
          <button type="button" className="link-button" aria-expanded={editor === 'sigma'} onClick={() => open(editor === 'sigma' ? 'none' : 'sigma')}>
            Edit Sigma
          </button>
          <button type="button" className="link-button" aria-expanded={editor === 'kql'} onClick={() => open(editor === 'kql' ? 'none' : 'kql')}>
            Edit KQL
          </button>
        </div>

        {editor !== 'none' && (
          <div className="rule-editor">
            <label className="field">
              <span>{editor === 'sigma' ? 'Sigma YAML' : 'KQL'}</span>
              <textarea
                className="code-input"
                rows={editor === 'sigma' ? 16 : 8}
                spellCheck={false}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
              />
            </label>
            <p className="muted small">
              Saving {editor === 'sigma' ? 'revalidates the rule and regenerates the converted KQL. It ' : ''}returns the rule to
              review.
            </p>
            {editorErrors.length > 0 && (
              <div className="form-error" role="alert">
                <ul>
                  {editorErrors.map((error, index) => (
                    <li key={index}>{error}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="form-actions">
              <button type="button" disabled={saving || !draft.trim()} onClick={saveEdit}>
                {editor === 'sigma' ? 'Save Sigma' : 'Save KQL'}
              </button>
              <button type="button" className="secondary" onClick={() => setEditor('none')}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </RuleCard>
  )
}
