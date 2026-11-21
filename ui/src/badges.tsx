import type { PipelineState } from './api'

const PIPELINE_LABELS: Record<PipelineState, string> = {
  not_run: 'Not run',
  in_progress: 'Running',
  filtered: 'Filtered',
  failed: 'Failed',
  no_rules: 'No rules',
  rules: 'Rules',
}

export function PipelineBadge({ state }: { state: PipelineState }) {
  return <span className={`pipeline-badge pipeline-${state}`}>{PIPELINE_LABELS[state]}</span>
}

export function ScoreBadge({ score, band }: { score: number; band: string }) {
  return (
    <span className={`score-badge band-${band.toLowerCase().replace(' ', '-')}`} title={`Priority ${score} (${band})`}>
      {score.toFixed(1)}
      <span className="visually-hidden">, {band}</span>
    </span>
  )
}
