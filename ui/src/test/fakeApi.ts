import { vi } from 'vitest'

type Handler = (body: unknown) => { status: number; body: unknown }

/** Stub fetch with handlers keyed by "METHOD /path". Records each request's parsed JSON body. */
export function fakeApi(routes: Record<string, Handler>) {
  const calls: { route: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const route = `${init?.method ?? 'GET'} ${String(input)}`
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ route, body })
      const handler = routes[route]
      if (!handler) throw new Error(`Unexpected request: ${route}`)
      const result = handler(body)
      return new Response(JSON.stringify(result.body), {
        status: result.status,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return { calls }
}
