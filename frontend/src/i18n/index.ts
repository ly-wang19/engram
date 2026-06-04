import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { en, type Dict } from './en'
import { zh } from './zh'

export type Lang = 'zh' | 'en'

const dicts: Record<Lang, Dict> = { zh, en }

// First-visit default: honor the browser, fall back to English (the project's lingua
// franca). After the user picks once, the choice is persisted and wins over detection.
function detectLang(): Lang {
  if (typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('zh')) return 'zh'
  return 'en'
}

interface LangState {
  lang: Lang
  setLang: (lang: Lang) => void
  toggle: () => void
}

export const useLang = create<LangState>()(
  persist(
    (set, get) => ({
      lang: detectLang(),
      setLang: (lang) => set({ lang }),
      toggle: () => set({ lang: get().lang === 'zh' ? 'en' : 'zh' }),
    }),
    { name: 'engram.lang' },
  ),
)

/** Reactive translation dictionary — re-renders the component when the language changes. */
export function useT(): Dict {
  return dicts[useLang((s) => s.lang)]
}

/** Current language outside React (read once, non-reactive). */
export function currentLang(): Lang {
  return useLang.getState().lang
}

/** Translation dictionary outside React (e.g. the fetch client) — non-reactive. */
export function getT(): Dict {
  return dicts[currentLang()]
}
