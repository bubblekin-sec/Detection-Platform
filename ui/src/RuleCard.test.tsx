import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RuleCard } from './RuleCard'
import type { DetectionRule } from './api'

const rule: DetectionRule = {
  id: '6f1d0b8e-3c1a-4b8e-9a6e-1f2a3b4c5d6e',
  title: 'Scheduled Task Created Pointing to Public Folder',
  level: 'high',
  status: 'pending_review',
  group: { platform: 'windows', telemetry_category: 'process_creation' },
  observable_ids: [1],
  sigma_yaml: 'title: Scheduled Task Created Pointing to Public Folder\nlevel: high\n',
  rationale: 'Covers the observed task creation.',
  queries: [
    {
      id: 1,
      target: 'defender_xdr',
      origin: 'converted',
      query_text: 'DeviceProcessEvents\n| where ProcessCommandLine contains "/create"',
      status: 'ok',
      error: null,
      warnings: [],
      stale: false,
    },
  ],
}

describe('RuleCard', () => {
  it('shows the Sigma, the converted query and the review status', () => {
    render(<RuleCard rule={rule} />)

    const card = screen.getByRole('article', { name: rule.title })
    expect(within(card).getByText('Pending review')).toBeInTheDocument()
    expect(within(card).getByRole('figure', { name: 'Sigma' })).toHaveTextContent('level: high')
    const kql = within(card).getByRole('region', { name: `Defender XDR KQL for ${rule.title}` })
    expect(within(kql).getByText(/DeviceProcessEvents/)).toBeInTheDocument()
    expect(within(kql).getByText('(converted from Sigma)')).toBeInTheDocument()
  })

  it('shows why there is no query, and any lint warnings', () => {
    render(
      <RuleCard
        rule={{
          ...rule,
          queries: [
            { ...rule.queries[0], status: 'failed', query_text: null, error: 'Unable to determine table name from rule.' },
            { ...rule.queries[0], id: 2, warnings: ['`endswith "\\Run*"` compares against a literal *'] },
          ],
        }}
      />,
    )

    expect(screen.getByText(/Unable to determine table name/)).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Query warnings' })).toHaveTextContent('literal *')
  })

  it('flags a query made from an older version of the Sigma', () => {
    render(<RuleCard rule={{ ...rule, queries: [{ ...rule.queries[0], stale: true }] }} />)

    expect(screen.getByText(/Out of date/)).toBeInTheDocument()
  })
})
