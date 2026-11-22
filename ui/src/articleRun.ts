import { useEffect, useState } from 'react'

import {
  cancelRun,
  describeError,
  getRun,
  getWorkerStatus,
  listArticleRuns,
  queueRun,
  type PipelineRun,
  type StageResult,
  type WorkerStatus,
} from './api'

const ACTIVE = new Set(['queued', 'running'])

export function isActive(run: PipelineRun | null | undefined): boolean {
  return run ? ACTIVE.has(run.status) : false
}

export interface ArticleRun {
  run: PipelineRun | null | undefined // undefined = loading, null = never run
  active: boolean
  worker: WorkerStatus | null
  workerGone: boolean
  error: string | null
  queueing: boolean
  cancelling: boolean
  start: () => void
  cancel: () => void
}

/** The article's latest detection run: load it, queue a new one, follow its progress and whether a worker is there. */
export function useArticleRun(articleId: number, pollMs = 2000): ArticleRun {
  const [run, setRun] = useState<PipelineRun | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [queueing, setQueueing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [worker, setWorker] = useState<WorkerStatus | null>(null)

  useEffect(() => {
    listArticleRuns(articleId)
      .then((runs) => setRun(runs[0] ?? null))
      .catch((failure) => {
        setRun(null) // still allow queueing a new run
        setError(`Could not load previous runs: ${describeError(failure)}`)
      })
  }, [articleId])

  const runId = run?.id
  const active = isActive(run)
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

  return {
    run,
    active,
    worker,
    workerGone: worker !== null && !worker.alive,
    error,
    queueing,
    cancelling,
    start: () => void start(),
    cancel: () => void cancel(),
  }
}

export function stageOf(run: PipelineRun | null | undefined, name: string): StageResult | undefined {
  return run?.stages.find((stage) => stage.stage === name)
}

export function tokens(stage: StageResult): string {
  const total = (stage.input_tokens ?? 0) + (stage.output_tokens ?? 0)
  return `${stage.model ?? 'model'} · ${total.toLocaleString('en-US')} tokens`
}
