import { ArrowUp, Lock } from 'lucide-react'

import type { MemoryFact } from '../types'
import { useT } from '../i18n'
import { splitStamp } from '../lib/format'
import { cx } from './ui'

// Facts arrive newest-first (the server sorts by valid_at desc). The vertical rail +
// colored dots make the bi-temporal evolution readable: green = currently true,
// grey/strikethrough = superseded (a later fact replaced it), ↑ = this fact is an update.
export function Timeline({ facts }: { facts: MemoryFact[] }) {
  const t = useT()
  return (
    <div className="relative">
      <div className="absolute bottom-2 left-[104px] top-2 w-px bg-line" />
      <ul className="space-y-1">
        {facts.map((f) => {
          const live = f.status === 'live'
          const { date, time } = splitStamp(f.valid_at)
          return (
            <li key={f.id} className="relative flex items-start gap-4 py-2">
              <time className="w-[88px] shrink-0 pt-0.5 text-right tabular-nums leading-tight text-brand-cyan">
                <span className="block text-[11.5px]">{date}</span>
                {time && <span className="block text-[10px] text-brand-cyan/55">{time}</span>}
              </time>
              <span
                className={cx(
                  'relative z-10 mt-1 h-2.5 w-2.5 shrink-0 rounded-full ring-4 ring-ink',
                  live ? 'bg-brand-mint' : 'bg-slate-500',
                )}
              />
              <div className="min-w-0">
                <span className={cx('text-sm', !live && 'text-ghost line-through')}>{f.display || f.text}</span>
                <span className="ml-2 inline-flex flex-wrap items-center gap-1.5 align-middle">
                  {f.source === 'user' && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-brand-amber">
                      <Lock className="h-3 w-3" /> {t.common.mine}
                    </span>
                  )}
                  {f.supersedes && (
                    <span className="inline-flex items-center gap-0.5 rounded border border-brand-violet/40 px-1.5 py-px text-[10px] text-brand-violet">
                      <ArrowUp className="h-3 w-3" /> {t.common.updated}
                    </span>
                  )}
                  {!live && (
                    <span className="text-[10px] text-ghost">
                      {t.common.superseded}
                      {f.invalid_at ? ` · ${f.invalid_at}` : ''}
                    </span>
                  )}
                </span>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
