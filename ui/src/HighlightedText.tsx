import { Fragment, memo, useMemo } from 'react'

import { segments, type Mark } from './highlight'

const BUCKET_STRENGTH = ['perfect', 'lolbas', 'intelligence', 'good', 'negative']

function describeMarks(marks: Mark[]): string {
  return marks.map((mark) => (mark.kind === 'evidence' ? `Evidence for ${mark.label}` : `${mark.bucket} keyword: ${mark.label}`)).join('; ')
}

function className(marks: Mark[]): string {
  const classes = ['hl']
  if (marks.some((mark) => mark.kind === 'evidence')) classes.push('hl-evidence')
  const buckets = marks.flatMap((mark) => (mark.kind === 'keyword' && mark.bucket ? [mark.bucket] : []))
  const strongest = BUCKET_STRENGTH.find((bucket) => buckets.includes(bucket))
  if (strongest) classes.push(`hl-${strongest}`)
  return classes.join(' ')
}

/** Article text with keyword and evidence highlights. Newlines are kept (the container uses pre-wrap). */
export const HighlightedText = memo(function HighlightedText({ text, marks }: { text: string; marks: Mark[] }) {
  const parts = useMemo(() => segments(text, marks), [text, marks])
  return (
    <div className="article-text article-text-marked">
      {parts.map((part) =>
        part.marks.length ? (
          <mark key={part.start} className={className(part.marks)} title={describeMarks(part.marks)}>
            {part.text}
          </mark>
        ) : (
          <Fragment key={part.start}>{part.text}</Fragment>
        ),
      )}
    </div>
  )
})
