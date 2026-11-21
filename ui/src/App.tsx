import { useEffect, useState, type MouseEvent } from 'react'

import { ArticleReader, ArticlesPage } from './ArticlesPage'
import { ReviewPage } from './ReviewPage'
import { normalize, route } from './routes'
import { SourcesPage } from './SourcesPage'

/** Minimal history-based routing for a handful of pages; swap for a router library if routes grow. */
function usePath(): [string, (path: string) => void] {
  const [path, setPath] = useState(normalize(window.location.pathname))
  useEffect(() => {
    const onPop = () => setPath(normalize(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  function navigate(next: string) {
    window.history.pushState(null, '', next)
    setPath(normalize(next))
    window.scrollTo(0, 0)
  }
  return [path, navigate]
}

const NAV = [
  { href: '/', label: 'Sources', pages: ['sources'] },
  { href: '/articles', label: 'Articles', pages: ['articles', 'article'] },
  { href: '/review', label: 'Review', pages: ['review'] },
]

export function App() {
  const [path, navigate] = usePath()
  const current = route(path)

  function follow(event: MouseEvent<HTMLAnchorElement>, href: string) {
    event.preventDefault()
    navigate(href)
  }

  return (
    <>
      <header className="app-bar">
        <span className="brand">Detection Platform</span>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              aria-current={item.pages.includes(current.page) ? 'page' : undefined}
              onClick={(event) => follow(event, item.href)}
            >
              {item.label}
            </a>
          ))}
        </nav>
      </header>
      <main>
        {current.page === 'sources' && <SourcesPage />}
        {current.page === 'articles' && <ArticlesPage onOpen={(id) => navigate(`/articles/${id}`)} />}
        {/* key: a different article remounts the reader, so the previous one never shows under the new URL */}
        {current.page === 'article' && (
          <ArticleReader key={current.id} id={current.id} onBack={() => navigate('/articles')} />
        )}
        {current.page === 'review' && <ReviewPage onOpenArticle={(id) => navigate(`/articles/${id}`)} />}
        {current.page === 'not-found' && (
          <section className="page">
            <h1>Page not found</h1>
            <p>
              <a href="/" onClick={(event) => follow(event, '/')}>
                Go to Sources
              </a>
            </p>
          </section>
        )}
      </main>
    </>
  )
}
