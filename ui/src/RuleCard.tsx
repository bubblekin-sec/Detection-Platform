import type { DetectionRule } from './api'

const TARGET_LABELS: Record<string, string> = { defender_xdr: 'Defender XDR KQL' }
const ORIGIN_LABELS: Record<string, string> = { converted: 'converted from Sigma', llm: 'written by the model', human_edit: 'edited' }
const STATUS_LABELS: Record<DetectionRule['status'], string> = {
  pending_review: 'Pending review',
  approved: 'Approved',
  rejected: 'Rejected',
}

/** One generated rule: Sigma of record plus its query artifacts, with conversion errors and lint warnings. */
export function RuleCard({ rule }: { rule: DetectionRule }) {
  return (
    <article className="rule-card" aria-labelledby={`rule-${rule.id}`}>
      <header className="rule-header">
        <h4 id={`rule-${rule.id}`}>{rule.title}</h4>
        <p className="tag-row">
          <span className={`rule-status rule-status-${rule.status}`}>{STATUS_LABELS[rule.status]}</span>
          {rule.level && <span className="observable-tag">level: {rule.level}</span>}
          <span className="observable-tag">
            {rule.group.platform} · {rule.group.telemetry_category}
          </span>
        </p>
      </header>
      {rule.rationale && <p className="muted small">{rule.rationale}</p>}

      <figure className="code-figure" aria-labelledby={`sigma-${rule.id}`}>
        <figcaption id={`sigma-${rule.id}`}>Sigma</figcaption>
        <pre className="code-block">{rule.sigma_yaml}</pre>
      </figure>

      {rule.queries.map((query) => (
        <section key={query.id} aria-label={`${TARGET_LABELS[query.target] ?? query.target} for ${rule.title}`}>
          <h5>
            {TARGET_LABELS[query.target] ?? query.target}{' '}
            <span className="muted small">({ORIGIN_LABELS[query.origin] ?? query.origin})</span>
          </h5>
          {query.stale && <p className="warning">Out of date: the Sigma changed after this query was made.</p>}
          {query.status === 'ok' && query.query_text ? (
            <figure className="code-figure" aria-labelledby={`query-${query.id}`}>
              <figcaption id={`query-${query.id}`} className="visually-hidden">
                {TARGET_LABELS[query.target] ?? query.target} query
              </figcaption>
              <pre className="code-block">{query.query_text}</pre>
            </figure>
          ) : (
            <p className="warning">
              <strong>No query:</strong> {query.error ?? 'conversion failed.'}
            </p>
          )}
          {query.warnings.length > 0 && (
            <ul className="rule-warnings" aria-label="Query warnings">
              {query.warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </article>
  )
}
