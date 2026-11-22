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
  rules: [],
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

  it('says when no worker is running and how to start one, and cancels the queued run', async () => {
    const cancelled: PipelineRun = { ...base, status: 'failed', error: 'Cancelled before it started.' }
    let current = base
    const api = fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [base] }),
      'GET /api/runs/7': () => ({ status: 200, body: current }),
      'GET /api/worker': () => ({ status: 200, body: { alive: false, last_seen_at: null, queued: 1, running: 0 } }),
      'POST /api/runs/7/cancel': () => {
        current = cancelled
        return { status: 200, body: cancelled }
      },
    })
    const user = userEvent.setup()
    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText('No worker is running')).toBeInTheDocument()
    expect(screen.getByText('uv run python -m app.worker')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByText(/Cancelled before it started\./)).toBeInTheDocument()
    expect(screen.queryByText('No worker is running')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run again' })).toBeEnabled()
    expect(api.calls.some((call) => call.route === 'POST /api/runs/7/cancel')).toBe(true)
  })

  it('says the run is next when a worker is busy with another run', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [base] }),
      'GET /api/runs/7': () => ({ status: 200, body: base }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: '2026-11-22T12:00:00Z', queued: 1, running: 1 } }),
    })
    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText(/busy and will pick this one up when it is free/)).toBeInTheDocument()
    expect(screen.queryByText('No worker is running')).not.toBeInTheDocument()
  })

  it('says when the worker processing a running run stopped, and lets the reviewer cancel it', async () => {
    const running: PipelineRun = { ...base, status: 'running', current_step: 'extraction' }
    let current = running
    fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [running] }),
      'GET /api/runs/7': () => ({ status: 200, body: current }),
      'GET /api/worker': () => ({ status: 200, body: { alive: false, last_seen_at: null, queued: 0, running: 1, orphaned: 1 } }),
      'POST /api/runs/7/cancel': () => {
        current = { ...running, status: 'failed', error: 'The worker processing this run stopped before finishing. Run it again.' }
        return { status: 200, body: current }
      },
    })
    const user = userEvent.setup()
    render(<RunPanel articleId={10} pollMs={5} />)

    expect(await screen.findByText('The worker processing this run stopped')).toBeInTheDocument()
    expect(screen.queryByText(/Running: extraction/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByText(/stopped before finishing\. Run it again\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run again' })).toBeEnabled()
  })

  it('explains a cancel that lost the race to the worker', async () => {
    fakeApi({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [base] }),
      'GET /api/runs/7': () => ({ status: 200, body: base }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: null, queued: 1, running: 0 } }),
      'POST /api/runs/7/cancel': () => ({
        status: 409,
        body: { detail: { errors: ['Run 7 is running; only a queued run can be cancelled.'] } },
      }),
    })
    const user = userEvent.setup()
    render(<RunPanel articleId={10} pollMs={5} />)

    await user.click(await screen.findByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('only a queued run can be cancelled')
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

  it('shows groups that produced no rule and groups skipped by the cap', async () => {
    const ruleStage = {
      ...extractionStage,
      stage: 'rule_generation',
      status: 'failed' as const,
      output: {
        rules: 0,
        skipped_groups: 2,
        groups: [
          { platform: 'windows', telemetry_category: 'registry_set', observables: 1, outcome: 'invalid', errors: ["Detection 'x' not defined in detections"] },
          { platform: 'windows', telemetry_category: 'file_event', observables: 3, outcome: 'llm_error', error: 'Rate limited by the Anthropic API (429) after retries.' },
        ],
      },
    }
    fakeApi({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'failed', current_step: 'rule_generation', error: 'No valid rule', stages: [relevance(8, 'passed'), extractionStage, ruleStage] }],
      }),
    })

    render(<RunPanel articleId={10} pollMs={5} />)

    const problems = await screen.findByRole('note', { name: 'Groups without a rule' })
    expect(problems).toHaveTextContent('Sigma still invalid after a repair attempt')
    expect(problems).toHaveTextContent("Detection 'x' not defined in detections")
    expect(problems).toHaveTextContent('Model call failed — Rate limited')
    expect(problems).toHaveTextContent('2 more groups skipped by the per-run limit')
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
