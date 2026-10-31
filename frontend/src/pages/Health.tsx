import { useState } from 'react'
import { AlertTriangle, Eraser, Pencil, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, ErrorState, PageHeader, Spinner } from '../components/ui'
import { ConfirmDialog, Modal } from '../components/Modal'
import { toast } from '../components/Toast'
import { useClearSlot, useDeleteFact, useEditFact } from '../hooks/queries'
import { useT } from '../i18n'
import { useAuth } from '../store/auth'
import { useHealthQueue, useInvalidateReview, useReviewUpdate, type Finding } from '../features/review/api'
import { ReviewHistory, ReviewPager, statusLabel, useReviewText } from '../features/review/components'

export default function Health() {
  const apiKey = useAuth(s => s.apiKey)
  return <HealthContent key={apiKey} />
}

function HealthContent() {
  const t = useT()
  const tr = useReviewText()
  const [kind, setKind] = useState('all')
  const [status, setStatus] = useState('open')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [includeSensitive, setIncludeSensitive] = useState(false)
  const query = useHealthQueue({ kind, status, q, offset, limit: 30, include_sensitive: includeSensitive })
  const data = query.data
  const update = useEditFact()
  const del = useDeleteFact()
  const clearSlot = useClearSlot()
  const invalidateReview = useInvalidateReview()
  const [confirmSlot, setConfirmSlot] = useState<Finding | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Finding | null>(null)
  const [handling, setHandling] = useState<{ item: Finding; status: string } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const saveEdit = (f: Finding) => {
    if (!f.fact_id || !draft.trim() || draft === f.object) { setEditing(null); return }
    update.mutate({ id: f.fact_id, patch: { object: draft.trim() } }, {
      onSuccess: () => { toast.success(t.health.fixed); setEditing(null); invalidateReview() },
      onError: e => toast.error((e as Error).message),
    })
  }
  const kindName = (value: string) => value === 'embedder_blind' ? tr('嵌入模型覆盖不足', 'Embedding coverage')
    : t.health.kind[value as keyof typeof t.health.kind] ?? value

  return <div className="space-y-6">
    <PageHeader title={t.health.title} subtitle={t.health.subtitle} actions={
      <Button variant="ghost" onClick={() => query.refetch()} loading={query.isFetching}><RefreshCw className="h-4 w-4" />{t.health.recheck}</Button>
    } />
    <Card>
      <CardTitle><span className="inline-flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-brand-mint" />{t.health.summary}</span></CardTitle>
      {data && <div className="mb-4 flex flex-wrap gap-3 text-sm text-ghost">
        <span>{t.health.checked(data.checked.facts, data.checked.entities)}</span>
        <span>{t.health.foundN(data.total_findings)}</span>
        {Object.entries(data.by_status).map(([s, n]) => <span key={s}>{statusLabel(s, tr)} · {n}</span>)}
      </div>}
      <div className="flex flex-wrap gap-3">
        <input className="input min-w-0 flex-1" aria-label={tr('搜索体检队列', 'Search health queue')} placeholder={tr('搜索体检结果', 'Search findings')}
          value={q} onChange={e => { setQ(e.target.value); setOffset(0) }} />
        <select className="input max-w-full" value={kind} aria-label={tr('体检类型', 'Finding type')} onChange={e => { setKind(e.target.value); setOffset(0) }}>
          <option value="all">{tr('全部类型', 'All types')}</option>
          {[...new Set([...Object.keys(data?.by_kind ?? {}), ...(kind !== 'all' ? [kind] : [])])].map(k => <option key={k} value={k}>{kindName(k)} · {data?.by_kind[k] ?? 0}</option>)}
        </select>
        <select className="input max-w-full" value={status} aria-label={tr('体检状态', 'Finding status')} onChange={e => { setStatus(e.target.value); setOffset(0) }}>
          {['open', 'ignored', 'resolved', 'all'].map(s => <option key={s} value={s}>{statusLabel(s, tr)}</option>)}
        </select>
      </div>
      <label className="mt-3 flex items-center gap-2 text-xs text-ghost">
        <input type="checkbox" checked={includeSensitive} onChange={e => {
          setIncludeSensitive(e.target.checked); setOffset(0); setHandling(null); setEditing(null); setConfirmDelete(null); setConfirmSlot(null)
        }} />{tr('显示敏感内容与处理备注', 'Show sensitive content and handling notes')}
      </label>
    </Card>
    {query.isLoading && <Spinner label={t.health.loading} />}
    {query.isError && <ErrorState message={(query.error as Error).message} />}
    {data && <Card>
      <CardTitle><span className="inline-flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-brand-amber" />{tr('完整体检队列', 'Complete health queue')}</span></CardTitle>
      {!data.items.length && <p className="py-4 text-sm text-ghost">{tr('当前条件下没有体检结果。', 'No findings match these filters.')}</p>}
      <div className="divide-y divide-line">
        {data.items.map(f => <div key={f.id} className="py-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{kindName(f.kind)}</Badge><Badge>{statusLabel(f.status, tr)}</Badge>
            {f.valid_at_h && <span className="text-xs text-ghost">{f.valid_at_h}</span>}
          </div>
          <p className="mt-2 break-words text-sm">{f.text ?? f.entity ?? (f.present ? '' : tr('原来源已不再被报告', 'Source is no longer flagged'))}</p>
          <p className="mt-1 break-words text-xs text-ghost">{!f.present ? tr('修正或移除后，当前规则已不再报告此项，处理记录仍保留。', 'The current rules no longer flag this source; handling history is retained.')
            : f.kind === 'slot_overflow' ? t.health.slotOverflowWhy(f.count ?? 0, f.label ?? '', f.predicate ?? '') : f.why}</p>
          {f.source_changed && <p className="mt-1 text-xs text-brand-amber">{tr('来源已变化，旧处理状态已失效。', 'Source changed; previous handling status no longer applies.')}</p>}
          {!!f.samples?.length && <ul className="mt-2 space-y-1 text-xs text-ghost">{f.samples.map(s => <li className="break-words" key={s.fact_id}>{s.text}</li>)}</ul>}
          {editing === f.id && <div className="mt-3 flex flex-wrap gap-2">
            <input className="input min-w-0 flex-1" aria-label={t.health.fix} value={draft} onChange={e => setDraft(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveEdit(f); if (e.key === 'Escape') setEditing(null) }} />
            <Button loading={update.isPending} onClick={() => saveEdit(f)}>{t.common.save}</Button>
            <Button variant="ghost" onClick={() => setEditing(null)}>{t.common.cancel}</Button>
          </div>}
          {f.present && <div className="mt-3 flex flex-wrap gap-2">
            {f.fact_id && editing !== f.id && <>
              <Button variant="ghost" onClick={() => { setEditing(f.id); setDraft(f.object ?? '') }}><Pencil className="h-3.5 w-3.5" />{t.health.fix}</Button>
              <Button variant="ghost" onClick={() => setConfirmDelete(f)}><Trash2 className="h-3.5 w-3.5" />{t.common.delete}</Button>
            </>}
            {f.kind === 'slot_overflow' && <Button variant="ghost" className="text-brand-rose" onClick={() => setConfirmSlot(f)}><Eraser className="h-3.5 w-3.5" />{t.health.clearSlot(f.count ?? 0)}</Button>}
            {f.status === 'open' ? <>
              <Button variant="ghost" onClick={() => setHandling({ item: f, status: 'ignored' })}>{tr('忽略误报', 'Ignore false positive')}</Button>
              <Button variant="ghost" onClick={() => setHandling({ item: f, status: 'resolved' })}>{tr('标记已处理', 'Mark resolved')}</Button>
            </> : <Button variant="ghost" onClick={() => setHandling({ item: f, status: 'open' })}>{tr('重新打开', 'Reopen')}</Button>}
          </div>}
          <ReviewHistory events={f.history} />
        </div>)}
      </div>
      <ReviewPager page={data.page} onOffset={setOffset} />
    </Card>}
    {handling && <HandlingDialog item={handling.item} status={handling.status} includeSensitive={includeSensitive} onClose={() => setHandling(null)} />}
    <ConfirmDialog open={!!confirmDelete} title={t.common.delete} danger confirmLabel={t.common.delete} loading={del.isPending}
      body={<div className="space-y-2"><p>{t.health.confirmDelete}</p><p className="break-words text-xs text-brand-rose">{confirmDelete?.text}</p></div>}
      onClose={() => setConfirmDelete(null)} onConfirm={() => {
        if (confirmDelete?.fact_id) del.mutate(confirmDelete.fact_id, {
          onSuccess: () => { toast.success(t.health.removed); setConfirmDelete(null); invalidateReview() }, onError: e => toast.error((e as Error).message),
        })
      }} />
    <ConfirmDialog open={!!confirmSlot} title={t.health.clearSlotTitle} danger confirmLabel={t.common.delete} loading={clearSlot.isPending}
      body={<div className="space-y-3"><p>{confirmSlot && t.health.clearSlotBody(confirmSlot.count ?? 0, confirmSlot.predicate ?? '')}</p>
        <ul className="space-y-1 text-xs text-brand-rose">{confirmSlot?.samples?.map(s => <li className="break-words" key={s.fact_id}>{s.text}</li>)}</ul><p className="text-xs text-ghost">{t.health.clearSlotIrreversible}</p></div>}
      onClose={() => setConfirmSlot(null)} onConfirm={() => {
        if (!confirmSlot?.subject || !confirmSlot.predicate || !confirmSlot.count) return
        clearSlot.mutate({ subject: confirmSlot.subject, predicate: confirmSlot.predicate, count: confirmSlot.count }, {
          onSuccess: res => { if (res.ok) toast.success(t.health.clearSlotDone(res.deleted)); else toast.error(t.health.clearSlotStale); setConfirmSlot(null); invalidateReview() },
          onError: e => toast.error((e as Error).message),
        })
      }} />
  </div>
}

function HandlingDialog({ item, status, includeSensitive, onClose }: { item: Finding; status: string; includeSensitive: boolean; onClose: () => void }) {
  const tr = useReviewText()
  const t = useT()
  const [note, setNote] = useState('')
  const update = useReviewUpdate('health')
  return <Modal open title={status === 'ignored' ? tr('忽略误报', 'Ignore false positive') : statusLabel(status, tr)} onClose={onClose}
    footer={<><Button variant="ghost" onClick={onClose}>{t.common.cancel}</Button><Button loading={update.isPending} onClick={() => update.mutate({
      id: item.id, status, expected_version: item.version, include_sensitive: includeSensitive, note,
    }, { onSuccess: () => { toast.success(tr('处理状态已保存', 'Handling status saved')); onClose() }, onError: e => toast.error((e as Error).message) })}>{t.common.confirm}</Button></>}>
    <p className="mb-3 break-words text-sm">{item.text ?? item.entity ?? item.kind}</p>
    <p className="mb-3 text-xs text-ghost">{tr('此操作只更新体检处理状态；来源变化后会重新待处理。', 'This updates the review status. A source change reopens the finding.')}</p>
    <textarea className="input w-full" maxLength={2000} value={note} onChange={e => setNote(e.target.value)} aria-label={tr('处理备注', 'Handling note')} placeholder={tr('处理备注（可选）', 'Optional handling note')} />
  </Modal>
}
