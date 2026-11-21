/**
 * A small bar chart drawn with CSS, plus the same data as a table for screen readers (and tests).
 * Hand-rolled on purpose: the dashboard only needs bar charts, so a charting library isn't worth its weight.
 */
export interface Bar {
  label: string
  value: number
  tone?: 'accent' | 'muted' | 'danger' | 'good'
}

export function BarChart({
  title,
  bars,
  orientation = 'horizontal',
  format = (value) => value.toLocaleString('en-US'),
  empty = 'No data yet.',
}: {
  title: string
  bars: Bar[]
  orientation?: 'horizontal' | 'vertical'
  format?: (value: number) => string
  empty?: string
}) {
  if (bars.length === 0) {
    return (
      <figure className={`bar-chart bar-chart-${orientation}`}>
        <figcaption>{title}</figcaption>
        <p className="muted">{empty}</p>
      </figure>
    )
  }
  const max = Math.max(1, ...bars.map((bar) => bar.value))
  return (
    <figure className={`bar-chart bar-chart-${orientation}`}>
      <figcaption>{title}</figcaption>
      <div className="bars" aria-hidden="true">
        {bars.map((bar) => (
          <div className="bar-row" key={bar.label}>
            <span className="bar-label">{bar.label}</span>
            <span className="bar-track">
              <span
                className={`bar-fill tone-${bar.tone ?? 'accent'}`}
                style={orientation === 'horizontal' ? { width: `${(bar.value / max) * 100}%` } : { height: `${(bar.value / max) * 100}%` }}
              />
            </span>
            <span className="bar-value">{format(bar.value)}</span>
          </div>
        ))}
      </div>
      <table className="visually-hidden">
        <caption>{title}</caption>
        <tbody>
          {bars.map((bar) => (
            <tr key={bar.label}>
              <th scope="row">{bar.label}</th>
              <td>{format(bar.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}
