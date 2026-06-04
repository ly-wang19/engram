import { Languages } from 'lucide-react'

import { useLang, useT, type Lang } from '../i18n'
import { cx } from './ui'

// Segmented 中 / EN switch. Persisted via the i18n store, so the choice survives refresh.
export function LangToggle({ className }: { className?: string }) {
  const lang = useLang((s) => s.lang)
  const setLang = useLang((s) => s.setLang)
  const t = useT()

  const opts: Array<{ value: Lang; label: string; title: string }> = [
    { value: 'zh', label: t.lang.zh, title: t.lang.switchToZh },
    { value: 'en', label: t.lang.en, title: t.lang.switchToEn },
  ]

  return (
    <div
      role="group"
      aria-label={t.lang.label}
      className={cx('flex items-center rounded-xl border border-line bg-white/5 p-0.5', className)}
    >
      <Languages className="ml-1.5 mr-0.5 h-3.5 w-3.5 shrink-0 text-ghost" aria-hidden />
      {opts.map((o) => {
        const active = lang === o.value
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => setLang(o.value)}
            title={o.title}
            aria-pressed={active}
            className={cx(
              'rounded-lg px-2 py-1 text-xs font-semibold transition',
              active ? 'bg-brand-cyan/15 text-white' : 'text-ghost hover:text-slate-100',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
