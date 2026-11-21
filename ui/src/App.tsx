import { useEffect, useState, type MouseEvent } from 'react'

import { getRuleCounts } from './api'
import { ArticleReader, ArticlesPage } from './ArticlesPage'
import { DashboardPage } from './DashboardPage'
import { ReviewPage } from './ReviewPage'
import { followLink, normalize, route } from './routes'
import { SourcesPage } from './SourcesPage'

interface Location {
  path: string
  search: string
}

function current(): Location {
  return { path: normalize(window.location.pathname), search: window.location.search }
}

/** Minimal history-based routing (path + query string); swap for a router library if routes nest. */
function useLocation(): [Location, (href: string) => void] {
  const [location, setLocation] = useState<Location>(current)
  useEffect(() => {
    const onPop = () => setLocation(current())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  function navigate(href: string) {
    window.history.pushState(null, '', href)
    setLocation(current())
    window.scrollTo(0, 0)
  }
  return [location, navigate]
}

const NAV = [
  { href: '/', label: 'Dashboard', pages: ['dashboard'] },
  { href: '/articles', label: 'Articles', pages: ['articles', 'article'] },
  { href: '/review', label: 'Review', pages: ['review'] },
  { href: '/sources', label: 'Sources', pages: ['sources'] },
]

export function App() {
  const [location, navigate] = useLocation()
  const page = route(location.path)
  const [pending, setPending] = useState<number | null>(null)
  const [rulesChanged, setRulesChanged] = useState(0)

  // Refresh the review badge when the page changes or a rule is reviewed (cheap: one count query).
  useEffect(() => {
    let current = true // a slow earlier response must not overwrite a newer count
    getRuleCounts()
      .then((counts) => current && setPending(counts.pending_review))
      .catch(() => current && setPending(null))
    return () => {
      current = false
    }
  }, [location.path, rulesChanged])

  function follow(event: MouseEvent<HTMLAnchorElement>, href: string) {
    followLink(event, () => navigate(href))
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" onClick={(event) => follow(event, '/')}>
          <span className="brand-mark" aria-hidden="true">◆</span> Detection Platform
        </a>
        <nav aria-label="Main">
          {NAV.map((item) => {
            const isReview = item.label === 'Review' && pending !== null && pending > 0
            return (
              <a
                key={item.href}
                href={item.href}
                aria-current={item.pages.includes(page.page) ? 'page' : undefined}
                aria-label={isReview ? `Review, ${pending} pending` : undefined}
                onClick={(event) => follow(event, item.href)}
              >
                <span>{item.label}</span>
                {isReview && (
                  <span className="nav-count" aria-hidden="true">
                    {pending}
                  </span>
                )}
              </a>
            )
          })}
        </nav>
      </aside>
      <main>
        {page.page === 'dashboard' && (
          <DashboardPage onOpenArticle={(id) => navigate(`/articles/${id}`)} onNavigate={navigate} />
        )}
        {page.page === 'sources' && <SourcesPage />}
        {page.page === 'articles' && <ArticlesPage onOpen={(id) => navigate(`/articles/${id}`)} />}
        {/* key: a different article remounts the reader, so the previous one never shows under the new URL */}
        {page.page === 'article' && (
          <ArticleReader key={page.id} id={page.id} onBack={() => navigate('/articles')} />
        )}
        {page.page === 'review' && (
          <ReviewPage
            onOpenArticle={(id) => navigate(`/articles/${id}`)}
            onRulesChanged={() => setRulesChanged((count) => count + 1)}
          />
        )}
        {page.page === 'not-found' && (
          <section className="page">
            <h1>Page not found</h1>
            <p>
              <a href="/" onClick={(event) => follow(event, '/')}>
                Go to the dashboard
              </a>
            </p>
          </section>
        )}
      </main>
    </div>
  )
}
