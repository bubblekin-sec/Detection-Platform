import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ArticleReader } from './ArticleReader'
import type { ReaderView } from './readerView'
import type { ArticleDetail, PipelineRun, RuleDetail } from './api'
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


const detail: ArticleDetail = {
  id: 10,
  url: 'https://research.example.com/paper-lantern',
  title: 'Operation Paper Lantern',
  author: 'Research Team',
  published_at: '2026-11-17T09:00:00Z',
  fetched_at: '2026-11-21T10:00:00Z',
  word_count: 1840,
  excerpt: 'The operators…',
  source: { id: 1, slug: 'cisco_talos', name: 'Cisco Talos' },
  priority: null,
  content_text: 'First paragraph.\nschtasks /create /tn Updater',
}
const ARTICLE = 'GET /api/articles/10'
const RULES = 'GET /api/rules?article_id=10'

/** Routes every article page loads, before the test's own (which may override them). */
function page(routes: Parameters<typeof fakeApi>[0], rules: RuleDetail[] = []) {
  return fakeApi({
    [ARTICLE]: () => ({ status: 200, body: detail }),
    [RULES]: () => ({ status: 200, body: rules }),
    ...routes,
  })
}

function renderReader(view: ReaderView | undefined, props: Partial<Parameters<typeof ArticleReader>[0]> = {}) {
  return render(<ArticleReader id={10} view={view} onBack={() => {}} pollMs={5} {...props} />)
}

describe('ArticleReader run', () => {
  it('queues a run and shows the relevance result when it finishes', async () => {
    let polls = 0
    const api = page({
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
    renderReader('filtering')

    expect(await screen.findByRole('status')).toHaveTextContent('Not run yet.')
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
    const api = page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [base] }),
      'GET /api/runs/7': () => ({ status: 200, body: current }),
      'GET /api/worker': () => ({ status: 200, body: { alive: false, last_seen_at: null, queued: 1, running: 0 } }),
      'POST /api/runs/7/cancel': () => {
        current = cancelled
        return { status: 200, body: cancelled }
      },
    })
    const user = userEvent.setup()
    renderReader('filtering')

    expect(await screen.findByText('No worker is running')).toBeInTheDocument()
    expect(screen.getByText('uv run python -m app.worker')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByText(/Cancelled before it started\./)).toBeInTheDocument()
    expect(screen.queryByText('No worker is running')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run again' })).toBeEnabled()
    expect(api.calls.some((call) => call.route === 'POST /api/runs/7/cancel')).toBe(true)
  })

  it('says the run is next when a worker is busy with another run', async () => {
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [base] }),
      'GET /api/runs/7': () => ({ status: 200, body: base }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: '2026-11-22T12:00:00Z', queued: 1, running: 1 } }),
    })
    renderReader('filtering')

    expect(await screen.findByText(/busy and will pick this one up when it is free/)).toBeInTheDocument()
    expect(screen.queryByText('No worker is running')).not.toBeInTheDocument()
  })

  it('says when the worker processing a running run stopped, and lets the reviewer cancel it', async () => {
    const running: PipelineRun = { ...base, status: 'running', current_step: 'extraction' }
    let current = running
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [running] }),
      'GET /api/runs/7': () => ({ status: 200, body: current }),
      'GET /api/worker': () => ({ status: 200, body: { alive: false, last_seen_at: null, queued: 0, running: 1, orphaned: 1 } }),
      'POST /api/runs/7/cancel': () => {
        current = { ...running, status: 'failed', error: 'The worker processing this run stopped before finishing. Run it again.' }
        return { status: 200, body: current }
      },
    })
    const user = userEvent.setup()
    renderReader('filtering')

    expect(await screen.findByText('The worker processing this run stopped')).toBeInTheDocument()
    expect(screen.queryByText(/running: extraction/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByText(/stopped before finishing\. Run it again\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run again' })).toBeEnabled()
  })

  it('explains a cancel that lost the race to the worker', async () => {
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [base] }),
      'GET /api/runs/7': () => ({ status: 200, body: base }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: null, queued: 1, running: 0 } }),
      'POST /api/runs/7/cancel': () => ({
        status: 409,
        body: { detail: { errors: ['Run 7 is running; only a queued run can be cancelled.'] } },
      }),
    })
    const user = userEvent.setup()
    renderReader('filtering')

    await user.click(await screen.findByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('only a queued run can be cancelled')
  })

  it('shows an existing filtered-out run on load', async () => {
    page({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'filtered_out', current_step: 'relevance', stages: [relevance(3, 'filtered')] }],
      }),
    })

    renderReader('filtering')

    expect(await screen.findByText('Not relevant: 3/10 (needs 6)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run again' })).toBeEnabled()
  })

  it('shows why a run failed', async () => {
    page({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'failed', current_step: 'relevance', error: 'Rate limited by the Anthropic API (429) after retries.' }],
      }),
    })

    renderReader('filtering')

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
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [extracted] }) })

    renderReader('extraction')

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
    page({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'succeeded', current_step: 'extraction', stages: [relevance(7, 'passed'), extractionStage] }],
      }),
    })

    renderReader('extraction')

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
    page({
      'GET /api/articles/10/runs': () => ({
        status: 200,
        body: [{ ...base, status: 'failed', current_step: 'rule_generation', error: 'No valid rule', stages: [relevance(8, 'passed'), extractionStage, ruleStage] }],
      }),
    })

    renderReader('rules')

    const problems = await screen.findByRole('note', { name: 'Groups without a rule' })
    expect(problems).toHaveTextContent('Sigma still invalid after a repair attempt')
    expect(problems).toHaveTextContent("Detection 'x' not defined in detections")
    expect(problems).toHaveTextContent('Model call failed — Rate limited')
    expect(problems).toHaveTextContent('2 more groups skipped by the per-run limit')
  })

  it('stops polling once the run finishes', async () => {
    let polls = 0
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...base, status: 'running', current_step: 'relevance' }] }),
      'GET /api/runs/7': () => {
        polls += 1
        return { status: 200, body: { ...base, status: 'filtered_out', current_step: 'relevance', stages: [relevance(2, 'filtered')] } }
      },
    })

    renderReader('filtering')
    await screen.findByText('Not relevant: 2/10 (needs 6)')
    const settled = polls
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(polls).toBe(settled)
  })

  it('explains a junk-filter skip', async () => {
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...base, status: 'filtered_out', current_step: 'junk_filter' }] }),
    })

    renderReader('filtering')

    expect(await screen.findByText(/Skipped: the article is too short/)).toBeInTheDocument()
  })

  it('still allows queueing when previous runs cannot be loaded', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 500, body: {} }) })

    renderReader('filtering')

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load previous runs')
    expect(screen.getByRole('button', { name: 'Run detection' })).toBeEnabled()
  })

  it('disables the button while a run is in progress', async () => {
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...base, status: 'running', current_step: 'relevance' }] }),
      'GET /api/runs/7': () => ({ status: 200, body: { ...base, status: 'running', current_step: 'relevance' } }),
    })

    renderReader('filtering')

    expect(await screen.findByText(/Run #7 running: relevance/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run detection' })).toBeDisabled()
  })
})

const pendingRule: RuleDetail = {
  id: 'rule-a',
  run_id: 7,
  title: 'Scheduled Task Created Pointing to Public Folder',
  level: 'high',
  status: 'pending_review',
  group: { platform: 'windows', telemetry_category: 'process_creation' },
  observable_ids: [1],
  sigma_yaml: 'title: Scheduled Task Created Pointing to Public Folder\n',
  sigma_hash: 'a'.repeat(64),
  rationale: null,
  queries: [],
  reviewer_note: null,
  reviewed_at: null,
  created_at: '2026-11-21T12:00:00Z',
  article: { id: 10, title: 'Operation Paper Lantern', url: detail.url },
}
const secondRule: RuleDetail = { ...pendingRule, id: 'rule-b', title: 'Run Key Written by Updater', sigma_hash: 'b'.repeat(64) }
const finished: PipelineRun = {
  ...base,
  status: 'succeeded',
  current_step: 'rule_generation',
  finished_at: '2026-11-21T12:05:00Z',
  stages: [relevance(8, 'passed'), extractionStage, { ...extractionStage, stage: 'rule_generation', output: { rules: 2 } }],
  observables: [
    { id: 1, field_name: 'scheduled_tasks', value: 'schtasks', evidence: 'ran schtasks', evidence_verified: true, tags: {} },
    { id: 2, field_name: 'registry', value: 'HKLM', evidence: 'wrote HKLM', evidence_verified: false, tags: {} },
  ],
}

function strip() {
  return screen.getByRole('navigation', { name: 'Pipeline stages' })
}

describe('ArticleReader stages', () => {
  it('summarises each stage as text and opens on Rules while a rule is pending', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [finished] }) }, [pendingRule, { ...secondRule, status: 'approved' }])

    renderReader(undefined)

    expect(await screen.findByRole('article', { name: pendingRule.title })).toBeInTheDocument()
    const rules = screen.getByRole('link', { name: /^Rules/ })
    expect(rules).toHaveAttribute('aria-current', 'page')
    expect(rules).toHaveTextContent('2 rules · 1 pending')
    expect(within(strip()).getByRole('link', { name: /^Filtering/ })).toHaveTextContent('Not scoredRelevant 8/10 (needs 6)')
    expect(within(strip()).getByRole('link', { name: /^Extraction/ })).toHaveTextContent('2 observables · 1 unverified')
    expect(screen.getByRole('article', { name: pendingRule.title })).toBeInTheDocument()
    expect(screen.getByText(/Run #7 · succeeded 21 Nov 2026 · detection v1/)).toBeInTheDocument()
  })

  it('opens on Filtering when nothing is pending, and says which stages have not run', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [] }) })

    renderReader(undefined)

    expect(await screen.findByRole('link', { name: /^Filtering/ })).toHaveAttribute('aria-current', 'page')
    expect(within(strip()).getByRole('link', { name: /^Extraction/ })).toHaveTextContent('Not run')
    expect(within(strip()).getByRole('link', { name: /^Rules/ })).toHaveTextContent('Not run')
    expect(screen.getByRole('complementary', { name: 'Filtering' })).toHaveTextContent('Not scored for the current profile yet.')
  })

  it('switches view through the strip, reports it for the URL and focuses the new view', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [finished] }) })
    const onViewChange = vi.fn()
    const user = userEvent.setup()
    renderReader('filtering', { onViewChange })

    await user.click(await screen.findByRole('link', { name: /^Extraction/ }))

    expect(onViewChange).toHaveBeenCalledWith('extraction')
    expect(screen.getByRole('heading', { name: 'Extraction', level: 2 })).toHaveFocus()
    expect(screen.getByRole('heading', { name: 'Scheduled tasks (1)' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^Extraction/ })).toHaveAttribute('href', '/articles/10?view=extraction')
  })

  it('reviews rules in place: focus moves to the next pending rule, then to the all-decided note', async () => {
    const api = page(
      {
        'GET /api/articles/10/runs': () => ({ status: 200, body: [finished] }),
        'PATCH /api/rules/rule-a': () => ({ status: 200, body: { ...pendingRule, status: 'approved' } }),
        'PATCH /api/rules/rule-b': () => ({ status: 200, body: { ...secondRule, status: 'rejected' } }),
      },
      [pendingRule, secondRule],
    )
    const onRulesChanged = vi.fn()
    const user = userEvent.setup()
    renderReader('rules', { onRulesChanged })

    const first = await screen.findByRole('article', { name: pendingRule.title })
    expect(within(first).queryByText(/^From/)).not.toBeInTheDocument()
    await user.click(within(first).getByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('heading', { name: secondRule.title })).toHaveFocus()

    await user.click(within(screen.getByRole('article', { name: secondRule.title })).getByRole('button', { name: 'Reject' }))

    const done = await screen.findByText('All 2 rules decided (1 approved, 1 rejected).')
    expect(done).toHaveFocus()
    expect(onRulesChanged).toHaveBeenCalledTimes(2)
    expect(within(strip()).getByRole('link', { name: /^Rules/ })).toHaveTextContent('2 rules · 0 pending')
    expect(api.calls.filter((call) => call.route.startsWith('PATCH'))).toHaveLength(2)
  })

  it('keeps rules from an earlier run that are still waiting, apart from the latest run', async () => {
    const latest = { ...finished, id: 8 }
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [latest] }) }, [{ ...secondRule, run_id: 8 }, pendingRule])

    renderReader('rules')

    expect(await screen.findByRole('heading', { name: 'Latest run (#8)' })).toBeInTheDocument()
    const earlier = screen.getByRole('heading', { name: 'Earlier run (#7)' }).parentElement!
    expect(within(earlier).getByRole('article', { name: pendingRule.title })).toBeInTheDocument()
  })

  it('loads the new rules when a run finishes', async () => {
    let polls = 0
    let ruleLoads = 0
    const working: PipelineRun = { ...base, status: 'running', current_step: 'rule_generation' }
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [working] }),
      'GET /api/runs/7': () => ({ status: 200, body: ++polls < 2 ? working : finished }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: null, queued: 0, running: 1 } }),
      [RULES]: () => ({ status: 200, body: ruleLoads++ === 0 ? [] : [pendingRule] }),
    })

    renderReader('rules')

    expect(await screen.findByText('Rules appear here when the run finishes.')).toBeInTheDocument()
    expect(await screen.findByRole('article', { name: pendingRule.title })).toBeInTheDocument()
  })
})

describe('ArticleReader text', () => {
  it('shows the full text and a link to the original', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [] }) })

    renderReader('filtering')

    expect(await screen.findByRole('heading', { name: 'Operation Paper Lantern', level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/schtasks \/create \/tn Updater/)).toHaveTextContent('First paragraph.\nschtasks /create /tn Updater', {
      normalizeWhitespace: false,
    })
    expect(screen.getByRole('link', { name: /Open original/ })).toHaveAttribute('href', detail.url)
  })

  const text = 'Intro.\nThe operators ran schtasks /create /tn Updater and wrote HKLM keys.'
  const scoredDetail: ArticleDetail = {
    ...detail,
    content_text: text,
    priority: {
      score: 47.5,
      band: 'Fair',
      terms: { perfect: ['schtasks', 'hklm'], lolbas: [], intelligence: [] },
      threshold: 37.5,
      profile_version: 2,
      stale: false,
      buckets: { perfect: { terms: ['hklm', 'schtasks'], count: 2, points: 56.25, max_points: 75 } },
      spans: [
        { start: text.indexOf('schtasks'), end: text.indexOf('schtasks') + 8, bucket: 'perfect', term: 'schtasks' },
        { start: text.indexOf('HKLM'), end: text.indexOf('HKLM') + 4, bucket: 'perfect', term: 'hklm' },
      ],
      spans_truncated: false,
    },
  }
  const evidenceStart = text.indexOf('ran schtasks')
  const withEvidence: PipelineRun = {
    ...finished,
    observables: [
      {
        id: 1,
        field_name: 'scheduled_tasks',
        value: 'schtasks /create /tn Updater',
        evidence: 'ran schtasks /create /tn Updater',
        evidence_verified: true,
        tags: {},
        evidence_span: { start: evidenceStart, end: evidenceStart + 'ran schtasks /create /tn Updater'.length },
      },
    ],
  }

  function marked(): string[] {
    return Array.from(document.querySelectorAll('.article-text-marked mark')).map((mark) => `${mark.className}|${mark.textContent}`)
  }

  it('highlights keywords on Filtering and evidence on Extraction, each toggled separately', async () => {
    page({
      [ARTICLE]: () => ({ status: 200, body: scoredDetail }),
      'GET /api/articles/10/runs': () => ({ status: 200, body: [withEvidence] }),
    })
    const user = userEvent.setup()
    renderReader('filtering')

    expect(await screen.findByRole('region', { name: 'Priority score' })).toHaveTextContent('47.5, Fair')
    expect(await screen.findByLabelText('Evidence (1)')).not.toBeChecked()
    expect(marked()).toEqual(['hl hl-perfect|schtasks', 'hl hl-perfect|HKLM'])

    await user.click(screen.getByRole('link', { name: /^Extraction/ }))
    expect(marked()).toEqual(['hl hl-evidence|ran schtasks /create /tn Updater'])

    await user.click(screen.getByLabelText('Keywords (2)'))
    expect(marked()).toEqual([
      'hl hl-evidence|ran ',
      'hl hl-evidence hl-perfect|schtasks',
      'hl hl-evidence| /create /tn Updater',
      'hl hl-perfect|HKLM',
    ])
  })

  it('marks evidence when a running run finishes, and clears it when detection runs again', async () => {
    const running: PipelineRun = { ...withEvidence, status: 'running', observables: [] }
    let polls = 0
    page({
      [ARTICLE]: () => ({ status: 200, body: scoredDetail }),
      'GET /api/articles/10/runs': () => ({ status: 200, body: [running] }),
      'GET /api/runs/7': () => ({ status: 200, body: ++polls < 2 ? running : withEvidence }),
      'POST /api/articles/10/runs': () => ({ status: 202, body: { ...base, id: 8 } }),
      'GET /api/runs/8': () => ({ status: 200, body: { ...base, id: 8 } }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: null, queued: 1, running: 0 } }),
    })
    const user = userEvent.setup()
    renderReader('extraction')
    expect(await screen.findByLabelText('Evidence (0)')).toBeInTheDocument()

    expect(await screen.findByLabelText('Evidence (1)')).toBeInTheDocument()
    expect(marked()).toContain('hl hl-evidence|ran schtasks /create /tn Updater')

    await user.click(screen.getByRole('button', { name: 'Run again' }))
    expect(await screen.findByLabelText('Evidence (0)')).toBeInTheDocument()
    expect(marked().some((mark) => mark.includes('hl-evidence'))).toBe(false)
  })

  it('reports a missing article', async () => {
    fakeApi({
      'GET /api/articles/99': () => ({ status: 404, body: { detail: { errors: ['Article 99 not found.'] } } }),
      'GET /api/articles/99/runs': () => ({ status: 200, body: [] }),
      'GET /api/rules?article_id=99': () => ({ status: 200, body: [] }),
    })

    render(<ArticleReader id={99} onBack={() => {}} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Article 99 not found.')
  })
})

describe('ArticleReader default view', () => {
  it('stays on Rules after the last pending rule is decided when the URL named no view', async () => {
    page(
      {
        'GET /api/articles/10/runs': () => ({ status: 200, body: [finished] }),
        'PATCH /api/rules/rule-a': () => ({ status: 200, body: { ...pendingRule, status: 'approved' } }),
      },
      [pendingRule],
    )
    const user = userEvent.setup()
    renderReader(undefined)

    await user.click(within(await screen.findByRole('article', { name: pendingRule.title })).getByRole('button', { name: 'Approve' }))

    expect(await screen.findByText('All 1 rule decided (1 approved, 0 rejected).')).toHaveFocus()
    expect(within(strip()).getByRole('link', { name: /^Rules/ })).toHaveAttribute('aria-current', 'page')
  })

  it('stays on Filtering when a run finishes with new pending rules', async () => {
    let polls = 0
    let ruleLoads = 0
    const working: PipelineRun = { ...base, status: 'running', current_step: 'rule_generation' }
    page({
      'GET /api/articles/10/runs': () => ({ status: 200, body: [working] }),
      'GET /api/runs/7': () => ({ status: 200, body: ++polls < 2 ? working : finished }),
      'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: null, queued: 0, running: 1 } }),
      [RULES]: () => ({ status: 200, body: ruleLoads++ === 0 ? [] : [pendingRule] }),
    })

    renderReader(undefined)

    expect(await screen.findByText('1 rule · 1 pending')).toBeInTheDocument()
    expect(within(strip()).getByRole('link', { name: /^Filtering/ })).toHaveAttribute('aria-current', 'page')
  })

  it('re-selecting the shown view only refocuses it', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [finished] }) })
    const onViewChange = vi.fn()
    const user = userEvent.setup()
    renderReader('extraction', { onViewChange })

    await user.click(await screen.findByRole('link', { name: /^Extraction/ }))

    expect(onViewChange).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: 'Extraction', level: 2 })).toHaveFocus()
  })

  it('shows earlier rules beneath an in-progress run after Run again', async () => {
    page(
      {
        'GET /api/articles/10/runs': () => ({ status: 200, body: [finished] }),
        'POST /api/articles/10/runs': () => ({ status: 202, body: { ...base, id: 8 } }),
        'GET /api/runs/8': () => ({ status: 200, body: { ...base, id: 8 } }),
        'GET /api/worker': () => ({ status: 200, body: { alive: true, last_seen_at: null, queued: 1, running: 0 } }),
      },
      [pendingRule],
    )
    const user = userEvent.setup()
    renderReader('rules')

    await user.click(await screen.findByRole('button', { name: 'Run again' }))

    expect(await screen.findByRole('heading', { name: 'Latest run (#8)' })).toBeInTheDocument()
    expect(screen.getByText('Rules appear here when the run finishes.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Earlier run (#7)' })).toBeInTheDocument()
  })
})

describe('ArticleReader jumps', () => {
  const text = 'Intro.\nThe operators ran schtasks /create /tn Updater and wrote HKLM keys.'
  const start = text.indexOf('ran schtasks')
  const quote = 'ran schtasks /create /tn Updater'
  const evidenceRun: PipelineRun = {
    ...finished,
    observables: [
      { id: 1, field_name: 'scheduled_tasks', value: 'schtasks', evidence: quote, evidence_verified: true, tags: {},
        evidence_span: { start, end: start + quote.length } },
      { id: 2, field_name: 'registry', value: 'HKLM', evidence: 'wrote HKLM', evidence_verified: false, tags: {} },
    ],
  }

  it('moves from an observable to its quote in the text, turning evidence highlights back on', async () => {
    page({
      [ARTICLE]: () => ({ status: 200, body: { ...detail, content_text: text } }),
      'GET /api/articles/10/runs': () => ({ status: 200, body: [evidenceRun] }),
    })
    const user = userEvent.setup()
    renderReader('extraction')
    await user.click(await screen.findByLabelText('Evidence (1)')) // off

    const verified = screen.getByText(quote, { selector: 'blockquote' }).closest('li')!
    await user.click(within(verified).getByRole('button', { name: 'Show in text' }))

    expect(screen.getByLabelText('Evidence (1)')).toBeChecked()
    expect(document.activeElement).toHaveTextContent(quote)
    expect(document.activeElement?.tagName).toBe('MARK')
    const unverified = screen.getByText('wrote HKLM', { selector: 'blockquote' }).closest('li')!
    expect(within(unverified).queryByRole('button', { name: 'Show in text' })).not.toBeInTheDocument()
  })

  it('opens Extraction on the observables a rule was built from, focusing the first one in list order', async () => {
    const onViewChange = vi.fn()
    const observable = (id: number, field: string, value: string) => ({
      id, field_name: field, value, evidence: `quote ${value}`, evidence_verified: true, tags: {},
    })
    const listedRun: PipelineRun = {
      ...finished,
      observables: [observable(1, 'cmdline', 'A'), observable(2, 'registry', 'B'), observable(3, 'cmdline', 'C')],
    }
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [listedRun] }) }, [{ ...pendingRule, observable_ids: [2, 3] }])
    const user = userEvent.setup()
    renderReader('rules', { onViewChange })

    await user.click(await screen.findByRole('button', { name: 'Built from 2 observables' }))

    expect(onViewChange).toHaveBeenCalledWith('extraction')
    const item = (value: string) => screen.getByText(`quote ${value}`, { selector: 'blockquote' }).closest('li')!
    expect(item('C')).toHaveFocus() // the list shows cmdline (A, C) before registry (B)
    expect(item('C')).toHaveClass('is-focused')
    expect(item('B')).toHaveClass('is-focused')
    expect(item('B')).toHaveTextContent('Used by the rule:')
    expect(item('A')).not.toHaveClass('is-focused')
  })

  it('offers sources only for rules of the latest run, whose observables are the ones listed', async () => {
    page({ 'GET /api/articles/10/runs': () => ({ status: 200, body: [{ ...finished, id: 8 }] }) }, [
      { ...secondRule, run_id: 8 },
      pendingRule,
    ])

    renderReader('rules')

    const latest = await screen.findByRole('article', { name: secondRule.title })
    expect(within(latest).getByRole('button', { name: 'Built from 1 observable' })).toBeInTheDocument()
    const earlier = screen.getByRole('article', { name: pendingRule.title })
    expect(within(earlier).queryByRole('button', { name: /Built from/ })).not.toBeInTheDocument()
  })
})
