import type { DetectionRule } from './api'

/** The review page's view: by article (the default) or the flat rule list, and the rule status shown. */
export interface ReviewQuery {
  view: 'articles' | 'rules'
  status: DetectionRule['status']
}

const STATUSES: DetectionRule['status'][] = ['pending_review', 'approved', 'rejected']

export function parseReviewQuery(search: string): ReviewQuery {
  const params = new URLSearchParams(search)
  const status = params.get('status')
  return {
    view: params.get('view') === 'rules' ? 'rules' : 'articles',
    status: STATUSES.find((candidate) => candidate === status) ?? 'pending_review',
  }
}

/** Defaults are left out, so the plain review page stays `/review`. */
export function toReviewSearch(query: ReviewQuery): string {
  const params = new URLSearchParams()
  if (query.view !== 'articles') params.set('view', query.view)
  if (query.status !== 'pending_review') params.set('status', query.status)
  const search = params.toString()
  return search ? `?${search}` : ''
}
