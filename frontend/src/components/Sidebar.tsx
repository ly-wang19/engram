import { NavLink } from 'react-router-dom'
import {
  Clock,
  Database,
  LayoutDashboard,
  MessagesSquare,
  Search,
  Share2,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  X,
} from 'lucide-react'

import { cx } from './ui'

const NAV = [
  { to: '/', label: '总览', icon: LayoutDashboard, end: true },
  { to: '/ask', label: '记忆问答', icon: Search },
  { to: '/facts', label: '事实管理', icon: Database },
  { to: '/timeline', label: '时间线', icon: Clock },
  { to: '/graph', label: '关系图谱', icon: Share2 },
  { to: '/focus', label: '关注点', icon: Target },
  { to: '/conversations', label: '原始对话', icon: MessagesSquare },
  { to: '/settings', label: '记忆策略', icon: SlidersHorizontal },
  { to: '/privacy', label: '隐私与数据', icon: ShieldCheck },
]

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
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
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="px-5 py-4 text-[11px] leading-relaxed text-ghost">
          <p className="font-semibold text-slate-300">Engram</p>
          <p>开源长期记忆引擎 · 你的记忆只属于你</p>
        </div>
      </aside>
    </>
  )
}

export function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="text-2xl">🧠</span>
      <div className="leading-tight">
        <div className="bg-brand-gradient bg-clip-text text-base font-extrabold text-transparent">Engram</div>
        <div className="text-[10px] tracking-[0.18em] text-ghost">记忆控制台</div>
      </div>
    </div>
  )
}
