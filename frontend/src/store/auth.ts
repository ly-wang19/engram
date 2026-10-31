import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// The API key IS the tenant identity (engram/server/app.py `auth()`): in open mode the
// key text is the namespace; with ENGRAM_API_KEYS it maps to a user. We persist it so a
// refresh keeps you signed in, and expose it to the API client for the Bearer header.
interface AuthState {
  apiKey: string | null
  login: (key: string) => void
  logout: () => void
}

// Built with VITE_EMBEDDED=1, the console lives inside a host app (如我) that forwards /engram/* to a
// local Engram and attaches the key itself. There is no login: the page never holds the secret, and
// `apiKey` is only a stable placeholder so per-tenant query keys and `enabled` gates keep working.
export const EMBEDDED = import.meta.env.VITE_EMBEDDED === '1'
const EMBEDDED_KEY = 'embedded'

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: EMBEDDED ? EMBEDDED_KEY : null,
      login: (key) => set({ apiKey: key.trim() }),
      logout: () => set({ apiKey: EMBEDDED ? EMBEDDED_KEY : null }),
    }),
    { name: EMBEDDED ? 'engram.auth.embedded' : 'engram.auth' },
  ),
)

/** Read the key outside React (for the fetch client). */
export function currentKey(): string | null {
  return useAuth.getState().apiKey
}
