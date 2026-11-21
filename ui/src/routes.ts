import type { MouseEvent } from 'react'

export type Route =
  | { page: 'dashboard' }
  | { page: 'sources' }
  | { page: 'articles' }
  | { page: 'article'; id: number }
  | { page: 'review' }
  | { page: 'not-found' }

/** Path without a trailing slash (except the root). */
export function normalize(path: string): string {
  return path.length > 1 ? path.replace(/\/+$/, '') : path
}

export function route(path: string): Route {
  if (path === '/') return { page: 'dashboard' }
  if (path === '/sources') return { page: 'sources' }
  if (path === '/articles') return { page: 'articles' }
  if (path === '/review') return { page: 'review' }
  const article = /^\/articles\/(\d+)$/.exec(path)
  if (article) return { page: 'article', id: Number(article[1]) }
  return { page: 'not-found' }
}

/**
 * Handle a click on an internal link in place (history navigation). Modified or non-left clicks are left to the
 * browser, so Ctrl/Cmd/Shift-click still opens the link's real href in a new tab or window.
 */
export function followLink(event: MouseEvent<HTMLAnchorElement>, go: () => void): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return
  }
  event.preventDefault()
  go()
}
