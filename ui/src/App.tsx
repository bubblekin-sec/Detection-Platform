import { SourcesPage } from './SourcesPage'

export function App() {
  return (
    <>
      <header className="app-bar">
        <span className="brand">Detection Platform</span>
        <nav aria-label="Main">
          <a href="/" aria-current="page">
            Sources
          </a>
        </nav>
      </header>
      <main>
        <SourcesPage />
      </main>
    </>
  )
}
