import type { Band, PipelineState } from './api'

/** The article queue's view state. It lives in the URL query string, so a filtered view can be linked and reloaded. */
export interface ArticleQuery {
  sort: 'published' | 'priority' | 'source'
  order?: 'asc' | 'desc' // unset: the API's default for the sort (A to Z for source, highest/newest first otherwise)
  band?: Band
  status?: PipelineState
  sourceId?: number
  page: number // 0-based
}

export const PAGE_SIZE = 50 // the API's default limit, so the default request needs no parameters
export const SORTS = ['published', 'priority', 'source'] as const
export const BANDS: Band[] = ['Excellent', 'Good', 'Fair', 'Poor', 'Very Poor']
export const STATUSES: PipelineState[] = ['not_run', 'in_progress', 'filtered', 'failed', 'no_rules', 'rules']

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | undefined {
  return allowed.includes(value as T) ? (value as T) : undefined
}

function wholeNumber(value: string | null): number | undefined {
  // Digits only, and small enough to stay an exact integer (a huge page would serialise as "5e+21").
  return value !== null && /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : undefined
}

/** Parse a location.search string; unknown or malformed values are ignored rather than sent to the API. */
export function parseArticleQuery(search: string): ArticleQuery {
  const params = new URLSearchParams(search)
  return {
    sort: oneOf(params.get('sort'), SORTS) ?? 'published',
    order: oneOf(params.get('order'), ['asc', 'desc'] as const),
    band: oneOf(params.get('band'), BANDS),
    status: oneOf(params.get('status'), STATUSES),
    sourceId: wholeNumber(params.get('source_id')),
    page: wholeNumber(params.get('page')) ?? 0,
  }
}

function entries(query: ArticleQuery): [string, string][] {
  const pairs: [string, string | undefined][] = [
    ['source_id', query.sourceId?.toString()],
    ['sort', query.sort === 'published' ? undefined : query.sort],
    ['order', query.order],
    ['band', query.band],
    ['status', query.status],
  ]
  return pairs.filter((pair): pair is [string, string] => pair[1] !== undefined)
}

function encode(pairs: [string, string][]): string {
  return pairs.length ? `?${new URLSearchParams(pairs).toString()}` : ''
}

/** The browser URL's query string for this view ('' for the default view). */
export function toSearch(query: ArticleQuery): string {
  return encode([...entries(query), ...(query.page ? [['page', String(query.page)] as [string, string]] : [])])
}

/** The /api/articles query string for this view. */
export function toApiQuery(query: ArticleQuery): string {
  const offset = query.page * PAGE_SIZE
  return encode([...entries(query), ...(offset ? [['offset', String(offset)] as [string, string]] : [])])
}
