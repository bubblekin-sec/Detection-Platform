import { useEffect, useState, type MouseEvent } from 'react'

import { BarChart, type Bar } from './BarChart'
import { describeError, getDashboard, type Dashboard } from './api'
import { PipelineBadge, ScoreBadge } from './badges'
import { money } from './format'
import { followLink } from './routes'

const DAY = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const BAND_TONES: Record<string, Bar['tone']> = { Excellent: 'good', Good: 'good', Fair: 'accent', Poor: 'muted', 'Very Poor': 'muted' }

export function DashboardPage({
  onOpenArticle,
  onNavigate,
}: {
  onOpenArticle: (id: number) => void
  onNavigate: (path: string) => void
}) {
  const [data, setData] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getDashboard()
      .then(setData)
      .catch((failure) => setError(`Could not load the dashboard: ${describeError(failure)}`))
  }, [])

  function follow(event: MouseEvent<HTMLAnchorElement>, action: () => void) {
    followLink(event, action)
  }

  if (error) {
    return (
      <section className="page">
        <h1>Dashboard</h1>
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      </section>
    )
  }
  if (!data) {
    return (
      <section className="page">
        <h1>Dashboard</h1>
        <p className="muted">Loading…</p>
      </section>
    )
  }

  const { funnel } = data
  const thresholdLabel = funnel.threshold === null ? 'Above threshold' : `Above threshold (${funnel.threshold})`
  const funnelBars: Bar[] = [
    { label: 'Fetched', value: funnel.fetched, tone: 'muted' },
    { label: thresholdLabel, value: funnel.above_threshold },
    { label: 'Sent to models', value: funnel.run },
    { label: 'Relevant', value: funnel.relevant },
    { label: 'With rules', value: funnel.with_rules, tone: 'good' },
    { label: 'Approved', value: funnel.approved, tone: 'good' },
  ]
  const stats = [
    { label: 'Articles', value: funnel.fetched.toLocaleString('en-US') },
    { label: 'Priority', value: funnel.above_threshold.toLocaleString('en-US'), hint: `score ≥ ${funnel.threshold ?? '—'}` },
    { label: 'Relevant', value: funnel.relevant.toLocaleString('en-US') },
    { label: 'Rules approved', value: funnel.approved.toLocaleString('en-US') },
    { label: 'Model spend', value: money(data.spend.total), hint: 'all time, all profiles' },
  ]

  return (
    <section className="page dashboard">
      <header className="page-header">
        <h1>Dashboard</h1>
        {data.profile ? (
          <p className="muted">
            Profile {data.profile.slug} v{data.profile.version}. Articles below the priority threshold never reach a model.
          </p>
        ) : (
          <p className="banner banner-info" role="status">
            No detection profile yet, so nothing is scored. Run <code>uv run python -m app.cli seed-profiles</code> in
            api/, then <code>score-articles</code>.
          </p>
        )}
      </header>

      <div className="stat-grid">
        {stats.map((stat) => (
          <div className="stat-card" key={stat.label}>
            <span className="stat-label">{stat.label}</span>
            <span className="stat-value">{stat.value}</span>
            {stat.hint && <span className="stat-hint">{stat.hint}</span>}
          </div>
        ))}
      </div>

      <div className="dashboard-grid">
        <div className="panel">
          <BarChart title="Pipeline funnel" bars={funnelBars} />
        </div>
        <div className="panel">
          <BarChart
            title="Articles fetched per day"
            orientation="vertical"
            bars={data.volume.map((day) => ({ label: DAY.format(new Date(day.date)), value: day.articles }))}
          />
        </div>
        <div className="panel">
          <BarChart
            title="Priority score bands"
            bars={Object.entries(data.bands).map(([label, value]) => ({ label, value, tone: BAND_TONES[label] }))}
          />
        </div>
        <div className="panel">
          <BarChart
            title="Model spend by stage"
            format={money}
            empty="No model spend yet."
            bars={Object.entries(data.spend.by_stage).map(([label, value]) => ({ label, value }))}
          />
          {data.spend.unpriced_models.length > 0 && (
            <p className="muted small">No price for: {data.spend.unpriced_models.join(', ')}.</p>
          )}
        </div>

        <div className="panel span-2">
          <div className="panel-heading">
            <h2 id="top-heading">Top priority articles</h2>
            <a href="/articles?sort=priority" onClick={(event) => follow(event, () => onNavigate('/articles?sort=priority'))}>
              View queue →
            </a>
          </div>
          {data.top.length === 0 ? (
            <p className="muted">No scored articles yet. Fetch a source to collect some.</p>
          ) : (
            <ol className="top-list" aria-label="Top priority articles">
              {data.top.map((article) => (
                <li key={article.id}>
                  <ScoreBadge score={article.score} band={article.band} />
                  <a href={`/articles/${article.id}`} onClick={(event) => follow(event, () => onOpenArticle(article.id))}>
                    {article.title}
                  </a>
                  <span className="muted small">{article.source}</span>
                  <PipelineBadge state={article.pipeline} />
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="panel">
          <h2>Source health</h2>
          {data.failing_sources.length === 0 ? (
            <p className="muted">All sources healthy.</p>
          ) : (
            <ul className="failing-list">
              {data.failing_sources.map((source) => (
                <li key={source.id}>
                  <strong>{source.name}</strong>
                  <span className="row-error">{source.last_error}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}
