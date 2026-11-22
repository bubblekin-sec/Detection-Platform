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
    window.history.pushState(null, '', '/review?view=rules')
    const user = userEvent.setup()
    render(<App />)
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(await within(nav).findByRole('link', { name: 'Review, 1 pending' })).toBeInTheDocument()

    await user.click(within(await screen.findByRole('article', { name: 'Rule one' })).getByRole('button', { name: 'Approve' }))

    expect(await within(nav).findByRole('link', { name: 'Review' })).toBeInTheDocument()
  })

  it('opens the priority queue from the dashboard', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/dashboard': () => ({ status: 200, body: emptyDashboard }),
      'GET /api/sources': () => ({ status: 200, body: [] }),
      'GET /api/articles?sort=priority': () => ({ status: 200, body: [] }),
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('link', { name: /View queue/ }))

    expect(window.location.search).toBe('?sort=priority')
    expect(await screen.findByLabelText('Sort by')).toHaveValue('priority')
  })

  it('returns from an article to the queue view the user left', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/sources': () => ({ status: 200, body: [] }),
      'GET /api/articles?sort=priority&band=Excellent': () => ({
        status: 200,
        body: [{ id: 5, url: 'https://x', title: 'Queued article', author: null, published_at: null,
                 fetched_at: '2026-11-21T00:00:00Z', word_count: 10, excerpt: '', source: { id: 1, slug: 's', name: 'S' } }],
      }),
      'GET /api/articles/5': () => ({
        status: 200,
        body: { id: 5, url: 'https://x', title: 'Queued article', author: null, published_at: null,
                fetched_at: '2026-11-21T00:00:00Z', word_count: 10, excerpt: '', source: { id: 1, slug: 's', name: 'S' },
                content_text: 'Body.', priority: null, pipeline: null },
      }),
      'GET /api/articles/5/runs': () => ({ status: 200, body: [] }),
    })
    window.history.pushState(null, '', '/articles?sort=priority&band=Excellent')
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('link', { name: 'Queued article' }))
    await user.click(await screen.findByRole('button', { name: /All articles/ }))

    expect(window.location.search).toBe('?sort=priority&band=Excellent')
    expect(await screen.findByLabelText('Priority band')).toHaveValue('Excellent')
  })

  it('follows browser back and forward between queue views', async () => {
    const api = fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/sources': () => ({ status: 200, body: [] }),
      'GET /api/articles': () => ({ status: 200, body: [] }),
      'GET /api/articles?sort=priority': () => ({ status: 200, body: [] }),
    })
    window.history.pushState(null, '', '/articles')
    const user = userEvent.setup()
    render(<App />)
    await user.selectOptions(await screen.findByLabelText('Sort by'), 'Priority score')
    expect(window.location.search).toBe('?sort=priority')

    window.history.back()
    await new Promise((resolve) => window.addEventListener('popstate', resolve, { once: true }))

    expect(await screen.findByLabelText('Sort by')).toHaveValue('published')
    expect(api.calls.at(-1)?.route).toBe('GET /api/articles')
  })
})

describe('Reviewing by article', () => {
  const article = (id: number, title: string) => ({
    id, url: `https://r.example.com/${id}`, title, author: null, published_at: null, fetched_at: '2026-11-21T10:00:00Z',
    word_count: 100, excerpt: '', source: { id: 1, slug: 's', name: 'Source' }, priority: null, content_text: 'Text.',
  })
  const rule = (id: string, articleId: number) => ({
    id, run_id: 7, title: `Rule ${id}`, level: 'high', status: 'pending_review',
    group: { platform: 'windows', telemetry_category: 'process_creation' }, observable_ids: [],
    sigma_yaml: `title: Rule ${id}\n`, sigma_hash: 'a'.repeat(64), rationale: null, queries: [], reviewer_note: null,
    reviewed_at: null, created_at: '2026-11-21T12:00:00Z', article: { id: articleId, title: 'A', url: 'https://x' },
  })
  const row = (id: number, title: string) => ({
    article: { id, title, url: `https://r.example.com/${id}`, source: { id: 1, name: 'Source' } },
    priority: null, relevance: null, run_id: 7, rules: { pending_review: 1, approved: 0, rejected: 0 }, duplicates: 0,
    last_decided_at: null,
  })

  it('goes from the queue to an article, decides its rules, moves to the next one and back to the queue', async () => {
    const queue = [row(10, 'First article'), row(11, 'Second article')]
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/rules/by-article?status=pending_review': () => ({ status: 200, body: queue }),
      'GET /api/articles/10': () => ({ status: 200, body: article(10, 'First article') }),
      'GET /api/articles/10/runs': () => ({ status: 200, body: [] }),
      'GET /api/rules?article_id=10': () => ({ status: 200, body: [rule('a', 10)] }),
      'PATCH /api/rules/a': () => {
        queue.shift()
        return { status: 200, body: { ...rule('a', 10), status: 'approved' } }
      },
      'GET /api/articles/11': () => ({ status: 200, body: article(11, 'Second article') }),
      'GET /api/articles/11/runs': () => ({ status: 200, body: [] }),
      'GET /api/rules?article_id=11': () => ({ status: 200, body: [rule('b', 11)] }),
    })
    window.history.pushState(null, '', '/review')
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('link', { name: 'First article' }))
    expect(window.location.pathname + window.location.search).toBe('/articles/10?view=rules')
    await user.click(within(await screen.findByRole('article', { name: 'Rule a' })).getByRole('button', { name: 'Approve' }))
    await user.click(await screen.findByRole('button', { name: 'Next article in queue →' }))

    expect(await screen.findByRole('heading', { name: 'Second article', level: 1 })).toBeInTheDocument()
    expect(window.location.pathname + window.location.search).toBe('/articles/11?view=rules')
    await user.click(screen.getByRole('button', { name: '← Review queue' }))

    expect(window.location.pathname).toBe('/review')
    expect(await screen.findByRole('link', { name: 'Second article' })).toBeInTheDocument()
  })

  it('offers no next article when the reader came from the article list', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/articles/10': () => ({ status: 200, body: article(10, 'First article') }),
      'GET /api/articles/10/runs': () => ({ status: 200, body: [] }),
      'GET /api/rules?article_id=10': () => ({ status: 200, body: [{ ...rule('a', 10), status: 'approved' }] }),
    })
    window.history.pushState(null, '', '/articles/10?view=rules')
    render(<App />)

    expect(await screen.findByText(/All 1 rule decided/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Next article/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '← All articles' })).toBeInTheDocument()
  })
})

describe('Next article in queue', () => {
  it('says so when no other article is waiting', async () => {
    fakeApi({
      'GET /api/rules/counts': () => ({ status: 200, body: counts }),
      'GET /api/rules/by-article?status=pending_review': () => ({
        status: 200,
        body: [{
          article: { id: 10, title: 'Only article', url: 'https://x', source: { id: 1, name: 'Source' } },
          priority: null, relevance: null, run_id: 7, rules: { pending_review: 0, approved: 1, rejected: 0 }, duplicates: 0,
          last_decided_at: null,
        }],
      }),
      'GET /api/articles/10': () => ({
        status: 200,
        body: { id: 10, url: 'https://x', title: 'Only article', author: null, published_at: null, fetched_at: '2026-11-21T10:00:00Z',
          word_count: 1, excerpt: '', source: { id: 1, slug: 's', name: 'Source' }, priority: null, content_text: 'Text.' },
      }),
      'GET /api/articles/10/runs': () => ({ status: 200, body: [] }),
      'GET /api/rules?article_id=10': () => ({
        status: 200,
        body: [{ id: 'a', run_id: 7, title: 'Rule a', level: 'high', status: 'approved',
          group: { platform: 'windows', telemetry_category: 'process_creation' }, observable_ids: [], sigma_yaml: 'title: a\n',
          sigma_hash: 'a'.repeat(64), rationale: null, queries: [], reviewer_note: null, reviewed_at: null,
          created_at: '2026-11-21T12:00:00Z', article: { id: 10, title: 'Only article', url: 'https://x' } }],
      }),
    })
    window.history.pushState(null, '', '/review')
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('link', { name: 'Only article' }))
    await user.click(await screen.findByRole('button', { name: 'Next article in queue →' }))

    expect(await screen.findByText('No other article is waiting for review.')).toBeInTheDocument()
  })
})
