import { useState, type FormEvent } from 'react'
import { ArrowRight, KeyRound } from 'lucide-react'

import { Brand } from '../components/Sidebar'
import { Button } from '../components/ui'
import { useAuth } from '../store/auth'

export default function Login() {
  const login = useAuth((s) => s.login)
  const [key, setKey] = useState('1')  // demo namespace: public, fully-loaded memory to explore

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (key.trim()) login(key)
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-md animate-fade-up">
        <div className="mb-8 flex justify-center">
          <Brand />
        </div>

        <form onSubmit={submit} className="card p-7">
          <h1 className="text-lg font-bold">登录你的记忆空间</h1>
          <p className="mt-1.5 text-sm text-ghost">
            用 API key 进入。开放模式下，key 就是你的命名空间——随便起一个名字即可。想直接体验就用公开演示 key <code className="text-brand-cyan">1</code>（已装好一份完整记忆）。
          </p>

          <label className="mt-6 block">
            <span className="label">API Key</span>
            <div className="mt-1.5 flex items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3 focus-within:border-brand-cyan/60 focus-within:ring-2 focus-within:ring-brand-cyan/20">
              <KeyRound className="h-4 w-4 text-ghost" />
              <input
                autoFocus
                className="w-full bg-transparent py-2.5 text-sm outline-none placeholder:text-ghost/70"
                placeholder="例如 1 或 sk-..."
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </div>
          </label>

          <Button type="submit" className="mt-6 w-full" disabled={!key.trim()}>
            进入控制台 <ArrowRight className="h-4 w-4" />
          </Button>

          <p className="mt-5 text-center text-xs text-ghost">
            你的记忆存在自己的服务器上，只有持有此 key 才能访问。
          </p>
        </form>
      </div>
    </div>
  )
}
