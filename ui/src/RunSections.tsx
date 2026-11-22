import type { Observable, PipelineRun, StageResult } from './api'
import { groupByField, isActive, tokens, type ArticleRun } from './articleRun'

/** Interruptions that need the reviewer's attention: a missing or stopped worker, a failure, a load error. */
export function RunBanners({ state }: { state: ArticleRun }) {
  const { run, error, workerGone, worker, cancelling, cancel } = state
  return (
    <>
      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {run?.status === 'running' && workerGone && (
        <div className="run-queued">
          <p className="banner banner-error" role="alert">
            <strong>The worker processing this run stopped</strong> during {run.current_step ?? 'its first step'}. Cancel
            it and run detection again (a new worker also fails it when it starts).
          </p>
          <button type="button" className="secondary" onClick={cancel} disabled={cancelling}>
            Cancel run
          </button>
        </div>
      )}
      {run?.status === 'queued' && (
        <div className="run-queued">
          {workerGone ? (
            <div className="banner banner-info" role="status">
              <p>
                <strong>No worker is running</strong>, so this run is waiting. Start one, then it begins on its own:
              </p>
              <ul>
                <li>
                  in <code>api/</code>: <code>uv run python -m app.worker</code> (add <code>--once</code> to stop when the
                  queue is empty)
                </li>
                <li>
                  or in the repo root: <code>docker compose --profile worker up -d</code>
                </li>
              </ul>
            </div>
          ) : (
            <p className="muted" role="status">
              {worker?.alive && worker.running > 0
                ? 'Queued. The worker is busy and will pick this one up when it is free.'
                : 'Queued. Waiting for the worker…'}
            </p>
          )}
          <button type="button" className="secondary" onClick={cancel} disabled={cancelling}>
            Cancel run
          </button>
        </div>
      )}
      {run?.status === 'failed' && (
        <p className="banner banner-error" role="alert">
          Failed at {run.current_step ?? 'start'}: {run.error}
        </p>
      )}
    </>
  )
}

interface RelevanceOutput {
  score: number
  min_score: number
  rationale: string
  signals: string[]
}

/** The relevance stage's verdict, or why the run never reached it. */
export function RelevanceVerdict({ run }: { run: PipelineRun | null | undefined }) {
  const stage = run?.stages.find((candidate) => candidate.stage === 'relevance' && candidate.status !== 'failed')
  return (
    <section className="panel verdict" aria-labelledby="relevance-heading">
      <h2 id="relevance-heading">Relevance</h2>
      {run === null && <p className="muted">Not run yet.</p>}
      {run?.status === 'filtered_out' && run.current_step === 'junk_filter' && (
        <p className="muted">Skipped: the article is too short or matched the source’s junk filter.</p>
      )}
      {run && !stage && isActive(run) && <p className="muted">Waiting for the run…</p>}
      {stage && (
        <VerdictBody stage={stage} output={stage.output as unknown as RelevanceOutput} run={run!} />
      )}
    </section>
  )
}

function VerdictBody({ stage, output, run }: { stage: StageResult; output: RelevanceOutput; run: PipelineRun }) {
  return (
    <>
      <p className={`verdict-score ${stage.status === 'passed' ? 'is-relevant' : 'is-not-relevant'}`}>
        {stage.status === 'passed' ? 'Relevant' : 'Not relevant'}: {output.score}/10 (needs {output.min_score})
      </p>
      <p>{output.rationale}</p>
      {output.signals.length > 0 && (
        <ul className="signals" aria-label="Signals">
          {output.signals.map((signal, index) => (
            <li key={index}>{signal}</li>
          ))}
        </ul>
      )}
      <p className="muted small">
        {tokens(stage)} · profile {run.profile.slug} v{run.profile.version}
      </p>
    </>
  )
}

interface GroupOutcome {
  platform: string
  telemetry_category: string
  observables: number
  outcome: 'created' | 'invalid' | 'llm_error' | 'error' | 'not_attempted'
  error?: string
  errors?: string[]
}

const OUTCOME_LABELS: Record<string, string> = {
  invalid: 'Sigma still invalid after a repair attempt',
  llm_error: 'Model call failed',
  error: 'Internal error',
  not_attempted: 'Not attempted',
}

/** Groups that produced no rule, and groups skipped by the per-run cap: never hidden from the reviewer. */
export function GroupProblems({ output }: { output: Record<string, unknown> }) {
  const groups = ((output.groups as GroupOutcome[] | undefined) ?? []).filter((group) => group.outcome !== 'created')
  const skipped = (output.skipped_groups as number | undefined) ?? 0
  if (groups.length === 0 && skipped === 0) return null
  return (
    <div className="group-problems" role="note" aria-label="Groups without a rule">
      <h3>Groups without a rule</h3>
      <ul>
        {groups.map((group, index) => (
          <li key={index}>
            <strong>
              {group.platform} · {group.telemetry_category}
            </strong>{' '}
            ({group.observables} observable{group.observables === 1 ? '' : 's'}): {OUTCOME_LABELS[group.outcome] ?? group.outcome}
            {group.error && <> — {group.error}</>}
            {group.errors && group.errors.length > 0 && (
              <ul className="rule-warnings">
                {group.errors.map((error, errorIndex) => (
                  <li key={errorIndex}>{error}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
        {skipped > 0 && (
          <li>
            {skipped} more group{skipped === 1 ? '' : 's'} skipped by the per-run limit (MAX_RULE_GROUPS_PER_RUN).
          </li>
        )}
      </ul>
    </div>
  )
}

function fieldLabel(name: string): string {
  const words = name.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Extracted observables grouped by field, each with its evidence quote; unverified ones are flagged. */
export function Observables({
  run,
  notReached,
  focused,
  onShowInText,
}: {
  run: PipelineRun | null | undefined
  notReached: string
  focused?: ReadonlySet<number> // observables a rule was built from, marked in the list
  onShowInText?: (item: Observable) => void
}) {
  const extraction = run?.stages.find((stage) => stage.stage === 'extraction' && stage.status === 'passed')
  if (!run || !extraction) return <p className="muted">{notReached}</p>
  if (run.observables.length === 0) {
    return <p className="muted">No observables were extracted: the article had no concrete detail for the profile’s fields.</p>
  }
  const groups = groupByField(run.observables)
  const unverified = run.observables.filter((item) => !item.evidence_verified).length

  return (
    <div className="observables">
      <p className="muted small">
        {run.observables.length} observables, {unverified} unverified · {tokens(extraction)}
      </p>
      {[...groups].map(([field, items]) => (
        <section key={field} className="observable-group">
          <h3>
            {fieldLabel(field)} ({items.length})
          </h3>
          <ul>
            {items.map((item) => (
              <li
                key={item.id}
                id={`observable-${item.id}`}
                tabIndex={-1}
                className={[item.evidence_verified ? '' : 'unverified', focused?.has(item.id) ? 'is-focused' : ''].join(' ').trim() || undefined}
              >
                {focused?.has(item.id) && <span className="visually-hidden">Used by the rule: </span>}
                <code className="observable-value">{item.value}</code>
                <blockquote>{item.evidence}</blockquote>
                {item.evidence_span && onShowInText && (
                  <button type="button" className="link-button small" onClick={() => onShowInText(item)}>
                    Show in text
                  </button>
                )}
                {!item.evidence_verified && (
                  <p className="warning">
                    <strong>Unverified:</strong> the quote was not found in the article, or does not contain this
                    value. It will not be used for rules.
                  </p>
                )}
                {Object.keys(item.tags).length > 0 && (
                  <p className="tag-row">
                    {Object.entries(item.tags).map(([tag, value]) => (
                      <span key={tag} className="observable-tag">
                        {tag.replace(/_/g, ' ')}: {value}
                      </span>
                    ))}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
