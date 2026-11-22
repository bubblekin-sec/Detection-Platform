import { describe, expect, it } from 'vitest'

import { segments, type Mark } from './highlight'

const keyword = (start: number, end: number, label: string, bucket = 'perfect'): Mark => ({
  start,
  end,
  kind: 'keyword',
  label,
  bucket,
})

function shape(text: string, marks: Mark[]) {
  return segments(text, marks).map((segment) => [segment.text, segment.marks.map((mark) => mark.label)])
}

describe('segments', () => {
  it('returns the whole text as one plain segment when nothing is marked', () => {
    expect(shape('plain text', [])).toEqual([['plain text', []]])
  })

  it('splits around a mark and always reassembles to the original text', () => {
    const text = 'They ran rundll32.exe twice.'
    const result = segments(text, [keyword(9, 21, 'rundll32.exe')])

    expect(result.map((segment) => segment.text).join('')).toBe(text)
    expect(shape(text, [keyword(9, 21, 'rundll32.exe')])).toEqual([
      ['They ran ', []],
      ['rundll32.exe', ['rundll32.exe']],
      [' twice.', []],
    ])
  })

  it('gives overlapping and nested marks flat runs carrying every covering mark', () => {
    const text = 'ran rundll32.exe now'
    const evidence: Mark = { start: 0, end: 16, kind: 'evidence', label: 'cmdline' }

    expect(shape(text, [keyword(4, 12, 'rundll32', 'lolbas'), keyword(4, 16, 'rundll32.exe'), evidence])).toEqual([
      ['ran ', ['cmdline']],
      ['rundll32', ['rundll32', 'rundll32.exe', 'cmdline']],
      ['.exe', ['rundll32.exe', 'cmdline']],
      [' now', []],
    ])
  })

  it('keeps newlines inside and between marks', () => {
    const text = 'line one\nschtasks /create\nend'
    const evidence: Mark = { start: 5, end: 25, kind: 'evidence', label: 'cmdline' }

    const marked = segments(text, [evidence]).find((segment) => segment.marks.length)!

    expect(marked.text).toBe('one\nschtasks /create')
  })

  it('clamps marks that run past the text and drops empty ones', () => {
    expect(shape('abc', [keyword(-2, 1, 'a'), keyword(2, 9, 'c'), keyword(1, 1, 'empty'), keyword(3, 2, 'inverted')])).toEqual([
      ['a', ['a']],
      ['b', []],
      ['c', ['c']],
    ])
  })

  it('works on UTF-16 offsets, so emoji before a mark do not shift it', () => {
    const text = '🔥 certutil.exe'

    expect(shape(text, [keyword(3, 15, 'certutil.exe')])).toEqual([
      ['🔥 ', []],
      ['certutil.exe', ['certutil.exe']],
    ])
  })
})
