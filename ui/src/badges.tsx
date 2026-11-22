import type { PipelineState } from './api'
import { PIPELINE_LABELS } from './labels'

export function PipelineBadge({ state }: { state: PipelineState }) {
  return <span className={`pipeline-badge pipeline-${state}`}>{PIPELINE_LABELS[state]}</span>
}

export function ScoreBadge({ score, band }: { score: number; band: string }) {
  return (
    <span className={`score-badge band-${band.toLowerCase().replace(' ', '-')}`} title={`Priority ${score} (${band})`}>
      <span className="visually-hidden">Priority </span>
      {score.toFixed(1)}
      <span className="visually-hidden">, {band}</span>
    </span>
  )
}

const CHIP_ORDER = ['perfect', 'lolbas', 'intelligence']
const MAX_CHIPS = 6

/** The strongest matched keywords as chips, strongest bucket first; the rest summarised as "+N". */
export function KeywordChips({ terms }: { terms: Record<string, string[]> }) {
  const chips = CHIP_ORDER.flatMap((bucket) => (terms[bucket] ?? []).map((term) => ({ bucket, term })))
  if (chips.length === 0) return null
  const hidden = chips.length - MAX_CHIPS
  return (
    <ul className="chips" aria-label="Matched keywords">
      {chips.slice(0, MAX_CHIPS).map(({ bucket, term }) => (
        <li key={`${bucket}:${term}`} className={`chip chip-${bucket}`} title={`${bucket} keyword`}>
          <span className="visually-hidden">{bucket}: </span>
          {term}
        </li>
      ))}
      {hidden > 0 && <li className="chip chip-more">+{hidden} more</li>}
    </ul>
  )
}
