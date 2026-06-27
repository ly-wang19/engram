import { useState } from 'react'
import { Copy, Eye, EyeOff, LogOut, Menu, Wifi, WifiOff } from 'lucide-react'

import { useAuth } from '../store/auth'
import { useHealth } from '../hooks/queries'
import { useT } from '../i18n'
import { LangToggle } from './LangToggle'
import { toast } from './Toast'

function maskKey(key: string | null | undefined, reveal: boolean) {
  if (!key) return '?'
  if (reveal || key.length <= 6) return key
  return `${key.slice(0, 3)}…${key.slice(-3)}`
}

export function Topbar({ onMenu }: { onMenu: () => void }) {
  const apiKey = useAuth((s) => s.apiKey)
  const logout = useAuth((s) => s.logout)
  const health = useHealth()
  const online = health.isSuccess && health.data?.ok
  const t = useT()
  const [reveal, setReveal] = useState(false)

  const copyKey = async () => {
    if (!apiKey) return
    await navigator.clipboard?.writeText(apiKey)
    toast.success(t.topbar.keyCopied)
  }

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-ink/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1600px] items-center gap-3 px-4 sm:px-6 lg:px-8">
        <button onClick={onMenu} aria-label="Open navigation" className="rounded-lg p-2 text-ghost hover:text-slate-100 lg:hidden">
          <Menu className="h-5 w-5" />
        </button>

        <div className="flex-1" />

        <LangToggle />

        <span className="hidden items-center gap-1.5 text-xs text-ghost sm:flex">
          {online ? (
            <>
              <Wifi className="h-4 w-4 text-brand-mint" /> {t.topbar.online}
            </>
          ) : (
            <>
              <WifiOff className="h-4 w-4 text-brand-rose" /> {t.topbar.offline}
            </>
          )}
        </span>

        <div className="flex items-center gap-1.5 rounded-xl border border-line bg-white/5 px-2 py-1.5">
          <span className="grid h-6 w-6 place-items-center rounded-full bg-brand-gradient text-[11px] font-bold text-ink-900">
            {(apiKey ?? '?').slice(0, 1).toUpperCase()}
          </span>
          <span className="max-w-[116px] truncate text-sm text-slate-200">{maskKey(apiKey, reveal)}</span>
          <button
            type="button"
            onClick={() => setReveal((v) => !v)}
            aria-label={reveal ? t.topbar.hideKey : t.topbar.showKey}
            title={reveal ? t.topbar.hideKey : t.topbar.showKey}
            className="rounded-md p-1 text-ghost transition hover:text-slate-100"
          >
            {reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={copyKey}
            aria-label={t.topbar.copyKey}
            title={t.topbar.copyKey}
            className="rounded-md p-1 text-ghost transition hover:text-slate-100"
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
        </div>

        <button
          onClick={logout}
          className="flex items-center gap-1.5 rounded-xl border border-line bg-white/5 px-3 py-2 text-sm text-ghost transition hover:border-brand-rose/40 hover:text-brand-rose"
          title={t.topbar.logoutTitle}
        >
          <LogOut className="h-4 w-4" />
          <span className="hidden sm:inline">{t.topbar.logout}</span>
        </button>
      </div>
    </header>
  )
}
