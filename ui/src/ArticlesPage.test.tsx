import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { ArticlesPage } from './ArticlesPage'
import type { ArticleSummary, Source } from './api'
import { fakeApi } from './test/fakeApi'

const talosSource = { id: 1, slug: 'cisco_talos', name: 'Cisco Talos' }
const lantern: ArticleSummary = {
  id: 10,
  url: 'https://research.example.com/paper-lantern',
  title: 'Operation Paper Lantern',
  author: 'Research Team',
  published_at: '2026-11-17T09:00:00Z',
  fetched_at: '2026-11-21T10:00:00Z',
  word_count: 1840,
  excerpt: 'The operators executed rundll32.exe javascript:...',
  source: talosSource,
}
const heron: ArticleSummary = { ...lantern, id: 11, title: 'Quiet Heron loader', published_at: null, source: { id: 2, slug: 'other', name: 'Other Blog' } }
const scored: ArticleSummary = {
  ...lantern,
  id: 12,
  title: 'Ransomware via rundll32',
  priority: {
    score: 92.8,
    band: 'Excellent',
    terms: { perfect: ['rundll32.exe', 'hklm', 'powershell.exe', 'appdata'], lolbas: ['certutil.exe', 'cmd.exe'], intelligence: ['ransomware'] },
  },
  pipeline: { status: 'rules', run_id: 4, rules: 2 },
}
const sources = [
  { id: 1, name: 'Cisco Talos' },
  { id: 2, name: 'Other Blog' },
] as Source[]

/** The page with its URL state held the way App holds it: changes come back in as the new search string. */
function Queue({ initial = '', onOpen = () => {} }: { initial?: string; onOpen?: (id: number) => void }) {
  const [search, setSearch] = useState(initial)
  return (
    <>
      <output data-testid="search">{search}</output>
      <ArticlesPage search={search} onOpen={onOpen} onQueryChange={setSearch} />
    </>
  )
}

describe('ArticlesPage', () => {
  it('lists articles with their source, date and length', async () => {
    fakeApi({
      'GET /api/articles': () => ({ status: 200, body: [lantern, heron] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })

    render(<ArticlesPage onOpen={() => {}} />)

    const item = (await screen.findByRole('heading', { name: 'Operation Paper Lantern' })).closest('li')!
    expect(within(item).getByText('Cisco Talos')).toBeInTheDocument()
    expect(within(item).getByText(/1,840 words/)).toBeInTheDocument()
    expect(within(item).getByText('17 Nov 2026')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Quiet Heron loader' })).toBeInTheDocument()
  })

  it('filters by source', async () => {
    const api = fakeApi({
      'GET /api/articles': () => ({ status: 200, body: [lantern, heron] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
      'GET /api/articles?source_id=2': () => ({ status: 200, body: [heron] }),
    })
    const user = userEvent.setup()
    render(<Queue />)
    await screen.findByRole('heading', { name: 'Operation Paper Lantern' })

    await user.selectOptions(screen.getByLabelText('Source'), 'Other Blog')

    expect(await screen.findByRole('heading', { name: 'Quiet Heron loader' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Operation Paper Lantern' })).not.toBeInTheDocument()
    expect(api.calls.at(-1)?.route).toBe('GET /api/articles?source_id=2')
    expect(screen.getByTestId('search')).toHaveTextContent('?source_id=2')
  })

  it('shows each article\'s priority score, keyword chips and pipeline state', async () => {
    fakeApi({
      'GET /api/articles': () => ({ status: 200, body: [scored, heron] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })

    render(<ArticlesPage onOpen={() => {}} />)

    const item = (await screen.findByRole('heading', { name: 'Ransomware via rundll32' })).closest('li')!
    expect(item).toHaveTextContent('Priority 92.8, Excellent')
    const chips = within(item).getByRole('list', { name: 'Matched keywords' })
    expect(within(chips).getAllByRole('listitem').map((chip) => chip.textContent)).toEqual([
      'perfect: rundll32.exe', 'perfect: hklm', 'perfect: powershell.exe', 'perfect: appdata',
      'lolbas: certutil.exe', 'lolbas: cmd.exe', '+1 more',
    ])
    expect(within(item).getByText('Has rules')).toBeInTheDocument()
    expect(within(item).getByText('2 rules')).toBeInTheDocument()
    const unscored = screen.getByRole('heading', { name: 'Quiet Heron loader' }).closest('li')!
    expect(unscored).toHaveTextContent('Not scored')
  })

  it('sorts by priority and filters by band and pipeline state through the URL', async () => {
    const api = fakeApi({
      'GET /api/articles': () => ({ status: 200, body: [lantern] }),
      'GET /api/articles?sort=priority': () => ({ status: 200, body: [scored] }),
      'GET /api/articles?sort=priority&band=Excellent': () => ({ status: 200, body: [scored] }),
      'GET /api/articles?sort=priority&band=Excellent&status=not_run': () => ({ status: 200, body: [] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })
    const user = userEvent.setup()
    render(<Queue />)
    await screen.findByRole('heading', { name: 'Operation Paper Lantern' })

    await user.selectOptions(screen.getByLabelText('Sort by'), 'Priority score')
    expect(await screen.findByRole('heading', { name: 'Ransomware via rundll32' })).toBeInTheDocument()
    expect(screen.getByText(/highest priority first/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Priority band'), 'Excellent')
    await user.selectOptions(screen.getByLabelText('Pipeline'), 'Not run')

    expect(await screen.findByText('No articles match this view.')).toBeInTheDocument()
    expect(api.calls.at(-1)?.route).toBe('GET /api/articles?sort=priority&band=Excellent&status=not_run')
    expect(screen.getByTestId('search')).toHaveTextContent('?sort=priority&band=Excellent&status=not_run')

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByRole('heading', { name: 'Ransomware via rundll32' })).toBeInTheDocument()
    expect(screen.getByTestId('search')).toHaveTextContent('?sort=priority')
  })

  it('opens on the view named in the URL', async () => {
    const api = fakeApi({
      'GET /api/articles?sort=priority': () => ({ status: 200, body: [scored] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })

    render(<Queue initial="?sort=priority" />)

    expect(await screen.findByRole('heading', { name: 'Ransomware via rundll32' })).toBeInTheDocument()
    expect(screen.getByLabelText('Sort by')).toHaveValue('priority')
    expect(api.calls.map((call) => call.route)).toContain('GET /api/articles?sort=priority')
  })

  it('pages through a long list', async () => {
    const page = Array.from({ length: 50 }, (_, n) => ({ ...lantern, id: 100 + n, title: `Article ${n}` }))
    fakeApi({
      'GET /api/articles': () => ({ status: 200, body: page }),
      'GET /api/articles?offset=50': () => ({ status: 200, body: [heron] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })
    const user = userEvent.setup()
    render(<Queue />)
    await screen.findByRole('heading', { name: 'Article 0' })

    screen.getByRole('button', { name: /Next/ }).focus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('heading', { name: 'Quiet Heron loader' })).toBeInTheDocument()
    expect(screen.getByText('Page 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Next/ })).toBeDisabled()
    expect(screen.getByTestId('search')).toHaveTextContent('?page=1')
    // Focus moves to the new results rather than being lost with the pager, and the count is announced.
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Results, page 2' }))
    expect(screen.getByText('1 article on page 2.')).toHaveAttribute('role', 'status')

    await user.click(screen.getByRole('button', { name: /Previous/ }))
    expect(await screen.findByRole('heading', { name: 'Article 0' })).toBeInTheDocument()
    expect(screen.getByTestId('search')).toBeEmptyDOMElement()
  })

  it('starts a changed filter from the first page and clears a sort direction when the sort changes', async () => {
    fakeApi({
      'GET /api/articles?sort=priority&order=asc&offset=100': () => ({ status: 200, body: [lantern] }),
      'GET /api/articles?sort=priority&order=asc&band=Good': () => ({ status: 200, body: [lantern] }),
      'GET /api/articles?sort=source&band=Good': () => ({ status: 200, body: [heron] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })
    const user = userEvent.setup()
    render(<Queue initial="?sort=priority&order=asc&page=2" />)
    expect(await screen.findByText(/lowest priority first/)).toBeInTheDocument()
    await screen.findByRole('heading', { name: 'Operation Paper Lantern' })

    await user.selectOptions(screen.getByLabelText('Priority band'), 'Good')
    expect(screen.getByTestId('search')).toHaveTextContent('?sort=priority&order=asc&band=Good')
    await screen.findByRole('heading', { name: 'Operation Paper Lantern' })

    await user.selectOptions(screen.getByLabelText('Sort by'), 'Source')
    expect(await screen.findByRole('heading', { name: 'Quiet Heron loader' })).toBeInTheDocument()
    expect(screen.getByTestId('search')).toHaveTextContent('?sort=source&band=Good')
  })

  it('shows the newest view even when an older request answers last', async () => {
    let releaseSlow: (value: unknown) => void = () => {}
    const slow = new Promise((resolve) => (releaseSlow = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === '/api/sources') return Response.json(sources)
        if (url === '/api/articles') {
          await slow
          return Response.json([lantern])
        }
        if (url === '/api/articles?sort=priority') return Response.json([scored])
        throw new Error(`Unexpected request: ${url}`)
      }),
    )
    const user = userEvent.setup()
    render(<Queue />)
    expect(screen.getByText('Loading articles…')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Sort by'), 'Priority score')
    expect(await screen.findByRole('heading', { name: 'Ransomware via rundll32' })).toBeInTheDocument()
    releaseSlow(undefined)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(screen.queryByRole('heading', { name: 'Operation Paper Lantern' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Ransomware via rundll32' })).toBeInTheDocument()
  })

  it('names a filtered source that is not in the source list', async () => {
    fakeApi({
      'GET /api/articles?source_id=42': () => ({ status: 200, body: [] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })

    render(<Queue initial="?source_id=42" />)

    expect(await screen.findByText('No articles match this view.')).toBeInTheDocument()
    expect(screen.getByLabelText('Source')).toHaveDisplayValue('Source #42')
  })

  it('opens an article', async () => {
    fakeApi({
      'GET /api/articles': () => ({ status: 200, body: [lantern] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })
    const opened: number[] = []
    const user = userEvent.setup()
    render(<ArticlesPage onOpen={(id) => opened.push(id)} />)

    await user.click(await screen.findByRole('link', { name: 'Operation Paper Lantern' }))

    expect(opened).toEqual([10])
  })

  it('explains an empty list', async () => {
    fakeApi({
      'GET /api/articles': () => ({ status: 200, body: [] }),
      'GET /api/sources': () => ({ status: 200, body: sources }),
    })

    render(<ArticlesPage onOpen={() => {}} />)

    expect(await screen.findByText(/No articles yet/)).toBeInTheDocument()
  })
})
