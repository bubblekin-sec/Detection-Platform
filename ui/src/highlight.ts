/** A highlighted range of the article text. Offsets are UTF-16 code units, which is what JavaScript strings index. */
export interface Mark {
  start: number
  end: number
  kind: 'keyword' | 'evidence'
  label: string // the keyword or the observable's field name
  bucket?: string // keyword bucket (perfect, lolbas, intelligence, good, negative)
}

export interface Segment {
  start: number
  end: number
  text: string
  marks: Mark[] // every mark covering this whole segment, in input order; empty for plain text
}

/**
 * Split `text` into consecutive segments at every mark boundary, so overlapping and nested marks (a keyword inside
 * an evidence quote, "rundll32" inside "rundll32.exe") each become flat runs that carry all the marks covering them.
 * Marks outside the text are clamped; empty or inverted ones are dropped. Concatenating the segments gives `text`.
 *
 * One sweep over the boundaries, keeping the set of marks open at each point, so 2000 spans cost a few
 * milliseconds rather than a check of every mark for every segment.
 */
export function segments(text: string, marks: Mark[]): Segment[] {
  const valid = marks
    .map((mark) => ({ ...mark, start: Math.max(0, mark.start), end: Math.min(text.length, mark.end) }))
    .filter((mark) => mark.start < mark.end)
  const opening = new Map<number, number[]>() // position -> indexes of marks starting there
  const closing = new Map<number, number[]>()
  valid.forEach((mark, index) => {
    opening.set(mark.start, [...(opening.get(mark.start) ?? []), index])
    closing.set(mark.end, [...(closing.get(mark.end) ?? []), index])
  })
  const cuts = [...new Set([0, text.length, ...opening.keys(), ...closing.keys()])].sort((a, b) => a - b)
  const open = new Set<number>()
  const result: Segment[] = []
  for (let index = 0; index < cuts.length - 1; index++) {
    const start = cuts[index]
    const end = cuts[index + 1]
    for (const closed of closing.get(start) ?? []) open.delete(closed)
    for (const opened of opening.get(start) ?? []) open.add(opened)
    result.push({
      start,
      end,
      text: text.slice(start, end),
      marks: [...open].sort((a, b) => a - b).map((markIndex) => valid[markIndex]),
    })
  }
  return result
}

/** The id of the highlight where an evidence quote starting at `start` begins, for jumping to it. */
export function evidenceAnchor(start: number): string {
  return `evidence-at-${start}`
}
