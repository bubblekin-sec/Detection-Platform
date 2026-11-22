import { describe, expect, it } from 'vitest'

import { parseArticleQuery, toApiQuery, toSearch } from './articleQuery'

describe('article query', () => {
  it('defaults to newest first with no parameters', () => {
    const query = parseArticleQuery('')

    expect(query).toEqual({ sort: 'published', page: 0 })
    expect(toSearch(query)).toBe('')
    expect(toApiQuery(query)).toBe('')
  })

  it('round-trips a filtered view through the URL', () => {
    const search = '?source_id=3&sort=priority&order=asc&band=Very+Poor&status=not_run&page=2'

    const query = parseArticleQuery(search)

    expect(query).toEqual({ sort: 'priority', order: 'asc', band: 'Very Poor', status: 'not_run', sourceId: 3, page: 2 })
    expect(toSearch(query)).toBe(search)
  })

  it('turns the page into an API offset', () => {
    expect(toApiQuery(parseArticleQuery('?sort=priority&page=2'))).toBe('?sort=priority&offset=100')
  })

  it('ignores values the API would reject', () => {
    expect(parseArticleQuery('?page=99999999999999999999').page).toBe(0) // would serialise as an exponent
    expect(parseArticleQuery('?sort=bogus&order=up&band=Great&status=x&source_id=-1&page=1.5')).toEqual({
      sort: 'published',
      page: 0,
    })
  })
})
