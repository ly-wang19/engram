import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The console is served by the FastAPI memory server under `/ui/` in production
// (engram/server/app.py mounts the built `dist/`). In dev, Vite proxies the API
// to the running server so the SPA talks to the real backend with zero CORS.
export default defineConfig({
  plugins: [react()],
  base: '/ui/',
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
