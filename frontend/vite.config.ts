import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Static single-page app. Dev on :5175; /api is proxied to the SWMMCanada
// backend (FastAPI tasks-api) on :8000. The frontend only ever talks to the
// async task API — submit AOI, poll progress, download the model package.
// https://vite.dev/config/
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    proxy: { '/api': 'http://localhost:8000' },
  },
})
