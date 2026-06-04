import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { AlertTriangle, Inbox, Loader2 } from 'lucide-react'

/* ---------- utilities ---------- */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/* ---------- Button ---------- */
type Variant = 'primary' | 'ghost' | 'danger'
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  loading?: boolean
}
export function Button({ variant = 'primary', loading, className, children, disabled, ...rest }: ButtonProps) {
  const cls = variant === 'primary' ? 'btn-primary' : variant === 'danger' ? 'btn-danger' : 'btn-ghost'
  return (
    <button className={cx(cls, className)} disabled={disabled || loading} {...rest}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  )
}

/* ---------- PageHeader ---------- */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold tracking-tight sm:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ghost">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/* ---------- Card ---------- */
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cx('card p-5 sm:p-6', className)}>{children}</section>
}

export function CardTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold tracking-wide text-brand-cyan">{children}</h3>
      {hint && <span className="text-xs text-ghost">{hint}</span>}
    </div>
  )
}

/* ---------- Badge ---------- */
export function Badge({
  children,
  tone = 'default',
}: {
  children: ReactNode
  tone?: 'default' | 'live' | 'old' | 'user' | 'cyan' | 'violet'
}) {
  const tones: Record<string, string> = {
    default: 'bg-white/5 text-ghost',
    live: 'bg-brand-mint/15 text-brand-mint',
    old: 'bg-white/5 text-ghost line-through',
    user: 'bg-brand-amber/15 text-brand-amber',
    cyan: 'bg-brand-cyan/15 text-brand-cyan',
    violet: 'bg-brand-violet/15 text-brand-violet',
  }
  return (
    <span className={cx('inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium', tones[tone])}>
      {children}
    </span>
  )
}

/* ---------- StatCard ---------- */
export function StatCard({
  label,
  value,
  icon,
  accent = 'cyan',
}: {
  label: string
  value: ReactNode
  icon: ReactNode
  accent?: 'cyan' | 'violet' | 'mint' | 'amber'
}) {
  const ring: Record<string, string> = {
    cyan: 'text-brand-cyan',
    violet: 'text-brand-violet',
    mint: 'text-brand-mint',
    amber: 'text-brand-amber',
  }
  return (
    <div className="card flex items-center gap-4 p-4">
      <div className={cx('grid h-11 w-11 place-items-center rounded-xl bg-white/5', ring[accent])}>{icon}</div>
      <div>
        <div className="text-2xl font-bold leading-none">{value}</div>
        <div className="mt-1 text-xs text-ghost">{label}</div>
      </div>
    </div>
  )
}

/* ---------- Field ---------- */
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <div className="mt-1.5">{children}</div>
    </label>
  )
}

/* ---------- States ---------- */
export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-ghost">
      <Loader2 className="h-5 w-5 animate-spin" />
      {label && <span className="text-sm">{label}</span>}
    </div>
  )
}

export function EmptyState({ title, hint, icon }: { title: string; hint?: string; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-14 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-2xl bg-white/5 text-ghost">
        {icon ?? <Inbox className="h-6 w-6" />}
      </div>
      <div className="text-sm font-medium text-slate-200">{title}</div>
      {hint && <div className="max-w-sm text-xs text-ghost">{hint}</div>}
    </div>
  )
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-brand-rose/30 bg-brand-rose/10 p-4 text-sm text-brand-rose">
      <AlertTriangle className="h-5 w-5 shrink-0" />
      <span>{message}</span>
    </div>
  )
}
