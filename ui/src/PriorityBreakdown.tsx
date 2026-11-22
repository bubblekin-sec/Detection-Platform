import type { PriorityDetail } from './api'
import { ScoreBadge } from './badges'

const BUCKETS: { key: string; label: string; hint: string }[] = [
  { key: 'perfect', label: 'Perfect', hint: 'strong detection artefacts' },
  { key: 'lolbas', label: 'LOLBAS', hint: 'living-off-the-land binaries' },
  { key: 'intelligence', label: 'Intelligence', hint: 'threat-intel language' },
  { key: 'good', label: 'Good', hint: 'supporting technical terms' },
  { key: 'negative', label: 'Negative', hint: 'marketing or off-topic; subtracts' },
]

/** Why an article scored what it did: each keyword bucket's points, its cap, and the terms that matched. */
export function PriorityBreakdown({ priority }: { priority: PriorityDetail }) {
  const highlighted = new Set(priority.spans.map((span) => span.term))
  const { threshold } = priority
  const above = threshold !== null && priority.score >= threshold
  return (
    <section className="panel breakdown" aria-labelledby="breakdown-heading">
      <div className="panel-heading">
        <h2 id="breakdown-heading">Priority score</h2>
        <ScoreBadge score={priority.score} band={priority.band} />
      </div>
      <p className="muted small">
        {threshold === null
          ? 'No threshold set: this profile never sends articles to models automatically.'
          : above
            ? `At or above the threshold (${threshold}), so it qualifies for automatic model runs.`
            : `Below the threshold (${threshold}): no model is called unless you run detection.`}
      </p>
      {priority.stale && (
        <p className="banner banner-info small" role="note">
          Scored by an older version of the scorer. Run <code>score-articles --missing</code> to refresh.
        </p>
      )}
      <dl className="buckets">
        {BUCKETS.filter(({ key }) => priority.buckets[key]).map(({ key, label, hint }) => {
          const bucket = priority.buckets[key]
          const negative = key === 'negative'
          return (
            <div key={key} className={`bucket bucket-${key}`}>
              <dt>
                <span>
                  {label} <span className="muted small">· {hint}</span>
                </span>
                <span className="bucket-points">
                  {negative && bucket.points > 0 ? '−' : ''}
                  {bucket.points.toFixed(1)} / {bucket.max_points}
                </span>
              </dt>
              <dd>
                <meter min={0} max={bucket.max_points} value={bucket.points} aria-label={`${label} points`} />
                {bucket.terms.length === 0 ? (
                  <span className="muted small">No matches</span>
                ) : (
                  <ul className="chips" aria-label={`${label} keywords`}>
                    {bucket.terms.map((term) => (
                      <li key={term} className={`chip chip-${key}`}>
                        {term}
                        {/* With the same scorer and no cap, a matched term with no body span can only be in the title. */}
                        {!priority.stale && !priority.spans_truncated && !highlighted.has(term) && (
                          <span className="chip-note"> (title only)</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
            </div>
          )
        })}
      </dl>
      {priority.spans_truncated && (
        <p className="muted small">Only the first {priority.spans.length.toLocaleString('en-US')} keyword matches are highlighted.</p>
      )}
    </section>
  )
}
