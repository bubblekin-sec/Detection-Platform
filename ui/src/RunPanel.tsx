import { useEffect, useState } from 'react'

import {
  cancelRun,
  describeError,
  getRun,
  getWorkerStatus,
  listArticleRuns,
  queueRun,
  type Observable,
  type PipelineRun,
  type StageResult,
  type WorkerStatus,
} from './api'
import { RuleCard } from './RuleCard'

const ACTIVE = new Set(['queued', 'running'])

interface RelevanceOutput {
  score: number
  min_score: number
  rationale: string
  signals: string[]
}

function relevanceOf(run: PipelineRun): { stage: StageResult; output: RelevanceOutput } | null {
  const stage = run.stages.find((candidate) => candidate.stage === 'relevance' && candidate.status !== 'failed')
  return stage ? { stage, output: stage.output as unknown as RelevanceOutput } : null
}

function tokens(stage: StageResult): string {
  const total = (stage.input_tokens ?? 0) + (stage.output_tokens ?? 0)
  return `${stage.model ?? 'model'} · ${total.toLocaleString('en-US')} tokens`
}

/** Detection run for one article: queue it, follow progress, show the relevance verdict. */
export function RunPanel({
  articleId,
  pollMs = 2000,
  onRunChange,
}: {
  articleId: number
  pollMs?: number
  onRunChange?: (run: PipelineRun | null) => void // the run shown, e.g. so the reader can mark its evidence
}) {
  const [run, setRun] = useState<PipelineRun | null | undefined>(undefined) // undefined = loading

  useEffect(() => {
    if (run !== undefined) onRunChange?.(run)
  }, [run, onRunChange])
  const [error, setError] = useState<string | null>(null)
  const [queueing, setQueueing] = useState(false)

  useEffect(() => {
    listArticleRuns(articleId)
      .then((runs) => setRun(runs[0] ?? null))
      .catch((failure) => {
        setRun(null) // still allow queueing a new run
        setError(`Could not load previous runs: ${describeError(failure)}`)
      })
  }, [articleId])

  const runId = run?.id
  const active = run ? ACTIVE.has(run.status) : false
  useEffect(() => {
    if (!active || runId === undefined) return
    let cancelled = false
    const timer = setInterval(() => {
      getRun(runId)
        .then((latest) => !cancelled && setRun(latest))
        .catch(() => {}) // keep polling through a transient error
    }, pollMs)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [active, runId, pollMs])

  // While the run is queued or running, say whether a worker is there to pick it up or finish it.
  const queued = run?.status === 'queued'
  const [worker, setWorker] = useState<WorkerStatus | null>(null)
  useEffect(() => {
    if (!active) return
    let cancelled = false
    const check = () =>
      getWorkerStatus()
        .then((status) => !cancelled && setWorker(status))
        .catch(() => !cancelled && setWorker(null)) // unknown: fall back to the plain queued message
    check()
    const timer = setInterval(check, pollMs)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [active, pollMs])
  const workerGone = worker !== null && !worker.alive

  const [cancelling, setCancelling] = useState(false)
  async function cancel() {
    if (!run) return
    setError(null)
    setCancelling(true)
    try {
      setRun(await cancelRun(run.id))
    } catch (failure) {
      // 409: a live worker started (or still holds) it; the next poll shows its progress.
      setError(`Could not cancel the run: ${describeError(failure)}`)
    } finally {
      setCancelling(false)
    }
  }

  async function start() {
    setError(null)
    setQueueing(true)
    try {
      setRun(await queueRun(articleId))
    } catch (failure) {
      setError(`Could not queue the run: ${describeError(failure)}`)
    } finally {
      setQueueing(false)
    }
  }

  const verdict = run ? relevanceOf(run) : null

  return (
    <section className="panel run-panel" aria-labelledby="run-heading">
      <div className="run-header">
        <h2 id="run-heading">Detection</h2>
        <button type="button" onClick={start} disabled={queueing || active || run === undefined}>
          {run && !active ? 'Run again' : 'Run detection'}
        </button>
      </div>

      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {run === null && <p className="muted">Not run yet.</p>}
      {run?.status === 'running' && !workerGone && (
        <p className="muted" role="status">
          Running: {run.current_step ?? 'starting'}…
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
      {queued && (
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
      {run?.status === 'filtered_out' && run.current_step === 'junk_filter' && (
        <p className="muted">Skipped: the article is too short or matched the source’s junk filter.</p>
      )}
      {verdict && (
        <div className="verdict">
          <p className={`verdict-score ${verdict.stage.status === 'passed' ? 'is-relevant' : 'is-not-relevant'}`}>
            {verdict.stage.status === 'passed' ? 'Relevant' : 'Not relevant'}: {verdict.output.score}/10 (needs{' '}
            {verdict.output.min_score})
          </p>
          <p>{verdict.output.rationale}</p>
          {verdict.output.signals.length > 0 && (
            <ul className="signals" aria-label="Signals">
              {verdict.output.signals.map((signal, index) => (
                <li key={index}>{signal}</li>
              ))}
            </ul>
          )}
          <p className="muted small">
            {tokens(verdict.stage)} · profile {run?.profile.slug} v{run?.profile.version}
          </p>
        </div>
      )}
      {run && <Rules run={run} />}
      {run && <Observables run={run} />}
    </section>
  )
}

function Rules({ run }: { run: PipelineRun }) {
  const stage = run.stages.find((candidate) => candidate.stage === 'rule_generation')
  if (!stage) return null
  const note = stage.output.note as string | undefined
  return (
    <div className="rules">
      <h3>Rules ({run.rules.length})</h3>
      {note && <p className="muted">{note}</p>}
      {run.rules.map((rule) => (
        <RuleCard key={rule.id} rule={rule} />
      ))}
      <GroupProblems output={stage.output} />
    </div>
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
function GroupProblems({ output }: { output: Record<string, unknown> }) {
  const groups = ((output.groups as GroupOutcome[] | undefined) ?? []).filter((group) => group.outcome !== 'created')
  const skipped = (output.skipped_groups as number | undefined) ?? 0
  if (groups.length === 0 && skipped === 0) return null
  return (
    <div className="group-problems" role="note" aria-label="Groups without a rule">
      <h4>Groups without a rule</h4>
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

function Observables({ run }: { run: PipelineRun }) {
  const extraction = run.stages.find((stage) => stage.stage === 'extraction' && stage.status === 'passed')
  if (!extraction) return null
  if (run.observables.length === 0) {
    return <p className="muted">No observables were extracted: the article had no concrete detail for the profile’s fields.</p>
  }
  const groups = new Map<string, Observable[]>()
  for (const item of run.observables) {
    groups.set(item.field_name, [...(groups.get(item.field_name) ?? []), item])
  }
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
              <li key={item.id} className={item.evidence_verified ? undefined : 'unverified'}>
                <code className="observable-value">{item.value}</code>
                <blockquote>{item.evidence}</blockquote>
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
