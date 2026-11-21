import { Fragment, useEffect, useState } from 'react'

import {
  createSource,
  describeError,
  fetchSourceNow,
  listSources,
  updateSource,
  type FetchSummary,
  type Source,
  type SourceOrigin,
} from './api'
import { SourceForm, type SourceFormValues } from './SourceForm'

const ORIGIN_LABELS: Record<SourceOrigin, string> = { huntable_seed: 'Huntable seed', user: 'Added by you' }

function byName(a: Source, b: Source) {
  return a.name.localeCompare(b.name)
}

function describeFetch(name: string, result: FetchSummary): string {
  return `${name}: ${result.new} new, ${result.duplicates} already stored, ${result.too_short} too short, ${result.filtered} filtered`
}

export function SourcesPage() {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [alert, setAlert] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  // Rows with a toggle in flight. Each stays disabled until its own request settles.
  const [pendingIds, setPendingIds] = useState<ReadonlySet<number>>(new Set())
  const [fetchingIds, setFetchingIds] = useState<ReadonlySet<number>>(new Set())
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => {
    listSources()
      .then(setSources)
      .catch(() => setAlert('Could not load sources. Check that the API is running.'))
  }, [])

  async function fetchNow(source: Source) {
    setAlert(null)
    setStatus(null)
    setFetchingIds((current) => new Set(current).add(source.id))
    try {
      setStatus(describeFetch(source.name, await fetchSourceNow(source.id)))
    } catch (error) {
      setAlert(`Fetch failed for ${source.name}: ${describeError(error)}`)
    } finally {
      setFetchingIds((current) => {
        const next = new Set(current)
        next.delete(source.id)
        return next
      })
      // Pick up last_checked_at / last_error for this source only. Replacing the whole list could undo a
      // toggle or drop a source added while this request was in flight.
      listSources()
        .then((fresh) => {
          const updated = fresh.find((candidate) => candidate.id === source.id)
          if (!updated) return
          setSources((current) =>
            (current ?? []).map((row) =>
              row.id === source.id ? { ...row, last_checked_at: updated.last_checked_at, last_error: updated.last_error } : row,
            ),
          )
        })
        .catch(() => {})
    }
  }

  function replace(updated: Source) {
    setSources((current) => (current ?? []).map((source) => (source.id === updated.id ? updated : source)).sort(byName))
  }

  async function add(values: SourceFormValues) {
    const created = await createSource(values)
    setSources((current) => [...(current ?? []), created].sort(byName))
  }

  async function save(source: Source, values: SourceFormValues) {
    replace(await updateSource(source.id, { ...values, rss_url: values.rss_url || null }))
    setEditingId(null)
  }

  function setPending(id: number, pending: boolean) {
    setPendingIds((current) => {
      const next = new Set(current)
      if (pending) next.add(id)
      else next.delete(id)
      return next
    })
  }

  async function toggle(source: Source) {
    setAlert(null)
    setPending(source.id, true)
    try {
      replace(await updateSource(source.id, { active: !source.active }))
    } catch (error) {
      setAlert(`Could not update ${source.name}: ${describeError(error)}`)
    } finally {
      setPending(source.id, false)
    }
  }

  const activeCount = sources?.filter((source) => source.active).length ?? 0

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>Sources</h1>
          <p className="muted">
            {sources ? `${sources.length} sources, ${activeCount} active` : 'Threat intelligence feeds the pipeline reads from.'}
          </p>
        </div>
      </header>

      {alert && (
        <p className="banner banner-error" role="alert">
          {alert}
        </p>
      )}
      {status && (
        <p className="banner banner-ok" role="status">
          {status}
        </p>
      )}

      {/* Shown only once the list has loaded, so a new source can't be overwritten by a late list response. */}
      {sources && (
        <div className="panel">
          <h2>Add an RSS source</h2>
          <SourceForm label="Add RSS source" submitLabel="Add source" onSubmit={add} resetOnSuccess />
        </div>
      )}

      {sources === null && !alert && <p className="muted">Loading sources…</p>}
      {sources && sources.length === 0 && <p className="muted">No sources yet. Seed them with the CLI or add one above.</p>}
      {sources && sources.length > 0 && (
        <div className="table-wrap">
          <table className="sources-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Feed</th>
                <th scope="col">Origin</th>
                <th scope="col">Active</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => (
                <Fragment key={source.id}>
                  <tr>
                    <th scope="row">
                      <a href={source.url} target="_blank" rel="noreferrer">
                        {source.name}
                      </a>
                      {source.last_error && <span className="row-error"> · {source.last_error}</span>}
                    </th>
                    <td>
                      {source.rss_url ? (
                        <span className="feed-text" title={source.rss_url}>
                          {source.rss_url}
                        </span>
                      ) : (
                        <span className="muted">No RSS feed</span>
                      )}
                    </td>
                    <td>
                      <span className={`tag tag-${source.origin}`}>{ORIGIN_LABELS[source.origin]}</span>
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        className="switch"
                        aria-label={`Active: ${source.name}`}
                        checked={source.active}
                        disabled={pendingIds.has(source.id)}
                        onChange={() => toggle(source)}
                      />
                    </td>
                    <td className="row-actions">
                      {source.rss_url && (
                        <button
                          type="button"
                          className="link-button"
                          aria-label={`Fetch ${source.name} now`}
                          disabled={fetchingIds.has(source.id)}
                          onClick={() => fetchNow(source)}
                        >
                          {fetchingIds.has(source.id) ? 'Fetching…' : 'Fetch now'}
                        </button>
                      )}
                      <button
                        type="button"
                        className="link-button"
                        aria-label={`Edit ${source.name}`}
                        aria-expanded={editingId === source.id}
                        onClick={() => setEditingId(editingId === source.id ? null : source.id)}
                      >
                        Edit
                      </button>
                    </td>
                  </tr>
                  {editingId === source.id && (
                    <tr className="edit-row">
                      <td colSpan={5}>
                        <SourceForm
                          label={`Edit ${source.name}`}
                          submitLabel="Save"
                          initial={{ name: source.name, url: source.url, rss_url: source.rss_url ?? '' }}
                          onSubmit={(values) => save(source, values)}
                          onCancel={() => setEditingId(null)}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
