import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { DashboardPage } from './DashboardPage'
import type { Dashboard } from './api'
import { fakeApi } from './test/fakeApi'

const dashboard: Dashboard = {
  profile: { slug: 'detection', version: 2 },
  funnel: { fetched: 715, above_threshold: 109, run: 5, relevant: 2, with_rules: 2, approved: 1, threshold: 37.5 },
  volume: [
    { date: '2026-11-20', articles: 12 },
    { date: '2026-11-21', articles: 40 },
  ],
  bands: { Excellent: 27, Good: 33, Fair: 42, Poor: 8, 'Very Poor': 605 },
  top: [
    { id: 11, title: 'GeoVision Camera Software Abused', source: 'Huntress', score: 92.8, band: 'Excellent', pipeline: 'not_run' },
    { id: 99, title: 'UAT-12643 North Korea-aligned actor', source: 'Cisco Talos', score: 80.6, band: 'Excellent', pipeline: 'rules' },
  ],
  failing_sources: [{ id: 3, name: 'Broken Feed', last_error: 'Feed returned HTTP 404.', last_checked_at: '2026-11-21T10:00:00Z' }],
  spend: {
    total: 0.4018,
    by_model: [{ model: 'claude-opus-5-5', input_tokens: 41379, output_tokens: 11610, cache_read_tokens: 0, cost: 0.3977 }],
    by_stage: { extraction: 0.2246, rule_generation: 0.1731, relevance: 0.0041 },
    unpriced_models: [],
  },
}

describe('DashboardPage', () => {
  it('shows the funnel as stat cards and an accessible chart', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: dashboard }) })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    const funnel = await screen.findByRole('table', { name: 'Pipeline funnel' })
    expect(within(funnel).getByRole('row', { name: /Above threshold \(37\.5\) 109/ })).toBeInTheDocument()
    const articles = screen.getByText('Articles', { selector: '.stat-label' }).closest('.stat-card') as HTMLElement
    expect(within(articles).getByText('715')).toBeInTheDocument()
    expect(screen.getByText('$0.40', { selector: '.stat-value' })).toBeInTheDocument()
  })

  it('charts daily volume and score bands', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: dashboard }) })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    const volume = await screen.findByRole('table', { name: 'Articles fetched per day' })
    expect(within(volume).getByRole('row', { name: /21 Nov 40/ })).toBeInTheDocument()
    const bands = screen.getByRole('table', { name: 'Priority score bands' })
    expect(within(bands).getByRole('row', { name: /Very Poor 605/ })).toBeInTheDocument()
  })

  it('links to the priority-sorted queue', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: dashboard }) })
    const paths: string[] = []
    const user = userEvent.setup()
    render(<DashboardPage onOpenArticle={() => {}} onNavigate={(path) => paths.push(path)} />)

    await user.click(await screen.findByRole('link', { name: /View queue/ }))

    expect(paths).toEqual(['/articles?sort=priority'])
  })

  it('names each score badge with its band for screen readers', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: dashboard }) })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    const list = await screen.findByRole('list', { name: 'Top priority articles' })
    expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('92.8, Excellent')
  })

  it('explains what to do before any profile is seeded', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: { ...dashboard, profile: null, top: [] } }) })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    expect(await screen.findByRole('status')).toHaveTextContent('No detection profile yet')
  })

  it('lists top-priority articles that open on click', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: dashboard }) })
    const opened: number[] = []
    const user = userEvent.setup()
    render(<DashboardPage onOpenArticle={(id) => opened.push(id)} onNavigate={() => {}} />)

    const list = await screen.findByRole('list', { name: 'Top priority articles' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(within(list).getByText('Has rules')).toBeInTheDocument()
    await user.click(within(list).getByRole('link', { name: /GeoVision Camera Software Abused/ }))

    expect(opened).toEqual([11])
  })

  it('shows failing sources and spend by stage', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 200, body: dashboard }) })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    expect(await screen.findByText('Broken Feed')).toBeInTheDocument()
    expect(screen.getByText('Feed returned HTTP 404.')).toBeInTheDocument()
    const stages = screen.getByRole('table', { name: 'Model spend by stage' })
    expect(within(stages).getByRole('row', { name: /extraction \$0\.22/ })).toBeInTheDocument()
  })

  it('handles an empty platform', async () => {
    fakeApi({
      'GET /api/dashboard': () => ({
        status: 200,
        body: {
          ...dashboard,
          funnel: { ...dashboard.funnel, fetched: 0, above_threshold: 0, run: 0, relevant: 0, with_rules: 0, approved: 0 },
          top: [],
          failing_sources: [],
          spend: { total: 0, by_model: [], by_stage: {}, unpriced_models: [] },
        },
      }),
    })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    expect(await screen.findByText(/No scored articles yet/)).toBeInTheDocument()
    expect(screen.getByText('All sources healthy.')).toBeInTheDocument()
    expect(screen.getByText('No model spend yet.')).toBeInTheDocument()
  })

  it('reports a load failure', async () => {
    fakeApi({ 'GET /api/dashboard': () => ({ status: 500, body: {} }) })

    render(<DashboardPage onOpenArticle={() => {}} onNavigate={() => {}} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the dashboard')
  })
})
