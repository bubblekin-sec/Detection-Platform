import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { ReviewPage } from './ReviewPage'
import type { RuleDetail } from './api'
import { fakeApi } from './test/fakeApi'

const pending: RuleDetail = {
  id: '6f1d0b8e-3c1a-4b8e-9a6e-1f2a3b4c5d6e',
  title: 'Scheduled Task Created Pointing to Public Folder',
  level: 'high',
  status: 'pending_review',
  group: { platform: 'windows', telemetry_category: 'process_creation' },
  observable_ids: [1],
  sigma_yaml: 'title: Scheduled Task Created Pointing to Public Folder\nlevel: high\n',
  sigma_hash: 'a'.repeat(64),
  rationale: 'Covers the observed task creation.',
  queries: [
    {
      id: 1,
      target: 'defender_xdr',
      origin: 'converted',
      query_text: 'DeviceProcessEvents',
      status: 'ok',
      error: null,
      warnings: [],
      stale: false,
    },
  ],
  run_id: 7,
  reviewer_note: null,
  reviewed_at: null,
  created_at: '2026-11-21T12:00:00Z',
  article: { id: 10, title: 'Operation Paper Lantern', url: 'https://r.example.com/lantern' },
}
const PATCH = `PATCH /api/rules/${pending.id}`

function card() {
  return screen.getByRole('article', { name: pending.title })
}

describe('ReviewPage', () => {
  it('lists pending rules with the article they came from', async () => {
    fakeApi({ 'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }) })

    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    await screen.findByRole('article', { name: pending.title })
    expect(screen.getByRole('link', { name: 'Operation Paper Lantern' })).toHaveAttribute('href', '/articles/10')
  })

  it('offers the approved rules as a YAML download only where there are approved rules', async () => {
    fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      'GET /api/rules?status=approved': () => ({ status: 200, body: [{ ...pending, status: 'approved' }] }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    await screen.findByRole('article', { name: pending.title })
    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Approved' }))
    const download = await screen.findByRole('link', { name: 'Download approved rules (YAML)' })
    expect(download).toHaveAttribute('href', '/api/rules/export')
    expect(download).toHaveAttribute('download')
  })

  it('has no download when nothing is approved', async () => {
    fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [] }),
      'GET /api/rules?status=approved': () => ({ status: 200, body: [] }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    await user.click(await screen.findByRole('button', { name: 'Approved' }))
    await screen.findByText('No approved rules yet.')
    expect(screen.queryByRole('link', { name: /download/i })).not.toBeInTheDocument()
  })

  it('approves a rule and takes it off the pending list', async () => {
    const api = fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      [PATCH]: () => ({ status: 200, body: { ...pending, status: 'approved', reviewed_at: '2026-11-21T13:00:00Z' } }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    await user.click(within(await screen.findByRole('article', { name: pending.title })).getByRole('button', { name: 'Approve' }))

    expect(await screen.findByRole('status')).toHaveTextContent(`Approved “${pending.title}”`)
    expect(screen.queryByRole('article', { name: pending.title })).not.toBeInTheDocument()
    expect(api.calls.at(-1)).toEqual({ route: PATCH, body: { status: 'approved', expected_sigma_hash: 'a'.repeat(64) } })
  })

  it('rejects a rule with a note', async () => {
    const api = fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      [PATCH]: (body) => ({ status: 200, body: { ...pending, ...(body as object) } }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)
    await screen.findByRole('article', { name: pending.title })

    await user.type(within(card()).getByLabelText('Reviewer note'), 'Too broad: matches admin tooling.')
    await user.click(within(card()).getByRole('button', { name: 'Reject' }))

    expect(api.calls.at(-1)?.body).toEqual({
      status: 'rejected',
      reviewer_note: 'Too broad: matches admin tooling.',
      expected_sigma_hash: 'a'.repeat(64),
    })
  })

  it('shows Sigma validation errors from an edit and keeps the editor open', async () => {
    fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      [PATCH]: () => ({ status: 422, body: { detail: { errors: ["sigma_yaml: Detection 'nope' not defined in detections"] } } }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)
    await screen.findByRole('article', { name: pending.title })

    await user.click(within(card()).getByRole('button', { name: 'Edit Sigma' }))
    const editor = within(card()).getByLabelText('Sigma YAML')
    await user.clear(editor)
    await user.type(editor, 'title: x')
    await user.click(within(card()).getByRole('button', { name: 'Save Sigma' }))

    expect(await within(card()).findByRole('alert')).toHaveTextContent("Detection 'nope' not defined in detections")
    expect(within(card()).getByLabelText('Sigma YAML')).toHaveValue('title: x')
  })

  it('saves a valid Sigma edit and shows the regenerated rule', async () => {
    const edited = { ...pending, sigma_yaml: 'title: Edited\nlevel: medium\n', level: 'medium' }
    const api = fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      [PATCH]: () => ({ status: 200, body: edited }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)
    await screen.findByRole('article', { name: pending.title })

    await user.click(within(card()).getByRole('button', { name: 'Edit Sigma' }))
    const editor = within(card()).getByLabelText('Sigma YAML')
    await user.clear(editor)
    await user.type(editor, 'title: Edited')
    await user.click(within(card()).getByRole('button', { name: 'Save Sigma' }))

    expect(await within(card()).findByText('level: medium')).toBeInTheDocument()
    expect(within(card()).queryByLabelText('Sigma YAML')).not.toBeInTheDocument()
    expect(api.calls.at(-1)?.body).toEqual({ sigma_yaml: 'title: Edited' })
  })

  it('saves a hand-written KQL query', async () => {
    const api = fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      [PATCH]: () => ({ status: 200, body: pending }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)
    await screen.findByRole('article', { name: pending.title })

    await user.click(within(card()).getByRole('button', { name: 'Edit KQL' }))
    await user.type(within(card()).getByLabelText('KQL'), 'DeviceEvents')
    await user.click(within(card()).getByRole('button', { name: 'Save KQL' }))

    expect(api.calls.at(-1)?.body).toEqual({ kql: 'DeviceEvents' })
  })

  it('switches between pending, approved and rejected', async () => {
    const api = fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [] }),
      'GET /api/rules?status=approved': () => ({ status: 200, body: [{ ...pending, status: 'approved' }] }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    expect(await screen.findByText(/Nothing waiting for review/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approved' }))

    expect(await screen.findByRole('article', { name: pending.title })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approved' })).toHaveAttribute('aria-pressed', 'true')
    expect(api.calls.at(-1)?.route).toBe('GET /api/rules?status=approved')
  })

  it('sends the note with Back to pending', async () => {
    const api = fakeApi({
      'GET /api/rules?status=approved': () => ({ status: 200, body: [{ ...pending, status: 'approved' }] }),
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [] }),
      [PATCH]: (body) => ({ status: 200, body: { ...pending, ...(body as object) } }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)
    await screen.findByText(/Nothing waiting for review/)
    await user.click(screen.getByRole('button', { name: 'Approved' }))
    await screen.findByRole('article', { name: pending.title })

    await user.type(within(card()).getByLabelText('Reviewer note'), 'Needs a narrower path.')
    await user.click(within(card()).getByRole('button', { name: 'Back to pending' }))

    expect(api.calls.at(-1)?.body).toEqual({
      status: 'pending_review',
      reviewer_note: 'Needs a narrower path.',
      expected_sigma_hash: 'a'.repeat(64),
    })
  })

  it('shows why an approval failed', async () => {
    fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      [PATCH]: () => ({ status: 409, body: { detail: { errors: ['The rule changed since you loaded it. Reload and review it again.'] } } }),
    })
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    await user.click(within(await screen.findByRole('article', { name: pending.title })).getByRole('button', { name: 'Approve' }))

    expect(await within(card()).findByRole('alert')).toHaveTextContent('The rule changed since you loaded it')
  })

  it('ignores a response that arrives after switching tabs', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    fakeApi({
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
      'GET /api/rules?status=approved': () => ({ status: 200, body: [{ ...pending, status: 'approved' }] }),
      [PATCH]: () => ({ status: 200, body: { ...pending, status: 'approved' } }),
    })
    const original = globalThis.fetch
    globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
      if (args[1]?.method === 'PATCH') await gate
      return original(...args)
    }) as typeof fetch
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)

    await user.click(within(await screen.findByRole('article', { name: pending.title })).getByRole('button', { name: 'Approve' }))
    await user.click(screen.getByRole('button', { name: 'Approved' }))
    await screen.findByRole('article', { name: pending.title })
    release()
    await gate
    await new Promise((resolve) => setTimeout(resolve, 20))

    expect(screen.getByRole('article', { name: pending.title })).toBeInTheDocument()
  })

  it('opens the source article in the app', async () => {
    fakeApi({ 'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }) })
    const opened: number[] = []
    const user = userEvent.setup()
    render(<ReviewPage search="?view=rules" onOpenArticle={(id) => opened.push(id)} />)

    await user.click(await screen.findByRole('link', { name: 'Operation Paper Lantern' }))

    expect(opened).toEqual([10])
  })
})

const queueRow = {
  article: { id: 10, title: 'Operation Paper Lantern', url: 'https://r.example.com/lantern', source: { id: 1, name: 'Cisco Talos' } },
  priority: { score: 92.8, band: 'Excellent' },
  relevance: { score: 8, min_score: 6 },
  run_id: 7,
  rules: { pending_review: 2, approved: 1, rejected: 0 },
  duplicates: 1,
  last_decided_at: null,
}

describe('ReviewPage by article', () => {
  it('lists articles with pending rules and the context to pick one', async () => {
    fakeApi({ 'GET /api/rules/by-article?status=pending_review': () => ({ status: 200, body: [queueRow] }) })

    render(<ReviewPage onOpenArticle={() => {}} />)

    const row = (await screen.findByRole('link', { name: 'Operation Paper Lantern' })).closest('li')!
    expect(row).toHaveTextContent('Priority 92.8, Excellent')
    expect(row).toHaveTextContent('Cisco Talos · run #7')
    expect(row).toHaveTextContent('Relevance 8/10')
    expect(row).toHaveTextContent('2 pending · 1 approved · 0 rejected')
    expect(row).toHaveTextContent('1 likely duplicate')
    expect(screen.getByRole('button', { name: 'By article' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('opens an article on its rules', async () => {
    fakeApi({ 'GET /api/rules/by-article?status=pending_review': () => ({ status: 200, body: [queueRow] }) })
    const opened: [number, string | undefined][] = []
    const user = userEvent.setup()
    render(<ReviewPage onOpenArticle={(id, view) => opened.push([id, view])} />)

    const link = await screen.findByRole('link', { name: 'Operation Paper Lantern' })
    expect(link).toHaveAttribute('href', '/articles/10?view=rules')
    await user.click(link)

    expect(opened).toEqual([[10, 'rules']])
  })

  it('keeps the view and status in the URL', async () => {
    fakeApi({
      'GET /api/rules/by-article?status=pending_review': () => ({ status: 200, body: [] }),
      'GET /api/rules/by-article?status=approved': () => ({ status: 200, body: [{ ...queueRow, last_decided_at: '2026-11-21T12:00:00Z' }] }),
      'GET /api/rules?status=approved': () => ({ status: 200, body: [{ ...pending, status: 'approved' }] }),
    })
    const searches: string[] = []
    const user = userEvent.setup()
    render(<ReviewPage onOpenArticle={() => {}} onQueryChange={(search) => searches.push(search)} />)

    expect(await screen.findByText(/No articles waiting for review/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approved' }))
    expect(await screen.findByRole('link', { name: /Download approved rules/ })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Articles with approved rules' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'By rule' }))

    expect(await screen.findByRole('article', { name: pending.title })).toBeInTheDocument()
    expect(searches).toEqual(['?status=approved', '?view=rules&status=approved'])
  })

  it('follows a URL change such as browser back', async () => {
    fakeApi({
      'GET /api/rules/by-article?status=pending_review': () => ({ status: 200, body: [queueRow] }),
      'GET /api/rules?status=pending_review': () => ({ status: 200, body: [pending] }),
    })
    const { rerender } = render(<ReviewPage search="?view=rules" onOpenArticle={() => {}} />)
    expect(await screen.findByRole('article', { name: pending.title })).toBeInTheDocument()

    rerender(<ReviewPage search="" onOpenArticle={() => {}} />)

    expect(await screen.findByRole('link', { name: 'Operation Paper Lantern' })).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: pending.title })).not.toBeInTheDocument()
  })
})
