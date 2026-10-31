/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  /** "1" when built for a host app that forwards /engram/* and owns the key (no login, no key chip). */
  readonly VITE_EMBEDDED?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
