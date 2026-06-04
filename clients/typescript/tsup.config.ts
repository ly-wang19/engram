import { defineConfig } from 'tsup'

// Dual ESM + CJS build with type declarations, so the SDK works in Node (import or require),
// bundlers, and the browser. Zero runtime dependencies (it uses the global fetch).
export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm', 'cjs'],
  dts: true,
  clean: true,
  sourcemap: true,
  minify: false,
  treeshake: true,
  target: 'es2021',
})
