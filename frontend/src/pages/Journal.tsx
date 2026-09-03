import { useEffect, useRef, useState } from 'react'
import { HelpCircle, Lock, Search, Trash2 } from 'lucide-react'

import { Button, Card, CardTitle, ErrorState, PageHeader, Spinner } from '../components/ui'
import { ConfirmDialog } from '../components/Modal'
import { toast } from '../components/Toast'
import { useCloseSession, useDeleteFact, useEditFact, useMemories, useSessions } from '../hooks/queries'
import { useT } from '../i18n'
import { splitStamp } from '../lib/format'
import type { MemoryFact } from '../types'

/** The archive of what sessions concluded.
 *
 * Per-turn extraction produces attributes; a session produces conclusions. Both are ordinary Facts —
 * conclusions are the ones whose predicate is one of the four outcome kinds — so this page is a
 * different *reading* of the same store, not a second store. The unit here is the session, because a
 * conclusion is supported by the whole conversation rather than by one line of it.
 */

const KIND_ORDER = ['decision', 'finding', 'lesson', 'open_question'] as const
export type OutcomeKind = (typeof KIND_ORDER)[number]

const KIND_COLOR: Record<string, string> = {
  decision: 'text-brand-cyan',
  finding: 'text-brand-mint',
  lesson: 'text-brand-amber',
  open_question: 'text-brand-violet',
}

const kindRank = (predicate: string) => {
  const i = KIND_ORDER.indexOf(predicate.toLowerCase() as OutcomeKind)
  return i === -1 ? KIND_ORDER.length : i
}

export interface OutcomeGroup {
  session: string
  facts: MemoryFact[]
}

/** Group outcomes into the sessions they came from (an outcome's subject IS its session id).
 *
 * Runs are consecutive and the server order (valid_at desc) is preserved: re-sorting would reshuffle
 * the whole archive every time another page is loaded. */
export function groupOutcomes(facts: MemoryFact[]): OutcomeGroup[] {
  const groups: OutcomeGroup[] = []
  for (const f of facts) {
    const last = groups[groups.length - 1]
    if (last && last.session === f.subject) last.facts.push(f)
    else groups.push({ session: f.subject, facts: [f] })
  }
  for (const g of groups) g.facts.sort((a, b) => kindRank(a.predicate) - kindRank(b.predicate))
  return groups
}

export default function Journal() {
  const t = useT()
  const [q, setQ] = useState('')
  const [showSuperseded, setShowSuperseded] = useState(false)
  const [kindChip, setKindChip] = useState<OutcomeKind | 'all'>('all')
  const [limit, setLimit] = useState(60)

  useEffect(() => {
    setLimit(60)
  }, [q, showSuperseded, kindChip])

  const { data, isLoading, isError, error } = useMemories({
    kind: 'outcomes',
    facts_limit: limit,
    episodes_limit: 0,
    q,
    status: showSuperseded ? undefined : 'live',
    include_sensitive: true,
  })

  if (isLoading) return <Spinner label={t.journal.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />

  const facts = data?.facts ?? []
  const rows = kindChip === 'all' ? facts : facts.filter((f) => f.predicate.toLowerCase() === kindChip)
  const groups = groupOutcomes(rows)
  const hasMore = data?.facts_page?.has_more ?? false
  const open = facts.filter((f) => f.status === 'live' && f.predicate.toLowerCase() === 'open_question').slice(0, 8)

  let lastDate = ''

  return (
    <div className="space-y-6">
      <PageHeader title={t.journal.title} subtitle={t.journal.subtitle} />

      {open.length > 0 && <OpenQuestionsCard items={open} />}

      <Card className="!p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3">
            <Search className="h-4 w-4 text-ghost" />
            <input
              aria-label={t.journal.searchPlaceholder}
              className="w-full bg-transparent py-2 text-sm outline-none placeholder:text-ghost/70"
              placeholder={t.journal.searchPlaceholder}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <KindChip active={kindChip === 'all'} onClick={() => setKindChip('all')} label={t.journal.kindAll} />
            {KIND_ORDER.map((k) => (
              <KindChip
                key={k}
                active={kindChip === k}
                onClick={() => setKindChip(k)}
                label={t.journal.kind[k]}
                color={KIND_COLOR[k]}
              />
            ))}
          </div>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input
              type="checkbox"
              checked={showSuperseded}
              onChange={(e) => setShowSuperseded(e.target.checked)}
              className="accent-brand-cyan"
            />
            {t.journal.showSuperseded}
          </label>
        </div>
      </Card>

      {rows.length ? (
        <>
          <div className="space-y-4">
            {groups.map((g, i) => {
              const { date } = splitStamp(g.facts[0]?.valid_at)
              const newDay = date !== lastDate
              lastDate = date
              return (
                <div key={`${g.session}-${i}`} className="space-y-3">
                  {newDay && (
                    <h2 className="sticky top-0 z-10 -mx-1 bg-ink-900/85 px-1 py-1.5 text-xs font-semibold tabular-nums tracking-wide text-brand-cyan backdrop-blur">
                      {date}
                    </h2>
                  )}
                  <SessionOutcomes group={g} editable />
                </div>
              )
            })}
          </div>
          {hasMore && (
            <div className="flex justify-center">
              <Button variant="ghost" onClick={() => setLimit((n) => n + 60)}>
                {t.journal.loadMore}
              </Button>
            </div>
          )}
        </>
      ) : (
        <JournalEmpty />
      )}

      {/* Deliberately not empty-state-only: one distilled session does not mean the backlog is done,
          and hiding the control the moment a conclusion exists leaves the rest of the owner's history
          with no route in from this page. Re-closing an already-distilled session is a no-op. */}
      {rows.length > 0 && (
        <Card>
          <BackfillSessions />
        </Card>
      )}
    </div>
  )
}

function KindChip({
  active,
  label,
  color,
  onClick,
}: {
  active: boolean
  label: string
  color?: string
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={
        active
          ? 'rounded-lg border border-brand-cyan/40 bg-brand-cyan/10 px-2.5 py-1 text-xs font-medium text-slate-100'
          : `rounded-lg border border-line px-2.5 py-1 text-xs transition hover:border-brand-cyan/30 hover:text-slate-100 ${color ?? 'text-ghost'}`
      }
    >
      {label}
    </button>
  )
}

function OpenQuestionsCard({ items }: { items: MemoryFact[] }) {
  const t = useT()
  return (
    <Card className="!border-brand-amber/30 !bg-brand-amber/[0.06]">
      <CardTitle>
        <span className="inline-flex items-center gap-2 text-brand-amber">
          <HelpCircle className="h-4 w-4" /> {t.journal.openQuestionsTitle(items.length)}
        </span>
      </CardTitle>
      <ul className="space-y-2">
        {items.map((f) => {
          const { date } = splitStamp(f.valid_at)
          return (
            <li key={f.id} className="flex items-baseline gap-2.5 text-sm">
              <time className="shrink-0 text-[11px] tabular-nums text-ghost">{date}</time>
              <span className="min-w-0 break-words leading-relaxed text-slate-100">{f.display || f.text}</span>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

/** One session's conclusions. Read-only unless `editable` — corrections belong on the Journal, and a
 *  Dashboard preview that could rewrite memory in passing would be the wrong affordance. */
export function SessionOutcomes({
  group,
  editable = false,
  className,
}: {
  group: OutcomeGroup
  editable?: boolean
  className?: string
}) {
  const t = useT()
  return (
    <Card className={className}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-ghost">
        <span className="min-w-0 max-w-full truncate font-mono" title={group.session}>
          {t.journal.sessionLabel} {group.session}
        </span>
        <span>{t.journal.conclusionCount(group.facts.length)}</span>
      </div>
      <ul>
        {group.facts.map((f, i) => {
          const prev = group.facts[i - 1]
          const gap = i === 0 ? '' : prev && prev.predicate === f.predicate ? 'mt-1.5' : 'mt-3'
          return <OutcomeRow key={f.id} fact={f} editable={editable} className={gap} />
        })}
      </ul>
    </Card>
  )
}

function OutcomeRow({
  fact,
  editable,
  className,
}: {
  fact: MemoryFact
  editable: boolean
  className: string
}) {
  const t = useT()
  const editFact = useEditFact()
  const deleteFact = useDeleteFact()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [mine, setMine] = useState(false)
  // Esc unmounts the textarea, which also fires blur — without this the cancel would save.
  const cancelled = useRef(false)

  const kind = fact.predicate.toLowerCase()
  const statement = fact.display || fact.text
  const isMine = mine || fact.source === 'user'

  const start = () => {
    if (!editable) return
    cancelled.current = false
    setDraft(statement)
    setEditing(true)
  }

  const save = () => {
    if (cancelled.current) return
    const next = draft.trim()
    if (!next || next === statement) {
      setEditing(false)
      return
    }
    editFact.mutate(
      { id: fact.id, patch: { object: next } },
      {
        onSuccess: (r) => {
          // The edit marks the fact user-authored — that is what tells the owner the correction will
          // survive the next distillation instead of being quietly re-extracted over.
          setMine(r.source === 'user')
          toast.success(t.journal.saved)
          setEditing(false)
        },
        onError: (e) => toast.error(String((e as Error).message)),
      },
    )
  }

  return (
    <li className={`group flex items-start gap-3 ${className}`}>
      <span className={`w-10 shrink-0 pt-0.5 text-[11px] font-medium ${KIND_COLOR[kind] ?? 'text-ghost'}`}>
        {t.journal.kind[kind as OutcomeKind] ?? fact.predicate}
      </span>
      <div className="min-w-0 flex-1">
        {editing ? (
          <AutoTextarea
            value={draft}
            onChange={setDraft}
            onSave={save}
            onCancel={() => {
              cancelled.current = true
              setEditing(false)
            }}
          />
        ) : (
          <div
            onClick={start}
            className={
              fact.status === 'live'
                ? `text-sm leading-relaxed text-slate-100 break-words ${editable ? 'cursor-text' : ''}`
                : `text-sm leading-relaxed text-ghost line-through break-words ${editable ? 'cursor-text' : ''}`
            }
            title={editable ? t.journal.editHint : undefined}
          >
            {statement}
          </div>
        )}
        {fact.why && (
          <div className="mt-0.5 break-words text-xs leading-relaxed text-ghost">↳ {fact.why}</div>
        )}
        {isMine && (
          <div className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-brand-amber">
            <Lock className="h-3 w-3" /> {t.journal.mine}
          </div>
        )}
      </div>
      {editable && !editing && (
        <button
          onClick={() => setConfirming(true)}
          className="shrink-0 rounded-lg border border-line p-1.5 text-ghost opacity-0 transition hover:border-brand-rose/50 hover:text-brand-rose focus:opacity-100 group-hover:opacity-100"
          aria-label={t.common.delete}
          title={t.common.delete}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
      <ConfirmDialog
        open={confirming}
        title={t.journal.deleteTitle}
        danger
        confirmLabel={t.common.delete}
        loading={deleteFact.isPending}
        body={
          <div className="space-y-3">
            <p>{t.journal.deleteBody}</p>
            <p className="rounded-lg border border-brand-rose/25 bg-brand-rose/10 px-3 py-2 text-brand-rose">
              {statement}
            </p>
          </div>
        }
        onClose={() => setConfirming(false)}
        onConfirm={() =>
          deleteFact.mutate(fact.id, {
            onSuccess: () => setConfirming(false),
            onError: (e) => toast.error(String((e as Error).message)),
          })
        }
      />
    </li>
  )
}

/** Same typography as the row it replaces, so editing does not make the line jump. */
function AutoTextarea({
  value,
  onChange,
  onSave,
  onCancel,
}: {
  value: string
  onChange: (v: string) => void
  onSave: () => void
  onCancel: () => void
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  return (
    <textarea
      ref={ref}
      autoFocus
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onSave}
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') onSave()
        if (e.key === 'Escape') onCancel()
      }}
      className="block w-full resize-none overflow-hidden rounded-lg border border-brand-cyan/40 bg-white/[0.04] px-2 py-1 text-sm leading-relaxed text-slate-100 outline-none"
    />
  )
}

/** The first thing a new owner sees. "No conclusions yet" would explain nothing — what they need is
 *  why their memory looked like fragments, and the two ways to get a conclusion written. */
function JournalEmpty() {
  const t = useT()
  return (
    <Card>
      <CardTitle>{t.journal.emptyTitle}</CardTitle>
      <p className="max-w-2xl text-sm leading-relaxed text-ghost">{t.journal.emptyBody}</p>
      <BackfillSessions className="mt-5" />
    </Card>
  )
}

/** Distil a session that already exists. Closing a session is what writes its conclusions, so this is
 *  the console's only way to reach history that was never closed through an agent client. */
function BackfillSessions({ className }: { className?: string }) {
  const t = useT()
  const sessions = useSessions({ limit: 8 })
  const closeSession = useCloseSession()
  const [pending, setPending] = useState<string | null>(null)

  const distil = (id: string) => {
    setPending(id)
    closeSession.mutate(id, {
      onSuccess: (r) => {
        setPending(null)
        toast.success(t.journal.backfillDone(r.outcomes))
      },
      onError: (e) => {
        setPending(null)
        toast.error(String((e as Error).message))
      },
    })
  }

  if (!sessions.data?.sessions.length) return null

  return (
    <div className={className}>
      <div className="mb-2 text-xs font-semibold tracking-wide text-slate-300">{t.journal.backfillTitle}</div>
      <ul className="space-y-2">
        {sessions.data.sessions.slice(0, 5).map((s) => (
          <li
            key={s.id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-white/[0.02] p-3"
          >
            <div className="min-w-0">
              <div className="max-w-full truncate font-mono text-xs text-brand-cyan" title={s.id}>
                {s.id}
              </div>
              {s.last_event_at_h && <div className="mt-1 text-[11px] text-ghost">{s.last_event_at_h}</div>}
            </div>
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              loading={pending === s.id}
              disabled={!!pending}
              onClick={() => distil(s.id)}
            >
              {t.journal.backfillButton}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  )
}
