import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { ArticlesPage } from './ArticlesPage'
import type { ArticleDetail, ArticleSummary, Source } from './api'
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
const sources = [
  { id: 1, name: 'Cisco Talos' },
  { id: 2, name: 'Other Blog' },
] as Source[]

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
    render(<ArticlesPage onOpen={() => {}} />)
    await screen.findByRole('heading', { name: 'Operation Paper Lantern' })

    await user.selectOptions(screen.getByLabelText('Source'), 'Other Blog')

    expect(await screen.findByRole('heading', { name: 'Quiet Heron loader' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Operation Paper Lantern' })).not.toBeInTheDocument()
    expect(api.calls.at(-1)?.route).toBe('GET /api/articles?source_id=2')
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

describe('ArticleReader', async () => {
  const { ArticleReader } = await import('./ArticlesPage')
  const detail: ArticleDetail = { ...lantern, content_text: 'First paragraph.\nschtasks /create /tn Updater' }

  it('shows the full text and a link to the original', async () => {
    fakeApi({ 'GET /api/articles/10': () => ({ status: 200, body: detail }) })

    render(<ArticleReader id={10} onBack={() => {}} />)

    expect(await screen.findByRole('heading', { name: 'Operation Paper Lantern', level: 1 })).toBeInTheDocument()
    expect(screen.getByText('schtasks /create /tn Updater')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open original/ })).toHaveAttribute('href', lantern.url)
  })

  it('reports a missing article', async () => {
    fakeApi({ 'GET /api/articles/99': () => ({ status: 404, body: { detail: { errors: ['Article 99 not found.'] } } }) })

    render(<ArticleReader id={99} onBack={() => {}} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Article 99 not found.')
  })
})
