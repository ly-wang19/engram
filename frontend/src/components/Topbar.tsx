import { LogOut, Menu, Wifi, WifiOff } from 'lucide-react'

import { useAuth } from '../store/auth'
import { useHealth } from '../hooks/queries'

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const apiKey = useAuth((s) => s.apiKey)
  const logout = useAuth((s) => s.logout)
  const health = useHealth()
  const online = health.isSuccess && health.data?.ok

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-ink/70 px-4 backdrop-blur-xl sm:px-6">
      <button onClick={onMenu} className="rounded-lg p-2 text-ghost hover:text-slate-100 lg:hidden">
        <Menu className="h-5 w-5" />
      </button>

      <div className="flex-1" />

      <span className="hidden items-center gap-1.5 text-xs text-ghost sm:flex">
        {online ? (
          <>
            <Wifi className="h-4 w-4 text-brand-mint" /> 服务在线
          </>
        ) : (
          <>
            <WifiOff className="h-4 w-4 text-brand-rose" /> 服务离线
          </>
        )}
      </span>

      <div className="flex items-center gap-2 rounded-xl border border-line bg-white/5 px-3 py-1.5">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-brand-gradient text-[11px] font-bold text-ink-900">
          {(apiKey ?? '?').slice(0, 1).toUpperCase()}
        </span>
        <span className="max-w-[120px] truncate text-sm text-slate-200">{apiKey}</span>
      </div>

      <button
        onClick={logout}
        className="flex items-center gap-1.5 rounded-xl border border-line bg-white/5 px-3 py-2 text-sm text-ghost transition hover:border-brand-rose/40 hover:text-brand-rose"
        title="退出登录"
      >
        <LogOut className="h-4 w-4" />
        <span className="hidden sm:inline">退出</span>
      </button>
    </header>
  )
}
