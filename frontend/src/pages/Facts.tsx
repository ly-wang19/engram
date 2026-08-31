import { useEffect, useState } from 'react'
import { FileSearch, GitMerge, Lock, Pencil, Plus, Search, ShieldAlert, Trash2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, Field, PageHeader, Spinner } from '../components/ui'
import { ConfirmDialog, Modal } from '../components/Modal'
import { toast } from '../components/Toast'
import {
  useAddFact,
  useConflicts,
  useDeleteFact,
  useEditFact,
  useMemories,
  useSourceEpisodes,
  useResolveConflict,
} from '../hooks/queries'
import { useT } from '../i18n'
import { splitStamp } from '../lib/format'
import type { MemoryEpisode, MemoryFact } from '../types'

export default function Facts() {
  const addFact = useAddFact()
  const editFact = useEditFact()
  const deleteFact = useDeleteFact()
  const t = useT()

  const [q, setQ] = useState('')
  const [showOld, setShowOld] = useState(true)
  const [hideSensitive, setHideSensitive] = useState(false)
  const [limit, setLimit] = useState(40)
  const [editing, setEditing] = useState<MemoryFact | null>(null)
  const [sourceOf, setSourceOf] = useState<MemoryFact | null>(null)
  const [adding, setAdding] = useState(false)
  const [confirmFact, setConfirmFact] = useState<MemoryFact | null>(null)

  useEffect(() => {
    setLimit(40)
  }, [q, showOld, hideSensitive])

  const { data, isLoading, isError, error } = useMemories({
    facts_limit: limit,
    episodes_limit: 0,
    q,
    status: showOld ? undefined : 'live',
    include_sensitive: !hideSensitive,
  })
  const facts = data?.facts ?? []
  const sourceQuery = useSourceEpisodes(!!sourceOf)
  const factsPage = data?.facts_page
  const hasMore = factsPage?.has_more ?? false

  if (isLoading) return <Spinner label={t.facts.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.facts.title}
        subtitle={t.facts.subtitle}
        actions={
          <Button onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" /> {t.facts.addManual}
          </Button>
        }
      />

      <ConflictsCard />


      <Card className="!p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3">
            <Search className="h-4 w-4 text-ghost" />
            <input
              aria-label={t.facts.searchPlaceholder}
              className="w-full bg-transparent py-2 text-sm outline-none placeholder:text-ghost/70"
              placeholder={t.facts.searchPlaceholder}
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input type="checkbox" checked={showOld} onChange={(e) => setShowOld(e.target.checked)} className="accent-brand-cyan" />
            {t.facts.showOld}
          </label>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input type="checkbox" checked={hideSensitive} onChange={(e) => setHideSensitive(e.target.checked)} className="accent-brand-rose" />
            {t.facts.hideSensitive}
          </label>
        </div>
      </Card>

      <Card>
        {facts.length ? (
          <>
          <ul className="divide-y divide-line">
            {facts.map((f) => {
              const { date, time } = splitStamp(f.valid_at)
              return (
              <li key={f.id} className="group flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:gap-3">
                <div className="flex shrink-0 items-center gap-2 sm:w-[148px]">
                  <Badge tone={f.status === 'live' ? 'live' : 'old'}>{f.status === 'live' ? t.facts.statusLive : t.facts.statusOld}</Badge>
                  <time className="shrink-0 tabular-nums leading-tight text-brand-cyan">
                    <span className="block text-[11px]">{date}</span>
                    {time && <span className="block text-[10px] text-brand-cyan/55">{time}</span>}
                  </time>
                </div>
                <div className="min-w-0 flex-1 break-words">
                  <div className={f.status === 'live' ? 'text-sm text-slate-100' : 'text-sm text-ghost line-through'}>
                    {f.display || f.text}
                  </div>
                  <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-2 text-[11px] text-ghost">
                    <span className="inline-flex min-w-0 max-w-full whitespace-normal break-all rounded bg-white/5 px-1.5 py-px font-mono">{f.predicate}</span>
                    {f.category && f.category !== '其他' && (
                      <span className="rounded bg-brand-violet/15 px-1.5 py-px text-brand-violet">{f.category}</span>
                    )}
                    {f.sensitive && (
                      <span className="inline-flex items-center gap-1 rounded bg-brand-rose/15 px-1.5 py-px text-brand-rose">
                        <ShieldAlert className="h-3 w-3" /> {t.facts.sensitive}
                      </span>
                    )}
                    {f.source === 'user' && (
                      <span className="inline-flex items-center gap-1 text-brand-amber">
                        <Lock className="h-3 w-3" /> {t.common.mine}
                      </span>
                    )}
                    {f.invalid_at && <span>{t.facts.invalidAt(f.invalid_at)}</span>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 sm:opacity-80 sm:transition sm:group-hover:opacity-100">
                  {!!f.provenance?.length && (
                    <button
                      onClick={() => setSourceOf(f)}
                      className="rounded-lg border border-line p-2 text-ghost transition hover:border-brand-violet/50 hover:text-brand-violet"
                      aria-label={t.facts.viewSource}
                      title={t.facts.viewSource}
                    >
                      <FileSearch className="h-4 w-4" />
                    </button>
                  )}
                  <button
                    onClick={() => setEditing(f)}
                    className="rounded-lg border border-line p-2 text-ghost transition hover:border-brand-cyan/50 hover:text-brand-cyan"
                    aria-label={t.common.edit}
                    title={t.common.edit}
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setConfirmFact(f)}
                    className="rounded-lg border border-line p-2 text-ghost transition hover:border-brand-rose/50 hover:text-brand-rose"
                    aria-label={t.common.delete}
                    title={t.common.delete}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </li>
              )
            })}
          </ul>
          {hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="ghost" onClick={() => setLimit((n) => n + 40)}>
                {t.facts.loadMore}
              </Button>
            </div>
          )}
          </>
        ) : (
          <EmptyState title={t.facts.emptyTitle} hint={t.facts.emptyHint} />
        )}
      </Card>

      {/* provenance: a fact you cannot trace is a fact you cannot trust */}
      <Modal open={!!sourceOf} title={t.facts.sourceTitle} onClose={() => setSourceOf(null)}>
        {sourceOf && (
          <div className="space-y-4">
            <div className="rounded-xl border border-line bg-white/[0.035] p-3">
              <p className="text-sm text-slate-100">{sourceOf.display || sourceOf.text}</p>
              <p className="mt-1 text-xs text-ghost">
                {sourceOf.source === 'user' ? t.facts.sourceUser : t.facts.sourceExtracted}
              </p>
            </div>
            {sourceQuery.isLoading ? (
              <Spinner label={t.facts.sourceLoading} />
            ) : (
              (() => {
                const eps = (sourceQuery.data?.episodes ?? []).filter((e: MemoryEpisode) =>
                  sourceOf.provenance?.includes(e.id),
                )
                if (!eps.length) return <p className="text-sm text-ghost">{t.facts.sourceMissing}</p>
                return (
                  <div className="space-y-3">
                    <p className="text-xs text-ghost">{t.facts.sourceFrom(eps.length)}</p>
                    {eps.map((e: MemoryEpisode) => (
                      <div key={e.id} className="rounded-xl border border-line bg-ink-900/50 p-3">
                        <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-ghost">
                          <span className="text-brand-cyan">{e.date}</span>
                          <span className="rounded bg-white/5 px-1.5 py-px font-mono">{e.session}</span>
                        </div>
                        <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-300">
                          {e.content}
                        </p>
                      </div>
                    ))}
                  </div>
                )
              })()
            )}
          </div>
        )}
      </Modal>

      {/* edit */}
      <FactModal
        open={!!editing}
        title={t.facts.editTitle}
        initial={editing ?? undefined}
        loading={editFact.isPending}
        onClose={() => setEditing(null)}
        onSubmit={(vals) => {
          if (!editing) return
          editFact.mutate(
            { id: editing.id, patch: vals },
            {
              onSuccess: () => {
                toast.success(t.facts.updated)
                setEditing(null)
              },
              onError: (e) => toast.error(String((e as Error).message)),
            },
          )
        }}
      />

      {/* add */}
      <FactModal
        open={adding}
        title={t.facts.addTitle}
        loading={addFact.isPending}
        onClose={() => setAdding(false)}
        onSubmit={(vals) => {
          addFact.mutate(
            {
              subject: vals.subject || 'user',
              predicate: vals.predicate!,
              object: vals.object!,
              sensitive: vals.sensitive,
              category: vals.category,
            },
            {
              onSuccess: () => {
                toast.success(t.facts.added)
                setAdding(false)
              },
              onError: (e) => toast.error(String((e as Error).message)),
            },
          )
        }}
      />

      {/* delete */}
      <ConfirmDialog
        open={!!confirmFact}
        title={t.facts.deleteTitle}
        danger
        confirmLabel={t.common.delete}
        loading={deleteFact.isPending}
        body={
          <div className="space-y-3">
            <p>{t.facts.deleteBody}</p>
            {confirmFact && (
              <p className="rounded-lg border border-brand-rose/25 bg-brand-rose/10 px-3 py-2 text-brand-rose">
                {t.facts.deleteTarget(confirmFact.display || confirmFact.text)}
              </p>
            )}
          </div>
        }
        onClose={() => setConfirmFact(null)}
        onConfirm={() => {
          if (!confirmFact) return
          deleteFact.mutate(confirmFact.id, {
            onSuccess: () => {
              toast.success(t.facts.deleted)
              setConfirmFact(null)
            },
            onError: (e) => toast.error(String((e as Error).message)),
          })
        }}
      />
    </div>
  )
}

function ConflictsCard() {
  const { data } = useConflicts()
  const resolve = useResolveConflict()
  const t = useT()
  const items = data?.conflicts ?? []
  if (!items.length) return null

  const act = (id: string, keep: 'newer' | 'older' | 'both', label: string) =>
    resolve.mutate(
      { id, keep },
      { onSuccess: () => toast.success(label), onError: (e) => toast.error(String((e as Error).message)) },
    )

  return (
    <Card className="!border-brand-amber/30 !bg-brand-amber/[0.06]">
      <CardTitle hint={t.facts.conflictsHint}>
        <span className="inline-flex items-center gap-2 text-brand-amber">
          <GitMerge className="h-4 w-4" /> {t.facts.conflictsTitle(items.length)}
        </span>
      </CardTitle>
      <ul className="space-y-3">
        {items.map((c) => (
          <li key={c.id} className="rounded-xl border border-line bg-white/[0.02] p-3">
            <div className="grid gap-1.5 sm:grid-cols-2">
              <div className="rounded-lg bg-brand-mint/10 px-2.5 py-1.5 text-sm">
                <span className="text-[10px] text-brand-mint">{t.facts.conflictNew}</span>
                <div className="text-slate-100">{c.newer_text}</div>
              </div>
              <div className="rounded-lg bg-white/5 px-2.5 py-1.5 text-sm">
                <span className="text-[10px] text-ghost">{t.facts.conflictOld}</span>
                <div className="text-ghost line-through">{c.older_text}</div>
              </div>
            </div>
            <div className="mt-2.5 flex flex-wrap gap-2">
              <Button onClick={() => act(c.id, 'newer', t.facts.keepNewerToast)}>{t.facts.keepNewer}</Button>
              <Button variant="ghost" onClick={() => act(c.id, 'older', t.facts.keepOlderToast)}>
                {t.facts.keepOlder}
              </Button>
              <Button variant="ghost" onClick={() => act(c.id, 'both', t.facts.keepBothToast)}>
                {t.facts.keepBoth}
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function FactModal({
  open,
  title,
  initial,
  loading,
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  initial?: MemoryFact
  loading: boolean
  onClose: () => void
  onSubmit: (vals: { subject?: string; predicate?: string; object?: string; sensitive?: boolean; category?: string }) => void
}) {
  const t = useT()
  const [subject, setSubject] = useState('user')
  const [predicate, setPredicate] = useState('')
  const [object, setObject] = useState('')
  const [sensitive, setSensitive] = useState(false)

  // Sync inputs whenever the dialog opens with a (possibly new) fact.
  const [seen, setSeen] = useState<string | null>(null)
  const sig = open ? (initial?.id ?? 'new') : null
  if (sig !== seen) {
    setSeen(sig)
    setSubject(initial?.subject ?? 'user')
    setPredicate(initial?.predicate ?? '')
    setObject(initial?.object ?? '')
    setSensitive(initial?.sensitive ?? false)
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button loading={loading} disabled={!predicate.trim() || !object.trim()} onClick={() => onSubmit({ subject, predicate, object, sensitive })}>
            {t.common.save}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label={t.facts.subjectLabel}>
          <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="user" />
        </Field>
        <Field label={t.facts.predicateLabel}>
          <input className="input font-mono" value={predicate} onChange={(e) => setPredicate(e.target.value)} placeholder="works_at" />
        </Field>
        <Field label={t.facts.objectLabel}>
          <input className="input" value={object} onChange={(e) => setObject(e.target.value)} placeholder={t.facts.objectPlaceholder} />
        </Field>
        <label className="flex select-none items-center gap-2 text-sm text-slate-200">
          <input type="checkbox" checked={sensitive} onChange={(e) => setSensitive(e.target.checked)} className="accent-brand-rose" />
          <ShieldAlert className="h-4 w-4 text-brand-rose" /> {t.facts.markSensitive}
        </label>
        <p className="text-xs text-ghost">{t.facts.modalNote}</p>
      </div>
    </Modal>
  )
}
