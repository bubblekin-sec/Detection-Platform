import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PriorityBreakdown } from './PriorityBreakdown'
import type { PriorityDetail } from './api'

const priority: PriorityDetail = {
  score: 47.5,
  band: 'Fair',
  terms: { perfect: ['rundll32.exe'], lolbas: [], intelligence: ['ransomware'] },
  threshold: 37.5,
  profile_version: 2,
  stale: false,
  buckets: {
    perfect: { terms: ['rundll32.exe'], count: 1, points: 37.5, max_points: 75 },
    lolbas: { terms: [], count: 0, points: 0, max_points: 10 },
    intelligence: { terms: ['ransomware'], count: 1, points: 5, max_points: 10 },
    good: { terms: ['lateral movement'], count: 1, points: 2.5, max_points: 5 },
    negative: { terms: ['webinar'], count: 1, points: 7.5, max_points: 15 },
  },
  spans: [
    { start: 0, end: 12, bucket: 'perfect', term: 'rundll32.exe' },
    { start: 20, end: 36, bucket: 'good', term: 'lateral movement' },
    { start: 40, end: 47, bucket: 'negative', term: 'webinar' },
  ],
  spans_truncated: false,
}

describe('PriorityBreakdown', () => {
  it('shows the score against the threshold and each bucket with its terms', () => {
    render(<PriorityBreakdown priority={priority} />)

    const panel = screen.getByRole('region', { name: 'Priority score' })
    expect(panel).toHaveTextContent('47.5, Fair')
    expect(panel).toHaveTextContent('At or above the threshold (37.5)')
    expect(screen.getByRole('meter', { name: 'Perfect points' })).toHaveValue(37.5)
    expect(within(screen.getByRole('list', { name: 'Perfect keywords' })).getByText('rundll32.exe')).toBeInTheDocument()
    expect(panel).toHaveTextContent('−7.5 / 15') // the negative bucket subtracts
    expect(panel).toHaveTextContent('No matches') // lolbas
  })

  it('marks terms that only matched in the title', () => {
    render(<PriorityBreakdown priority={priority} />)

    const intelligence = screen.getByRole('list', { name: 'Intelligence keywords' })
    expect(within(intelligence).getByRole('listitem')).toHaveTextContent('ransomware (title only)')
    expect(within(screen.getByRole('list', { name: 'Perfect keywords' })).getByRole('listitem')).not.toHaveTextContent(
      'title only',
    )
  })

  it('does not guess title-only matches when highlights were capped', () => {
    render(<PriorityBreakdown priority={{ ...priority, spans_truncated: true }} />)

    expect(screen.queryByText(/title only/)).not.toBeInTheDocument()
    expect(screen.getByText(/Only the first 3 keyword matches are highlighted/)).toBeInTheDocument()
  })

  it('explains below-threshold and stale scores', () => {
    render(<PriorityBreakdown priority={{ ...priority, score: 12, band: 'Very Poor', stale: true }} />)

    expect(screen.getByText(/Below the threshold \(37\.5\): no model is called/)).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent('older version of the scorer')
    expect(screen.queryByText(/title only/)).not.toBeInTheDocument() // old matching may differ from today's spans
  })
})
