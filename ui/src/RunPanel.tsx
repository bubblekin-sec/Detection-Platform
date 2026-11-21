import { useEffect, useState } from 'react'

import { describeError, getRun, listArticleRuns, queueRun, type PipelineRun, type StageResult } from './api'

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
export function RunPanel({ articleId, pollMs = 2000 }: { articleId: number; pollMs?: number }) {
  const [run, setRun] = useState<PipelineRun | null | undefined>(undefined) // undefined = loading
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
      {run && active && (
        <p className="muted" role="status">
          {run.status === 'queued' ? 'Queued. Waiting for the worker…' : `Running: ${run.current_step ?? 'starting'}…`}
        </p>
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
    </section>
  )
}
