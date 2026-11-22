import { useState, type MouseEvent } from 'react'

import { ApiError, describeError, updateRule, type DetectionRule, type RuleDetail, type RuleUpdate } from './api'
import { RuleCard } from './RuleCard'
import { followLink } from './routes'

type Status = DetectionRule['status']

type Editor = 'none' | 'sigma' | 'kql'

/** A rule with its review actions: approve, reject or send back (with a note), and edit the Sigma or the KQL. */
export function ReviewItem({
  rule,
  onUpdated,
  onOpenArticle,
  showArticle = true,
}: {
  rule: RuleDetail
  onUpdated: (rule: RuleDetail) => void
  onOpenArticle: (id: number) => void
  showArticle?: boolean // false on the article's own page
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

  const header = (showArticle || rule.reviewer_note) && (
    <p className="muted small">
      {showArticle && (
        <>
          From{' '}
          <a href={`/articles/${rule.article.id}`} onClick={followArticle}>
            {rule.article.title}
          </a>
        </>
      )}
      {showArticle && rule.reviewer_note && ' · '}
      {rule.reviewer_note && <>Note: {rule.reviewer_note}</>}
    </p>
  )

  return (
    <RuleCard rule={rule} header={header} onOpenArticle={onOpenArticle}>
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
