export type SourceOrigin = 'huntable_seed' | 'user'

export interface Source {
  id: number
  slug: string
  name: string
  url: string
  rss_url: string | null
  active: boolean
  check_frequency_seconds: number
  description: string | null
  origin: SourceOrigin
  config: Record<string, unknown>
  last_checked_at: string | null
  last_error: string | null
}

export interface SourceCreate {
  name: string
  url: string
  rss_url: string
}

export type SourceUpdate = Partial<{
  name: string
  url: string
  rss_url: string | null
  active: boolean
}>

export interface FetchSummary {
  new: number
  duplicates: number
  too_short: number
  filtered: number
}

export interface ArticleSummary {
  id: number
  url: string
  title: string
  author: string | null
  published_at: string | null
  fetched_at: string
  word_count: number
  excerpt: string
  source: { id: number; slug: string; name: string }
  priority?: PriorityBrief | null // null when not scored for the profile's latest version
  pipeline?: PipelineBrief | null // the list always sends one; detail sends null before any profile exists
}

export type Band = 'Excellent' | 'Good' | 'Fair' | 'Poor' | 'Very Poor'

export interface PriorityBrief {
  score: number
  band: Band
  terms: Record<string, string[]> // keyword chips: the strong buckets (perfect, lolbas, intelligence)
}

export interface PipelineBrief {
  status: PipelineState
  run_id: number | null
  rules: number
}

export interface PriorityDetail extends PriorityBrief {
  threshold: number | null
  profile_version: number
  stale: boolean
  buckets: Record<string, { terms: string[]; count: number; points: number; max_points: number }>
  spans: { start: number; end: number; bucket: string; term: string }[] // UTF-16 offsets into content_text
  spans_truncated: boolean
}

export interface ArticleDetail extends Omit<ArticleSummary, 'priority'> {
  content_text: string
  priority?: PriorityDetail | null
}

export type RunStatus = 'queued' | 'running' | 'filtered_out' | 'succeeded' | 'failed'

export interface StageResult {
  stage: string
  status: 'passed' | 'filtered' | 'failed'
  output: Record<string, unknown>
  compiled_prompt: string | null
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  started_at: string
  finished_at: string
}

export interface Observable {
  id: number
  field_name: string
  value: string
  evidence: string
  evidence_verified: boolean
  tags: Record<string, string>
  evidence_span?: { start: number; end: number } | null // UTF-16 offsets into the article text; verified only
}

export interface RuleQuery {
  id: number
  target: string
  origin: 'converted' | 'llm' | 'human_edit'
  query_text: string | null
  status: 'ok' | 'failed'
  error: string | null
  warnings: string[]
  stale: boolean
  /** Tables the query reads that the organisation doesn't collect (TELEMETRY_TABLES). */
  missing_tables?: string[]
}

export interface DetectionRule {
  id: string
  title: string
  level: string | null
  status: 'pending_review' | 'approved' | 'rejected'
  group: { platform: string; telemetry_category: string }
  observable_ids: number[]
  sigma_yaml: string
  sigma_hash: string
  rationale: string | null
  queries: RuleQuery[]
  /** An earlier rule with the same log source and near-identical values (Jaccard `similarity`). */
  duplicate_of?: { id: string; title: string; status: DetectionRule['status']; article_id: number; similarity: number } | null
}

export interface RuleDetail extends DetectionRule {
  reviewer_note: string | null
  reviewed_at: string | null
  created_at: string
  article: { id: number; title: string; url: string }
}

export type RuleUpdate = Partial<{
  status: DetectionRule['status']
  reviewer_note: string | null
  sigma_yaml: string
  kql: string
  expected_sigma_hash: string
}>

export interface PipelineRun {
  id: number
  article_id: number
  profile: { slug: string; version: number }
  status: RunStatus
  current_step: string | null
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  stages: StageResult[]
  observables: Observable[]
  rules: DetectionRule[]
}

/** The API returns every 4xx as {"detail": {"errors": ["field: message" | "message"]}}. */
export class ApiError extends Error {
  readonly status: number
  readonly errors: string[]

  constructor(status: number, errors: string[]) {
    super(errors[0] ?? `Request failed (${status})`)
    this.status = status
    this.errors = errors
  }

  /** Splits "field: message" errors by field name. Errors with no known field go under "". */
  byField(fields: readonly string[]): Record<string, string> {
    const result: Record<string, string> = {}
    for (const error of this.errors) {
      const match = /^([a-z_]+): (.*)$/s.exec(error)
      const key = match && fields.includes(match[1]) ? match[1] : ''
      result[key] ??= match && key ? match[2] : error
    }
    return result
  }

  /** The first error with any "field: " prefix removed, for one-line messages. */
  get summary(): string {
    return this.message.replace(/^[a-z_]+: /, '')
  }
}

/** One-line reason for any failed request, including network failures that never reached the API. */
export function describeError(error: unknown): string {
  return error instanceof ApiError ? error.summary : 'could not reach the server. Check your connection and try again.'
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const errors = (payload as { detail?: { errors?: unknown } } | null)?.detail?.errors
    throw new ApiError(response.status, Array.isArray(errors) ? errors.map(String) : [])
  }
  return payload as T
}

export const listSources = () => request<Source[]>('GET', '/api/sources')
export const createSource = (body: SourceCreate) => request<Source>('POST', '/api/sources', body)
export const updateSource = (id: number, body: SourceUpdate) => request<Source>('PATCH', `/api/sources/${id}`, body)
export const fetchSourceNow = (id: number) => request<FetchSummary>('POST', `/api/sources/${id}/fetch`)
/** `query`: a query string from articleQuery.toApiQuery ('' for the default list). */
export const listArticles = (query = '') => request<ArticleSummary[]>('GET', `/api/articles${query}`)
export const getArticle = (id: number) => request<ArticleDetail>('GET', `/api/articles/${id}`)
export const listArticleRuns = (articleId: number) => request<PipelineRun[]>('GET', `/api/articles/${articleId}/runs`)
export const queueRun = (articleId: number, profile = 'detection') =>
  request<PipelineRun>('POST', `/api/articles/${articleId}/runs`, { profile })
export const getRun = (id: number) => request<PipelineRun>('GET', `/api/runs/${id}`)
export const cancelRun = (id: number) => request<PipelineRun>('POST', `/api/runs/${id}/cancel`)

/** Whether a worker is processing the queue (a heartbeat in the last 30 s). */
export interface WorkerStatus {
  alive: boolean
  last_seen_at: string | null
  queued: number
  running: number
  orphaned: number // running runs whose worker stopped
}
export const getWorkerStatus = () => request<WorkerStatus>('GET', '/api/worker')
/** Same-origin download of every approved rule as one multi-document Sigma YAML file. */
export const RULES_EXPORT_URL = '/api/rules/export'
export const listRules = (status: DetectionRule['status']) => request<RuleDetail[]>('GET', `/api/rules?status=${status}`)
export const getRuleCounts = () => request<Record<DetectionRule['status'], number>>('GET', '/api/rules/counts')
export const getDashboard = () => request<Dashboard>('GET', '/api/dashboard')

export type PipelineState = 'not_run' | 'in_progress' | 'filtered' | 'failed' | 'no_rules' | 'rules'

export interface Dashboard {
  profile: { slug: string; version: number } | null // null before any profile is seeded
  funnel: {
    fetched: number
    above_threshold: number
    run: number
    relevant: number
    with_rules: number
    approved: number
    threshold: number | null
  }
  volume: { date: string; articles: number }[]
  bands: Record<string, number>
  top: { id: number; title: string; source: string; score: number; band: string; pipeline: PipelineState }[]
  failing_sources: { id: number; name: string; last_error: string; last_checked_at: string | null }[]
  spend: {
    total: number
    by_model: { model: string; input_tokens: number; output_tokens: number; cache_read_tokens: number; cost: number | null }[]
    by_stage: Record<string, number>
    unpriced_models: string[]
  }
}
export const updateRule = (id: string, body: RuleUpdate) => request<RuleDetail>('PATCH', `/api/rules/${id}`, body)
