export type Route = { page: 'sources' } | { page: 'articles' } | { page: 'article'; id: number } | { page: 'not-found' }

/** Path without a trailing slash (except the root). */
export function normalize(path: string): string {
  return path.length > 1 ? path.replace(/\/+$/, '') : path
}

export function route(path: string): Route {
  if (path === '/') return { page: 'sources' }
  if (path === '/articles') return { page: 'articles' }
  const article = /^\/articles\/(\d+)$/.exec(path)
  if (article) return { page: 'article', id: Number(article[1]) }
  return { page: 'not-found' }
}
