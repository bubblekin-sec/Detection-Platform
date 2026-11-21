import { describe, expect, it } from 'vitest'

import { normalize, route } from './routes'

describe('route', () => {
  it.each([
    ['/', { page: 'sources' }],
    ['/articles', { page: 'articles' }],
    ['/articles/42', { page: 'article', id: 42 }],
    ['/review', { page: 'review' }],
    ['/articles/abc', { page: 'not-found' }],
    ['/articlesX', { page: 'not-found' }],
    ['/nope', { page: 'not-found' }],
  ])('maps %s', (path, expected) => {
    expect(route(path)).toEqual(expected)
  })
})

describe('normalize', () => {
  it('drops trailing slashes except on the root', () => {
    expect(normalize('/articles/5/')).toBe('/articles/5')
    expect(normalize('/')).toBe('/')
  })
})
