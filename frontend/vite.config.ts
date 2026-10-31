import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Only the build environment is read here; declared locally rather than pulling in @types/node.
declare const process: { env: Record<string, string | undefined> }

// The console is served by the FastAPI memory server under `/ui/` in production
// (engram/server/app.py mounts the built `dist/`). In dev, Vite proxies the API
// to the running server so the SPA talks to the real backend with zero CORS.
// A host app that serves it under its own prefix builds with VITE_BASE (e.g. 如我: `/engram/ui/` with
// VITE_API_BASE=/engram VITE_EMBEDDED=1, forwarding /engram/* to Engram with the prefix stripped).
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE || '/ui/',
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
