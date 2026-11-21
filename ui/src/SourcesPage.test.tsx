import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { SourcesPage } from './SourcesPage'
import type { Source } from './api'
import { fakeApi } from './test/fakeApi'

const talos: Source = {
  id: 1,
  slug: 'cisco_talos',
  name: 'Cisco Talos',
  url: 'https://blog.talosintelligence.com/',
  rss_url: 'https://blog.talosintelligence.com/rss/',
  active: true,
  check_frequency_seconds: 1800,
  description: null,
  origin: 'huntable_seed',
  config: {},
  last_checked_at: null,
  last_error: null,
}
const splunk: Source = { ...talos, id: 2, slug: 'splunk', name: 'Splunk Security Blog', rss_url: null, active: false }

type User = ReturnType<typeof userEvent.setup>

function rowFor(name: string) {
  return screen.getByRole('row', { name: new RegExp(name) })
}

async function submitAddForm(user: User, values: { name: string; url: string; rss: string }) {
  const form = screen.getByRole('form', { name: 'Add RSS source' })
  await user.type(within(form).getByLabelText('Name'), values.name)
  await user.type(within(form).getByLabelText('Site URL'), values.url)
  await user.type(within(form).getByLabelText('RSS feed URL'), values.rss)
  await user.click(within(form).getByRole('button', { name: 'Add source' }))
  return form
}

describe('SourcesPage', () => {
  it('lists sources with their origin, feed and active state', async () => {
    fakeApi({ 'GET /api/sources': () => ({ status: 200, body: [talos, splunk] }) })

    render(<SourcesPage />)

    const talosRow = await screen.findByRole('row', { name: /Cisco Talos/ })
    expect(within(talosRow).getByText('Huntable seed')).toBeInTheDocument()
    expect(within(talosRow).getByRole('checkbox', { name: 'Active: Cisco Talos' })).toBeChecked()
    const splunkRow = rowFor('Splunk Security Blog')
    expect(within(splunkRow).getByRole('checkbox', { name: 'Active: Splunk Security Blog' })).not.toBeChecked()
    expect(within(splunkRow).getByText('No RSS feed')).toBeInTheDocument()
  })

  it('shows an error when sources cannot be loaded', async () => {
    fakeApi({ 'GET /api/sources': () => ({ status: 500, body: {} }) })

    render(<SourcesPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load sources')
  })

  it('adds an RSS source and shows it in the list', async () => {
    const created: Source = { ...talos, id: 3, slug: 'example_blog', name: 'Example Blog', origin: 'user' }
    const api = fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'POST /api/sources': () => ({ status: 201, body: created }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)
    await screen.findByRole('row', { name: /Cisco Talos/ })

    const form = await submitAddForm(user, { name: 'Example Blog', url: 'https://example.com', rss: 'https://example.com/feed' })

    const row = await screen.findByRole('row', { name: /Example Blog/ })
    expect(within(row).getByText('Added by you')).toBeInTheDocument()
    expect(api.calls.at(-1)).toEqual({
      route: 'POST /api/sources',
      body: { name: 'Example Blog', url: 'https://example.com', rss_url: 'https://example.com/feed' },
    })
    expect(within(form).getByLabelText('Name')).toHaveValue('')
  })

  it('shows the API validation error on the field it belongs to', async () => {
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [] }),
      'POST /api/sources': () => ({
        status: 422,
        body: { detail: { errors: ["rss_url: URL scheme should be 'http' or 'https'"] } },
      }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)
    await screen.findByText(/No sources yet/)

    const form = await submitAddForm(user, { name: 'Bad', url: 'https://ok.example.com', rss: 'ftp://example.com/feed' })

    const rss = within(form).getByLabelText('RSS feed URL')
    expect(await within(form).findByText("URL scheme should be 'http' or 'https'")).toBeInTheDocument()
    expect(rss).toHaveAccessibleDescription("URL scheme should be 'http' or 'https'")
    expect(rss).toHaveAttribute('aria-invalid', 'true')
    expect(rss).toHaveValue('ftp://example.com/feed')
    expect(within(form).getByLabelText('Name')).not.toHaveAttribute('aria-invalid', 'true')
  })

  it('shows a duplicate-name conflict on the name field', async () => {
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'POST /api/sources': () => ({
        status: 409,
        body: { detail: { errors: ["A source with slug 'cisco_talos' already exists."] } },
      }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)
    await screen.findByRole('row', { name: /Cisco Talos/ })

    const form = await submitAddForm(user, { name: 'Cisco Talos', url: 'https://x.example.com', rss: 'https://x.example.com/rss' })

    await within(form).findByText(/already exists/)
    expect(within(form).getByLabelText('Name')).toHaveAccessibleDescription(
      "A source with slug 'cisco_talos' already exists.",
    )
  })

  it('toggles whether a source is active', async () => {
    const api = fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'PATCH /api/sources/1': (body) => ({ status: 200, body: { ...talos, ...(body as object) } }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('checkbox', { name: 'Active: Cisco Talos' }))

    expect(api.calls.at(-1)).toEqual({ route: 'PATCH /api/sources/1', body: { active: false } })
    expect(await screen.findByRole('checkbox', { name: 'Active: Cisco Talos' })).not.toBeChecked()
  })

  it('keeps the previous state and reports the reason when a toggle is rejected', async () => {
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [splunk] }),
      'PATCH /api/sources/2': () => ({
        status: 422,
        body: { detail: { errors: ['rss_url: an active source needs an RSS feed URL'] } },
      }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('checkbox', { name: 'Active: Splunk Security Blog' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not update Splunk Security Blog: an active source needs an RSS feed URL',
    )
    expect(screen.getByRole('checkbox', { name: 'Active: Splunk Security Blog' })).not.toBeChecked()
  })

  it('edits a source in place', async () => {
    const api = fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'PATCH /api/sources/1': (body) => ({ status: 200, body: { ...talos, ...(body as object) } }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('button', { name: 'Edit Cisco Talos' }))
    const form = screen.getByRole('form', { name: 'Edit Cisco Talos' })
    const name = within(form).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Talos Intelligence')
    await user.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('row', { name: /Talos Intelligence/ })).toBeInTheDocument()
    expect(api.calls.at(-1)).toEqual({
      route: 'PATCH /api/sources/1',
      body: { name: 'Talos Intelligence', url: talos.url, rss_url: talos.rss_url },
    })
    expect(screen.queryByRole('form', { name: /^Edit/ })).not.toBeInTheDocument()
  })

  it('links each name to the source site', async () => {
    fakeApi({ 'GET /api/sources': () => ({ status: 200, body: [talos] }) })

    render(<SourcesPage />)

    expect(await screen.findByRole('link', { name: 'Cisco Talos' })).toHaveAttribute('href', talos.url)
  })

  it('keeps the list sorted by name after adding', async () => {
    const created: Source = { ...talos, id: 3, slug: 'aaa_research', name: 'Aaa Research', origin: 'user' }
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'POST /api/sources': () => ({ status: 201, body: created }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)
    await screen.findByRole('row', { name: /Cisco Talos/ })

    await submitAddForm(user, { name: 'Aaa Research', url: 'https://a.example.com', rss: 'https://a.example.com/feed' })
    await screen.findByRole('row', { name: /Aaa Research/ })

    const names = screen.getAllByRole('rowheader').map((cell) => cell.textContent)
    expect(names).toEqual(['Aaa Research', 'Cisco Talos'])
  })

  it('reports a network failure on the form instead of failing silently', async () => {
    fakeApi({ 'GET /api/sources': () => ({ status: 200, body: [] }) }) // no POST route: fetch rejects
    const user = userEvent.setup()
    render(<SourcesPage />)
    await screen.findByText(/No sources yet/)

    const form = await submitAddForm(user, { name: 'Offline', url: 'https://o.example.com', rss: 'https://o.example.com/f' })

    expect(await within(form).findByRole('alert')).toHaveTextContent('Could not save: could not reach the server')
    expect(within(form).getByRole('button', { name: 'Add source' })).toBeEnabled()
  })

  it('moves focus to the field with the error', async () => {
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [] }),
      'POST /api/sources': () => ({ status: 422, body: { detail: { errors: ['url: Input should be a valid URL'] } } }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)
    await screen.findByText(/No sources yet/)

    const form = await submitAddForm(user, { name: 'X', url: 'nope', rss: 'https://x.example.com/f' })

    await within(form).findByText('Input should be a valid URL')
    expect(within(form).getByLabelText('Site URL')).toHaveFocus()
  })

  it('sends a null feed when the RSS URL is cleared in an edit', async () => {
    const api = fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [{ ...talos, active: false }] }),
      'PATCH /api/sources/1': (body) => ({ status: 200, body: { ...talos, active: false, ...(body as object) } }),
    })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('button', { name: 'Edit Cisco Talos' }))
    const form = screen.getByRole('form', { name: 'Edit Cisco Talos' })
    await user.clear(within(form).getByLabelText('RSS feed URL'))
    await user.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await within(rowFor('Cisco Talos')).findByText('No RSS feed')).toBeInTheDocument()
    expect(api.calls.at(-1)?.body).toMatchObject({ rss_url: null })
  })

  it('disables a toggle while its update is in flight', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    const fetchMock = fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'PATCH /api/sources/1': () => ({ status: 200, body: { ...talos, active: false } }),
    })
    const original = globalThis.fetch
    globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
      if (args[1]?.method === 'PATCH') await gate
      return original(...args)
    }) as typeof fetch
    const user = userEvent.setup()
    render(<SourcesPage />)

    const toggle = await screen.findByRole('checkbox', { name: 'Active: Cisco Talos' })
    await user.click(toggle)

    expect(toggle).toBeDisabled()
    release()
    expect(await screen.findByRole('checkbox', { name: 'Active: Cisco Talos' })).toBeEnabled()
    expect(fetchMock.calls.filter((call) => call.route.startsWith('PATCH'))).toHaveLength(1)
  })

  it('marks the open edit button as expanded', async () => {
    fakeApi({ 'GET /api/sources': () => ({ status: 200, body: [talos] }) })
    const user = userEvent.setup()
    render(<SourcesPage />)

    const edit = await screen.findByRole('button', { name: 'Edit Cisco Talos' })
    expect(edit).toHaveAttribute('aria-expanded', 'false')
    await user.click(edit)
    expect(edit).toHaveAttribute('aria-expanded', 'true')
  })

  it('fetches a source now and reports what was stored', async () => {
    let fetched = false
    const api = fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [{ ...talos, last_checked_at: fetched ? '2026-11-21T12:00:00Z' : null }] }),
      'POST /api/sources/1/fetch': () => {
        fetched = true
        return { status: 200, body: { new: 3, duplicates: 12, too_short: 1, filtered: 0 } }
      },
    })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('button', { name: 'Fetch Cisco Talos now' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Cisco Talos: 3 new, 12 already stored, 1 too short, 0 filtered',
    )
    expect(api.calls.map((call) => call.route)).toEqual(['GET /api/sources', 'POST /api/sources/1/fetch', 'GET /api/sources'])
  })

  it('keeps a source added while the post-fetch refresh is in flight', async () => {
    let releaseRefresh: () => void = () => {}
    const refreshGate = new Promise<void>((resolve) => (releaseRefresh = resolve))
    let getCount = 0
    const created: Source = { ...talos, id: 3, slug: 'new_blog', name: 'New Blog', origin: 'user' }
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [talos] }),
      'POST /api/sources/1/fetch': () => ({ status: 200, body: { new: 0, duplicates: 0, too_short: 0, filtered: 0 } }),
      'POST /api/sources': () => ({ status: 201, body: created }),
    })
    const original = globalThis.fetch
    globalThis.fetch = (async (...args: Parameters<typeof fetch>) => {
      if (String(args[0]) === '/api/sources' && (args[1]?.method ?? 'GET') === 'GET' && ++getCount === 2) await refreshGate
      return original(...args)
    }) as typeof fetch
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('button', { name: 'Fetch Cisco Talos now' }))
    await screen.findByRole('status')
    await submitAddForm(user, { name: 'New Blog', url: 'https://n.example.com', rss: 'https://n.example.com/f' })
    await screen.findByRole('row', { name: /New Blog/ })
    releaseRefresh()
    await refreshGate

    expect(await screen.findByRole('row', { name: /New Blog/ })).toBeInTheDocument()
  })

  it('reports a failed fetch and shows the source error', async () => {
    let failed = false
    fakeApi({
      'GET /api/sources': () => ({ status: 200, body: [{ ...talos, last_error: failed ? 'Feed returned HTTP 404.' : null }] }),
      'POST /api/sources/1/fetch': () => {
        failed = true
        return { status: 502, body: { detail: { errors: ['Feed returned HTTP 404.'] } } }
      },
    })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('button', { name: 'Fetch Cisco Talos now' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Fetch failed for Cisco Talos: Feed returned HTTP 404.')
    expect(await within(rowFor('Cisco Talos')).findByText(/Feed returned HTTP 404/)).toBeInTheDocument()
  })

  it('offers no fetch for a source without a feed', async () => {
    fakeApi({ 'GET /api/sources': () => ({ status: 200, body: [splunk] }) })

    render(<SourcesPage />)

    await screen.findByRole('row', { name: /Splunk Security Blog/ })
    expect(screen.queryByRole('button', { name: /Fetch Splunk/ })).not.toBeInTheDocument()
  })

  it('cancels an edit without saving', async () => {
    const api = fakeApi({ 'GET /api/sources': () => ({ status: 200, body: [talos] }) })
    const user = userEvent.setup()
    render(<SourcesPage />)

    await user.click(await screen.findByRole('button', { name: 'Edit Cisco Talos' }))
    await user.type(within(screen.getByRole('form', { name: 'Edit Cisco Talos' })).getByLabelText('Name'), ' changed')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('form', { name: /^Edit/ })).not.toBeInTheDocument()
    expect(rowFor('Cisco Talos')).toBeInTheDocument()
    expect(api.calls.map((call) => call.route)).toEqual(['GET /api/sources'])
  })
})
