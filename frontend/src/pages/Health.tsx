import { useState } from 'react'
import { AlertTriangle, Check, Eraser, Pencil, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { ConfirmDialog } from '../components/Modal'
import { toast } from '../components/Toast'
import { useAudit, useClearSlot, useDeleteFact, useEditFact } from '../hooks/queries'
import { useT } from '../i18n'
import type { AuditFinding } from '../types'

/** Memory health: what's stored that a person would want to fix, and a way to fix it here.
 *
 * A memory store earns trust by being checkable, not by being large. Extraction on real conversations
 * leaves junk — raw tokens used as values, objects that only repeat the predicate, sentence-length
 * claims never reduced to a fact — and none of it is visible unless you read every fact by hand. Each
 * row carries WHY it was flagged and fixes in place, because a finding you can't act on is just noise.
 */
export default function Health() {
  const t = useT()
  const { data, isLoading, isError, error, refetch, isFetching } = useAudit(60)
  const update = useEditFact()
  const del = useDeleteFact()
  const clearSlot = useClearSlot()
  const [confirmSlot, setConfirmSlot] = useState<AuditFinding | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [done, setDone] = useState<Set<string>>(new Set())

  if (isLoading) return <Spinner label={t.health.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const markDone = (id: string) => setDone((prev) => new Set(prev).add(id))

  const saveEdit = (f: AuditFinding) => {
    if (!f.fact_id || !draft.trim() || draft === f.object) {
      setEditing(null)
      return
    }
    update.mutate(
      { id: f.fact_id, patch: { object: draft.trim() } },
      {
        onSuccess: () => {
          // The edit also marks the fact user-authored, so auto-extraction can't quietly revert it.
          toast.success(t.health.fixed)
          markDone(f.fact_id!)
          setEditing(null)
        },
        onError: (e: unknown) => toast.error(String((e as Error).message)),
      },
    )
  }

  const remove = (f: AuditFinding) => {
    if (!f.fact_id) return
    if (!window.confirm(t.health.confirmDelete)) return
    del.mutate(f.fact_id, {
      onSuccess: () => {
        toast.success(t.health.removed)
        markDone(f.fact_id!)
      },
      onError: (e: unknown) => toast.error(String((e as Error).message)),
    })
  }

  const KIND_TONE: Record<string, 'user' | 'violet' | 'cyan' | undefined> = {
    machine_token: 'user',     // amber-ish: a value that needs rewriting
    empty_value: 'violet',
    unreduced_claim: 'cyan',
    orphan_entity: undefined,
    slot_overflow: 'user',     // a whole single-value slot drowning in extraction fragments
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.health.title}
        subtitle={t.health.subtitle}
        actions={
          <Button variant="ghost" onClick={() => refetch()} loading={isFetching}>
            <RefreshCw className="h-4 w-4" /> {t.health.recheck}
          </Button>
        }
      />

      <Card>
        <CardTitle hint={t.health.scopeHint}>
          <span className="inline-flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-brand-mint" /> {t.health.summary}
          </span>
        </CardTitle>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <span className="text-ghost">
            {t.health.checked(data.checked.facts, data.checked.entities)}
          </span>
          <span className={data.total_findings ? 'font-semibold text-brand-amber' : 'font-semibold text-brand-mint'}>
            {data.total_findings ? t.health.foundN(data.total_findings) : t.health.allClear}
          </span>
        </div>
        {!!data.total_findings && (
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(data.by_kind).map(([kind, n]) => (
              <Badge key={kind} tone={KIND_TONE[kind]}>
                {String(t.health.kind[kind as keyof typeof t.health.kind] ?? kind)} · {String(n)}
              </Badge>
            ))}
          </div>
        )}
      </Card>

      {!data.findings.length ? (
        <EmptyState title={t.health.allClear} hint={t.health.allClearHint} />
      ) : (
        <Card>
          <CardTitle hint={t.health.listHint}>
            <span className="inline-flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-brand-amber" /> {t.health.worthLook}
            </span>
          </CardTitle>
          <div className="divide-y divide-line">
            {data.findings.map((f: AuditFinding, i: number) => {
              const id = f.fact_id ?? `${f.kind}-${f.entity}-${i}`
              const isDone = f.fact_id ? done.has(f.fact_id) : false
              return (
                <div key={id} className={isDone ? 'py-3 opacity-40' : 'py-3'}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={KIND_TONE[f.kind]}>
                          {t.health.kind[f.kind as keyof typeof t.health.kind] ?? f.kind}
                        </Badge>
                        {f.valid_at_h && <span className="text-xs text-ghost">{f.valid_at_h}</span>}
                        {isDone && (
                          <span className="inline-flex items-center gap-1 text-xs text-brand-mint">
                            <Check className="h-3.5 w-3.5" /> {t.health.handled}
                          </span>
                        )}
                      </div>
                      {(f.text ?? f.entity) && (
                        <p className="mt-1 break-words text-sm text-slate-100">{f.text ?? f.entity}</p>
                      )}
                      {/* why it was flagged, then what to do — a finding you can't act on is noise */}
                      {/* The backend writes why/action in one language for every rule. Fine for a
                          hint, but slot_overflow carries a one-click irreversible delete, so its
                          sentence is composed here from the structured fields instead. */}
                      <p className="mt-1 text-xs leading-relaxed text-ghost">
                        {f.kind === 'slot_overflow' && f.count
                          ? t.health.slotOverflowWhy(f.count, f.label || '', f.predicate || '')
                          : f.why}{' '}
                        → <span className="text-slate-300">
                          {f.kind === 'slot_overflow' ? t.health.slotOverflowAction : f.action}
                        </span>
                      </p>
                      {/* group findings (slot_overflow) carry no fact_id — samples are what makes
                          "N facts in one slot" concrete enough to act on */}
                      {!!f.samples?.length && (
                        <ul className="mt-1.5 space-y-0.5">
                          {f.samples.slice(0, 5).map((s) => (
                            <li key={s.fact_id} className="break-words text-[11px] leading-relaxed text-ghost/80">
                              {s.text}
                            </li>
                          ))}
                        </ul>
                      )}
                      {editing === f.fact_id && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <input
                            className="input max-w-md flex-1"
                            value={draft}
                            autoFocus
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveEdit(f)
                              if (e.key === 'Escape') setEditing(null)
                            }}
                          />
                          <Button onClick={() => saveEdit(f)} loading={update.isPending}>
                            {t.common.save}
                          </Button>
                          <Button variant="ghost" onClick={() => setEditing(null)}>
                            {t.common.cancel}
                          </Button>
                        </div>
                      )}
                    </div>
                    {/* A group finding has no fact_id, so the per-row buttons above skip it — and its
                        own action text ("clear this slot") had nothing behind it. 84 rows behind 84
                        confirm dialogs is not a path a person walks. */}
                    {!f.fact_id && f.kind === 'slot_overflow' && !!f.count && (
                      <Button
                        variant="ghost"
                        className="shrink-0 text-brand-rose"
                        onClick={() => setConfirmSlot(f)}
                      >
                        <Eraser className="mr-1.5 h-4 w-4" />
                        {t.health.clearSlot(f.count)}
                      </Button>
                    )}
                    {f.fact_id && !isDone && editing !== f.fact_id && (
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          variant="ghost"
                          aria-label={t.health.fix}
                          onClick={() => {
                            setEditing(f.fact_id!)
                            setDraft(f.object ?? '')
                          }}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" aria-label={t.common.delete} onClick={() => remove(f)}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
          {data.truncated && <p className="mt-4 text-xs text-ghost">{t.health.truncated}</p>}
        </Card>
      )}

      <ConfirmDialog
        open={!!confirmSlot}
        title={t.health.clearSlotTitle}
        danger
        confirmLabel={t.common.delete}
        loading={clearSlot.isPending}
        body={
          <div className="space-y-3">
            <p>{confirmSlot && t.health.clearSlotBody(confirmSlot.count ?? 0, confirmSlot.predicate ?? '')}</p>
            {!!confirmSlot?.samples?.length && (
              <ul className="space-y-1 rounded-lg border border-brand-rose/25 bg-brand-rose/10 px-3 py-2">
                {confirmSlot.samples.slice(0, 5).map((sample) => (
                  <li key={sample.fact_id} className="break-words text-xs text-brand-rose">
                    {sample.text}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-xs text-ghost">{t.health.clearSlotIrreversible}</p>
          </div>
        }
        onClose={() => setConfirmSlot(null)}
        onConfirm={() => {
          if (!confirmSlot?.subject || !confirmSlot.predicate || !confirmSlot.count) return
          clearSlot.mutate(
            { subject: confirmSlot.subject, predicate: confirmSlot.predicate, count: confirmSlot.count },
            {
              onSuccess: (res) => {
                // The server refuses instead of erasing more than the owner approved; say which it was.
                if (res.ok) toast.success(t.health.clearSlotDone(res.deleted))
                else toast.error(t.health.clearSlotStale)
                setConfirmSlot(null)
              },
              onError: (e) => toast.error((e as Error).message),
            },
          )
        }}
      />
    </div>
  )
}
