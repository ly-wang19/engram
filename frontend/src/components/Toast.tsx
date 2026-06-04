import { useEffect } from 'react'
import { create } from 'zustand'
import { CheckCircle2, Info, X, XCircle } from 'lucide-react'

import { cx } from './ui'

type Kind = 'success' | 'error' | 'info'
interface Toast {
  id: number
  kind: Kind
  message: string
}

interface ToastState {
  toasts: Toast[]
  push: (kind: Kind, message: string) => void
  dismiss: (id: number) => void
}

let seq = 1
const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (kind, message) => {
    const id = seq++
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

/** Imperative helper usable anywhere (including outside React event handlers). */
export const toast = {
  success: (m: string) => useToastStore.getState().push('success', m),
  error: (m: string) => useToastStore.getState().push('error', m),
  info: (m: string) => useToastStore.getState().push('info', m),
}

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)

  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[min(92vw,360px)] flex-col gap-2">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onClose={() => dismiss(t.id)} />
      ))}
    </div>
  )
}

function ToastItem({ toast: t, onClose }: { toast: Toast; onClose: () => void }) {
  useEffect(() => {
    const handle = setTimeout(onClose, 4000)
    return () => clearTimeout(handle)
  }, [onClose])

  const icon =
    t.kind === 'success' ? (
      <CheckCircle2 className="h-5 w-5 text-brand-mint" />
    ) : t.kind === 'error' ? (
      <XCircle className="h-5 w-5 text-brand-rose" />
    ) : (
      <Info className="h-5 w-5 text-brand-cyan" />
    )

  return (
    <div
      className={cx(
        'pointer-events-auto flex animate-fade-up items-start gap-3 rounded-xl border border-line bg-ink-800/95 p-3.5 pr-2 shadow-card backdrop-blur',
      )}
    >
      {icon}
      <p className="flex-1 text-sm leading-snug text-slate-100">{t.message}</p>
      <button onClick={onClose} className="rounded-md p-1 text-ghost transition hover:text-slate-100">
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
