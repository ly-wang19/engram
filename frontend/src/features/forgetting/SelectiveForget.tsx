import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, ErrorState, Spinner } from '../../components/ui'
import { ConfirmDialog } from '../../components/Modal'
import { useLang } from '../../i18n'
import { useAuth } from '../../store/auth'
import { forgettingApi, type ForgetPreview, type ForgetScope } from './api'

export function SelectiveForget() {
  const key = useAuth(s => s.apiKey)
  return <Panel key={key} />
}
function Panel() {
  const zh = useLang(s => s.lang) === 'zh'
  const key = useAuth(s => s.apiKey)
  const qc = useQueryClient()
  const [sessions, setSessions] = useState('')
  const [source, setSource] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sensitive, setSensitive] = useState(false)
  const [preview, setPreview] = useState<ForgetPreview | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [phrase, setPhrase] = useState('')
  const [done, setDone] = useState(false)
  const protectedNamespace = key === '1' || !key || key === 'anonymous'
  const scope: ForgetScope = { session_ids: sessions.split('\n').map(s => s.trim()).filter(Boolean),
    source: source.trim() || null, time_from: from ? new Date(from).getTime() / 1000 : null,
    time_to: to ? new Date(to).getTime() / 1000 : null }
  const pendingPreview = useMutation({ mutationKey: ['forget-preview', key, scope, sensitive],
    mutationFn: () => forgettingApi.preview(scope, sensitive), onSuccess: setPreview })
  const apply = useMutation({ mutationKey: ['forget-confirm', key], mutationFn: () => forgettingApi.confirm(preview!),
    onSuccess: () => { setPreview(null); setConfirmOpen(false); setPhrase(''); setDone(true); qc.clear() },
    onError: () => { setConfirmOpen(false); setPhrase(''); setPreview(null) },
  })
  const changed = () => { setPreview(null); setDone(false); pendingPreview.reset(); apply.reset() }
  const valid = !protectedNamespace && (!!sessions.trim() || !!source.trim() || (!!from && !!to)) && (!!from === !!to)
  const busy = pendingPreview.isPending || apply.isPending
  const labels: Record<string, string> = zh ? {
    episodes: '原文', facts: '派生事实', facts_hot: '热层事实', facts_cold: '冷层事实', shared_source_facts: '共享来源事实',
    untracked_derived_facts: '来源不完整的派生事实', summaries: '摘要', working: '工作记忆', relations: '图关系', conflicts: '冲突记录', review_records: '问题及体检处理记录', connection_events: '来源同步事件',
  } : { episodes: 'Original records', facts: 'Facts', facts_hot: 'Hot facts', facts_cold: 'Cold facts', shared_source_facts: 'Shared-source facts',
    untracked_derived_facts: 'Untraceable derived facts', summaries: 'Summaries', working: 'Working memory', relations: 'Graph relations', conflicts: 'Conflicts', review_records: 'Question and health review records', connection_events: 'Source events' }
  return <Card>
    <h2 className="font-semibold text-brand-rose">{zh ? '选择性遗忘' : 'Selective forgetting'}</h2>
    <p className="mt-2 text-sm text-ghost">{zh ? '按会话、来源或时间选择范围。填写多项时取交集，先查看影响，再确认删除。时间范围包含开始、不包含结束。' : 'Select sessions, a source, or a time range. Multiple filters intersect. Preview the impact before confirming deletion. Time includes the start and excludes the end.'}</p>
    <form className="mt-4 space-y-3" onSubmit={e => { e.preventDefault(); setDone(false); setPreview(null); apply.reset(); pendingPreview.mutate() }}>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="block min-w-0 text-sm">{zh ? '会话 ID（每行一个，可选）' : 'Session IDs (one per line, optional)'}
          <textarea disabled={busy} className="input mt-1 min-h-20 w-full" value={sessions} onChange={e => { setSessions(e.target.value); changed() }} /></label>
        <label className="block min-w-0 text-sm">{zh ? '来源名称（精确匹配，可选）' : 'Source name (exact match, optional)'}
          <input disabled={busy} className="input mt-1 w-full" value={source} placeholder="agent_session / document / records" onChange={e => { setSource(e.target.value); changed() }} /></label>
        <label className="block min-w-0 text-sm">{zh ? '开始时间（可选）' : 'Start time (optional)'}<input disabled={busy} type="datetime-local" className="input mt-1 w-full min-w-0 max-w-full" value={from} onChange={e => { setFrom(e.target.value); changed() }} /></label>
        <label className="block min-w-0 text-sm">{zh ? '结束时间（可选）' : 'End time (optional)'}<input disabled={busy} type="datetime-local" className="input mt-1 w-full min-w-0 max-w-full" value={to} onChange={e => { setTo(e.target.value); changed() }} /></label>
      </div>
      <label className="flex items-center gap-2 text-sm"><input disabled={busy} type="checkbox" checked={sensitive} onChange={e => { setSensitive(e.target.checked); changed() }} />{zh ? '预览中显示原文和敏感事实' : 'Show raw text and sensitive facts in preview'}</label>
      <Button type="submit" disabled={!valid || busy} loading={pendingPreview.isPending}>{zh ? '预览遗忘范围' : 'Preview forgetting'}</Button>
      {protectedNamespace && <p className="text-sm text-brand-amber">{zh ? '公共演示和匿名空间不能删除。' : 'Public demo and anonymous spaces cannot be erased.'}</p>}
    </form>
    {pendingPreview.isError && <div className="mt-3"><ErrorState message={pendingPreview.error.message} /></div>}
    {apply.isError && <div className="mt-3"><ErrorState message={apply.error.message} /><p className="mt-2 text-sm text-ghost">{zh ? '未确认完成。请重新预览核对状态，不要重复使用旧令牌。' : 'Completion was not confirmed. Preview again to check the state; do not reuse the old token.'}</p></div>}
    {done && <p role="status" className="mt-3 text-sm text-brand-mint">{zh ? '已按确认的范围完成遗忘；页面缓存已清除。' : 'Forgetting completed for the confirmed scope. Page caches were cleared.'}</p>}
    {preview && <section className="mt-5 space-y-4 border-t border-line pt-4">
      <h3 className="font-semibold">{zh ? '本次影响范围' : 'Impact preview'}</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">{Object.entries(preview.counts).map(([kind, count]) => <div key={kind} className="rounded-lg bg-white/5 p-2 text-sm"><span className="text-ghost">{labels[kind] ?? kind}</span><strong className="ml-2">{count}</strong></div>)}</div>
      <p className="text-sm text-brand-amber">{zh ? '依赖任一被删原文的事实会整条删除，即使还引用其他来源。为清除无法完整追踪的派生内容，本空间所有摘要、画像缓存和问题/体检处理记录会重置；来源不完整的自动事实也会删除。其他原文保留，摘要可重新生成。相关会话的提炼任务会取消。' : 'Any fact depending on an erased source is deleted in full, even if it also cites other sources. All summaries, profile caches, and question/health review state in this space are reset because their full lineage is unavailable. Untraceable automatic facts are also removed. Other raw records remain and can be summarized again. Distillation jobs for affected sessions are cancelled.'}</p>
      <PreviewList key={`${preview.token}:episodes`} preview={preview} kind="episodes" />
      <PreviewList key={`${preview.token}:facts`} preview={preview} kind="facts" />
      <p className="text-xs text-ghost">{zh ? '预览有效至' : 'Preview expires at'} {new Date(preview.expires_at * 1000).toLocaleString()}. {zh ? '期间范围或内容变化将拒绝执行。' : 'Scope or content changes will reject confirmation.'}</p>
      <Button variant="danger" disabled={busy} onClick={() => { setPhrase(''); setConfirmOpen(true) }}>{zh ? '确认这个范围…' : 'Confirm this scope…'}</Button>
    </section>}
    <ConfirmDialog open={confirmOpen} danger title={zh ? '永久遗忘已预览的数据？' : 'Permanently forget the previewed data?'}
      loading={apply.isPending} confirmDisabled={phrase !== 'FORGET' || !preview}
      confirmLabel={zh ? '永久遗忘' : 'Permanently forget'}
      body={<div className="space-y-3"><p>{zh ? '此操作不可撤销。请输入 FORGET 确认已核对原文及派生数据的影响。' : 'This cannot be undone. Type FORGET to confirm that you reviewed the original records and derived data.'}</p>
        <input className="input w-full" aria-label={zh ? '输入 FORGET 确认' : 'Type FORGET to confirm'} value={phrase} onChange={e => setPhrase(e.target.value)} autoComplete="off" /></div>}
      onClose={() => { if (!apply.isPending) setConfirmOpen(false) }} onConfirm={() => apply.mutate()} />
  </Card>
}

function PreviewList({ preview, kind }: { preview: ForgetPreview; kind: 'episodes' | 'facts' }) {
  const key = useAuth(s => s.apiKey)
  const zh = useLang(s => s.lang) === 'zh'
  const [offset, setOffset] = useState(0)
  const page = useQuery({ queryKey: ['forget-preview-page', key, preview.token, kind, preview.include_sensitive, offset],
    queryFn: () => forgettingApi.page(preview.token, kind, preview.include_sensitive, offset),
    initialData: offset === 0 ? preview[kind] : undefined, staleTime: 0, retry: false })
  return <div className="space-y-2">
    <h4 className="text-sm font-semibold">{kind === 'episodes' ? (zh ? '原文预览' : 'Original records') : (zh ? '事实预览' : 'Facts')}</h4>
    {page.isPending && <Spinner />}{page.isError && <ErrorState message={page.error.message} />}
    {!page.isError && page.data?.items.map(item => <article key={item.id} className="min-w-0 rounded-lg border border-line p-3">
      <p className="break-all text-xs text-ghost">{item.session_id ?? item.id} · {item.source}</p>
      <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-sm">{item.redacted ? (zh ? '内容已脱敏；开启预览敏感信息后重新预览。' : 'Content is redacted. Enable sensitive preview and preview again.') : item.text}</p>
    </article>)}
    {!page.isError && page.data?.total === 0 && <p className="text-sm text-ghost">{zh ? '此类没有待删除内容。' : 'No records of this kind will be deleted.'}</p>}
    {page.data && page.data.total > 20 && <div className="flex items-center gap-2 text-xs">
      <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>{zh ? '上一页' : 'Previous'}</Button>
      <span>{offset + 1}–{Math.min(offset + 20, page.data.total)} / {page.data.total}</span>
      <Button variant="ghost" disabled={page.data.next_offset === null} onClick={() => setOffset(page.data!.next_offset!)}>{zh ? '下一页' : 'Next'}</Button>
    </div>}
  </div>
}
