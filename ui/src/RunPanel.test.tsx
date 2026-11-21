import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { RunPanel } from './RunPanel'
import type { PipelineRun } from './api'
import { fakeApi } from './test/fakeApi'

const base: PipelineRun = {
  id: 7,
  article_id: 10,
  profile: { slug: 'detection', version: 1 },
  status: 'queued',
  current_step: null,
  error: null,
  created_at: '2026-11-21T12:00:00Z',
  started_at: null,
  finished_at: null,
  stages: [],
  observables: [],
}

function relevance(score: number, status: 'passed' | 'filtered') {
  return {
    stage: 'relevance',
    status,
    output: { score, min_score: 6, rationale: 'Concrete scheduled task persistence.', signals: ['schtasks /create', 'Run key'] },
    compiled_prompt: 'You triage…',
    model: 'claude-haiku-5-5',
    input_tokens: 1200,
    output_tokens: 150,
    cache_read_tokens: 0,
    started_at: '2026-11-21T12:00:01Z',
    finished_at: '2026-11-21T12:00:03Z',
  }
}

const extractionStage = {
  ...relevance(0, 'passed'),
  stage: 'extraction',
  output: { total: 0, unverified: 0, counts: {} },
  model: 'claude-opus-5-5',
}

describe('RunPanel', () => {
  it('queues a run and shows the relevance result when it finishes', async () => {
    let polls = 0
    const api = fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [] }),
      'POST /api/articles/10/runs': () => ({ status: 202, body: base }),
      'GET /api/runs/7': () => {
        polls += 1
        return polls < 2
          ? { status: 200, body: { ...base, status: 'running', current_step: 'relevance' } }
          : { status: 200, body: { ...base, status: 'succeeded', current_step: 'relevance', stages: [relevance(8, 'passed')] } }
      },
    })
    const user = userEvent.setup()
    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText('Not run yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run detection' }))

    expect(await screen.findByText('Relevant: 8/10 (needs 6)')).toBeInTheDocument()
    expect(screen.getByText('Concrete scheduled task persistence.')).toBeInTheDocument()
    expect(screen.getByText('schtasks /create')).toBeInTheDocument()
    expect(screen.getByText(/claude-haiku-5-5 · 1,350 tokens/)).toBeInTheDocument()
    expect(api.calls.find((call) => call.route === 'POST /api/articles/10/runs')?.body).toEqual({ profile: 'detection' })
  })

  it('shows an existing filtered-out run on load', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'filtered_out', current_step: 'relevance', stages: [relevance(3, 'filtered')] }],
      }),
    })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText('Not relevant: 3/10 (needs 6)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run again' })).toBeEnabled()
  })

  it('shows why a run failed', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'failed', current_step: 'relevance', error: 'Rate limited by the Anthropic API (429) after retries.' }],
      }),
    })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Failed at relevance: Rate limited by the Anthropic API (429)')
  })

  it('lists extracted observables by field and warns about unverified evidence', async () => {
    const extracted: PipelineRun = {
      ...base,
      status: 'succeeded',
      current_step: 'extraction',
      stages: [relevance(8, 'passed'), extractionStage],
      observables: [
        {
          id: 1,
          field_name: 'scheduled_tasks',
          value: 'schtasks /create /tn Updater /tr C:\\Users\\Public\\u.exe',
          evidence: 'The operators ran schtasks /create /tn Updater on each host.',
          evidence_verified: true,
          tags: { platform: 'windows', telemetry_category: 'process_creation' },
        },
        {
          id: 2,
          field_name: 'network_indicators',
          value: 'evil.example.net',
          evidence: 'beaconed to evil.example.net',
          evidence_verified: false,
          tags: { platform: 'windows', telemetry_category: 'network_connection' },
        },
      ],
    }
    fakeApi({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [extracted] }) })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByRole('heading', { name: 'Scheduled tasks (1)' })).toBeInTheDocument()
    expect(screen.getByText('schtasks /create /tn Updater /tr C:\\Users\\Public\\u.exe')).toBeInTheDocument()
    expect(screen.getByText('telemetry category: process_creation')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Network indicators (1)' })).toBeInTheDocument()
    const [taskItem, networkItem] = screen.getAllByRole('listitem').filter((li) => li.querySelector('code'))
    expect(within(networkItem).getByText(/Unverified:/)).toBeInTheDocument()
    expect(within(taskItem).queryByText(/Unverified:/)).not.toBeInTheDocument()
    expect(screen.getByText(/2 observables, 1 unverified · claude-opus-5-5/)).toBeInTheDocument()
  })

  it('says when a relevant article had nothing to extract', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'succeeded', current_step: 'extraction', stages: [relevance(7, 'passed'), extractionStage] }],
      }),
    })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText(/No observables were extracted/)).toBeInTheDocument()
  })

  it('stops polling once the run finishes', async () => {
    let polls = 0
    fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...base, status: 'running', current_step: 'relevance' }] }),
      'GET /api/runs/7': () => {
        polls += 1
        return { status: 200, body: { ...base, status: 'filtered_out', current_step: 'relevance', stages: [relevance(2, 'filtered')] } }
      },
    })

    render(<RunPanel articleId={10} pollMs={5} />)
    await screen.findByText('Not relevant: 2/10 (needs 6)')
    const settled = polls
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(polls).toBe(settled)
  })

  it('explains a junk-filter skip', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...base, status: 'filtered_out', current_step: 'junk_filter' }] }),
    })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText(/Skipped: the article is too short/)).toBeInTheDocument()
  })

  it('still allows queueing when previous runs cannot be loaded', async () => {
    fakeApi({ 'GET /api/articles/10/runs': () => ({ status: 500, body: {} }) })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load previous runs')
    expect(screen.getByRole('button', { name: 'Run detection' })).toBeEnabled()
  })

  it('disables the button while a run is in progress', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...base, status: 'running', current_step: 'relevance' }] }),
      'GET /api/runs/7': () => ({ status: 200, body: { ...base, status: 'running', current_step: 'relevance' } }),
    })

    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText(/Running: relevance/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run detection' })).toBeDisabled()
  })
})
