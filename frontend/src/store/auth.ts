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

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: null,
      login: (key) => set({ apiKey: key.trim() }),
      logout: () => set({ apiKey: null }),
    }),
    { name: 'engram.auth' },
  ),
)

/** Read the key outside React (for the fetch client). */
export function currentKey(): string | null {
  return useAuth.getState().apiKey
}
