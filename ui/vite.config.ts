import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The browser always calls the same-origin /api path, so no API hostname is built into the bundle.
// Vite proxies it in development. In the container, nginx is planned to proxy it via API_UPSTREAM (increment 9).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
    unstubGlobals: true,
  },
})
