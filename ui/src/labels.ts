import type { PipelineState } from './api'

/** One wording for pipeline states, used by the queue filter and the row and dashboard badges alike. */
export const PIPELINE_LABELS: Record<PipelineState, string> = {
  not_run: 'Not run',
  in_progress: 'Running',
  filtered: 'Filtered out',
  failed: 'Failed',
  no_rules: 'No rules',
  rules: 'Has rules',
}
