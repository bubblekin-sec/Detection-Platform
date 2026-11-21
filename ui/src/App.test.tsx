import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { App } from './App'
import { fakeApi } from './test/fakeApi'

const counts = { pending_review: 3, approved: 1, rejected: 0 }
const emptyDashboard = {
  profile: { slug: 'detection', version: 2 },
  funnel: { fetched: 0, above_threshold: 0, run: 0, relevant: 0, with_rules: 0, approved: 0, threshold: 37.5 },
  volume: [],
  bands: { Excellent: 0, Good: 0, Fair: 0, Poor: 0, 'Very Poor': 0 },
  top: [],
  failing_sources: [],
  spend: { total: 0, by_model: [], by_stage: {}, unpriced_models: [] },
}

afterEach(() => window.history.pushState(null, '', '/'))

describe('App shell', () => {
  it('opens on the dashboard with a sidebar showing the review count', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/dashboard': () => ({ status: 200, body: emptyDashboard }),
    })

    render(<App />)

    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(await within(nav).findByRole('link', { name: 'Review, 3 pending' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Dashboard', level: 1 })).toBeInTheDocument()
  })

  it('navigates with the sidebar and marks the current page', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/dashboard': () => ({ status: 200, body: emptyDashboard }),
      'GET /api/sources': () => ({ status: 200, body: [] }),
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('link', { name: 'Sources' }))

    expect(window.location.pathname).toBe('/sources')
    expect(screen.getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page')
    expect(await screen.findByRole('heading', { name: 'Sources', level: 1 })).toBeInTheDocument()
  })

  it('leaves modified clicks to the browser so links can open in a new tab', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/dashboard': () => ({ status: 200, body: emptyDashboard }),
    })
    render(<App />)

    const link = screen.getByRole('link', { name: 'Sources' })
    fireEvent.click(link, { ctrlKey: true })
    fireEvent.click(link, { button: 1 })

    expect(window.location.pathname).toBe('/')
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('heading', { name: 'Sources' })).not.toBeInTheDocument()
  })

  it('updates the review count when a rule is reviewed', async () => {
    let pendingCount = 1
    const rule = {
      id: 'r1', title: 'Rule one', level: 'high', status: 'pending_review',
      group: { platform: 'windows', telemetry_category: 'process_creation' }, observable_ids: [],
      sigma_yaml: 'title: Rule one\n', sigma_hash: 'a'.repeat(64), rationale: '', queries: [], reviewer_note: null,
      reviewed_at: null, created_at: '2026-11-21T12:00:00Z', article: { id: 1, title: 'Source article', url: 'https://x' },
    }
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: { ...counts, pending_review: pendingCount } }),
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [rule] }),
      'PATCH /api/rules/r1': () => {
        pendingCount = 0
        return { status: 200, body: { ...rule, status: 'approved' } }
      },
    })
    window.history.pushState(null, '', '/review')
    const user = userEvent.setup()
    render(<App />)
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(await within(nav).findByRole('link', { name: 'Review, 1 pending' })).toBeInTheDocument()

    await user.click(within(await screen.findByRole('article', { name: 'Rule one' })).getByRole('button', { name: 'Approve' }))

    expect(await within(nav).findByRole('link', { name: 'Review' })).toBeInTheDocument()
  })
})
