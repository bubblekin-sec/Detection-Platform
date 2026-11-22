export const READER_VIEWS = ['filtering', 'extraction', 'rules'] as const
export type ReaderView = (typeof READER_VIEWS)[number]
export const VIEW_LABELS: Record<ReaderView, string> = { filtering: 'Filtering', extraction: 'Extraction', rules: 'Rules' }

/** The `view` named in an article page's query string, if it is one we know. */
export function parseReaderView(search: string): ReaderView | undefined {
  const view = new URLSearchParams(search).get('view')
  return READER_VIEWS.find((candidate) => candidate === view)
}
