import { NavLink } from 'react-router-dom'
import {
  Clock,
  Database,
  HeartPulse,
  LayoutDashboard,
  MessagesSquare,
  Search,
  Share2,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  UserRound,
  X,
} from 'lucide-react'

import { useT } from '../i18n'
import type { Dict } from '../i18n/en'
import { cx } from './ui'

const NAV: Array<{ to: string; key: keyof Dict['nav']; icon: typeof LayoutDashboard; end?: boolean }> = [
  { to: '/', key: 'dashboard', icon: LayoutDashboard, end: true },
  { to: '/profile', key: 'profile', icon: UserRound },
  { to: '/ask', key: 'ask', icon: Search },
  { to: '/facts', key: 'facts', icon: Database },
  { to: '/health', key: 'health', icon: HeartPulse },
  { to: '/timeline', key: 'timeline', icon: Clock },
  { to: '/graph', key: 'graph', icon: Share2 },
  { to: '/focus', key: 'focus', icon: Target },
  { to: '/conversations', key: 'conversations', icon: MessagesSquare },
  { to: '/settings', key: 'settings', icon: SlidersHorizontal },
  { to: '/privacy', key: 'privacy', icon: ShieldCheck },
]

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT()
  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm lg:hidden" onClick={onClose} />}
      <aside
        className={cx(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-line bg-ink-800/80 backdrop-blur-xl transition-transform lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex items-center justify-between px-5 py-5">
          <Brand />
          <button onClick={onClose} className="rounded-lg p-1.5 text-ghost lg:hidden">
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onClose}
              className={({ isActive }) =>
                cx(
                  'flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition',
                  isActive
                    ? 'bg-brand-cyan/10 text-white shadow-[inset_0_0_0_1px_rgba(34,211,238,0.25)]'
                    : 'text-ghost hover:bg-white/5 hover:text-slate-100',
                )
              }
            >
              <item.icon className="h-[18px] w-[18px]" />
              {t.nav[item.key]}
            </NavLink>
          ))}
        </nav>

        <div className="px-5 py-4 text-[11px] leading-relaxed text-ghost">
          <p className="font-semibold text-slate-300">Engram</p>
          <p>{t.nav.tagline}</p>
        </div>
      </aside>
    </>
  )
}

export function Brand() {
  const t = useT()
  return (
    <div className="flex items-center gap-2.5">
      <span className="text-2xl">🧠</span>
      <div className="leading-tight">
        <div className="bg-brand-gradient bg-clip-text text-base font-extrabold text-transparent">Engram</div>
        <div className="text-[10px] tracking-[0.18em] text-ghost">{t.nav.consoleSub}</div>
      </div>
    </div>
  )
}
