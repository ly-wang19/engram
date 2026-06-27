import { useState, type FormEvent } from 'react'
import { ArrowRight, KeyRound } from 'lucide-react'

import { Brand } from '../components/Sidebar'
import { Button } from '../components/ui'
import { LangToggle } from '../components/LangToggle'
import { useAuth } from '../store/auth'
import { useT } from '../i18n'
import { useHealth } from '../hooks/queries'

export default function Login() {
  const login = useAuth((s) => s.login)
  const t = useT()
  const health = useHealth()
  const [key, setKey] = useState('1') // demo namespace: public, fully-loaded memory to explore

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (key.trim()) login(key)
  }

  const mode =
    health.data?.auth_mode === 'api_keys'
      ? t.login.modeApiKeys
      : health.data?.auth_mode === 'disabled'
        ? t.login.modeDisabled
        : t.login.modeOpen

  return (
    <div className="relative grid min-h-screen place-items-center px-4">
      <LangToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-md animate-fade-up">
        <div className="mb-8 flex justify-center">
          <Brand />
        </div>

        <form onSubmit={submit} className="card p-7">
          <h1 className="text-lg font-bold">{t.login.title}</h1>
          <p className="mt-1.5 text-sm text-ghost">
            {t.login.subtitlePre}
            <code className="text-brand-cyan">1</code>
            {t.login.subtitlePost}
          </p>
          <p className="mt-3 rounded-lg border border-line bg-white/[0.04] px-3 py-2 text-xs leading-relaxed text-ghost">
            {mode}
            {health.data?.anonymous_allowed ? ` ${t.login.anonymousOn}` : ''}
          </p>

          <label className="mt-6 block">
            <span className="label">{t.login.keyLabel}</span>
            <div className="mt-1.5 flex items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3 focus-within:border-brand-cyan/60 focus-within:ring-2 focus-within:ring-brand-cyan/20">
              <KeyRound className="h-4 w-4 text-ghost" />
              <input
                autoFocus
                className="w-full bg-transparent py-2.5 text-sm outline-none placeholder:text-ghost/70"
                placeholder={t.login.placeholder}
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </div>
          </label>

          <Button type="submit" className="mt-6 w-full" disabled={!key.trim()}>
            {t.login.submit} <ArrowRight className="h-4 w-4" />
          </Button>

          <p className="mt-5 text-center text-xs text-ghost">{t.login.footer}</p>
        </form>
      </div>
    </div>
  )
}
