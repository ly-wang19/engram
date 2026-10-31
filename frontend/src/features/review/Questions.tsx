import { useState } from 'react'
import { HelpCircle } from 'lucide-react'
import { Button, Card, CardTitle, ErrorState, Spinner } from '../../components/ui'
import { Modal } from '../../components/Modal'
import { toast } from '../../components/Toast'
import { useT } from '../../i18n'
import { useOutcomeReview, useQuestions, useReviewUpdate, type Question } from './api'
import { ReviewHistory, ReviewPager, statusLabel, useReviewText } from './components'

export function Questions({ includeSensitive }: { includeSensitive: boolean }) {
  const tr = useReviewText()
  const [status, setStatus] = useState('open')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Question | null>(null)
  const query = useQuestions({ status, q, offset, limit: 10, include_sensitive: includeSensitive })
  return <Card className="!border-brand-amber/30 !bg-brand-amber/[0.04]">
    <CardTitle><span className="inline-flex items-center gap-2 text-brand-amber"><HelpCircle className="h-4 w-4" />{tr('未决问题跟踪', 'Question tracking')}</span></CardTitle>
    <div className="mb-4 flex flex-wrap gap-2">
      <input className="input min-w-0 flex-1" value={q} aria-label={tr('搜索未决问题', 'Search questions')}
        placeholder={tr('搜索问题或会话', 'Search questions or sessions')} onChange={e => { setQ(e.target.value); setOffset(0) }} />
      <select className="input max-w-full" value={status} aria-label={tr('问题状态', 'Question status')} onChange={e => { setStatus(e.target.value); setOffset(0) }}>
        {['open', 'resolved', 'all'].map(s => <option key={s} value={s}>{statusLabel(s, tr)}</option>)}
      </select>
    </div>
    {query.isLoading && <Spinner />}
    {query.isError && <ErrorState message={(query.error as Error).message} />}
    {query.data && <>
      {!query.data.items.length && <p className="py-3 text-sm text-ghost">{tr('当前条件下没有问题。', 'No questions match these filters.')}</p>}
      <ul className="divide-y divide-line">
        {query.data.items.map(item => <li key={item.id} className="py-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="break-words text-sm">{item.display || item.text}</p>
              <p className="mt-1 break-all text-xs text-ghost">{item.subject} · {statusLabel(item.review_status, tr)}</p>
              {item.source_changed && <p className="mt-1 text-xs text-brand-amber">{tr('证据已变化，已重新打开。', 'Evidence changed; this question has reopened.')}</p>}
              {item.resolution && <div className="mt-2 rounded-lg bg-brand-mint/5 p-2 text-xs text-brand-mint">
                <span>{tr('解决结论：', 'Resolution: ')}{item.resolution.display || item.resolution.text}</span>
                <p className="mt-1 break-all text-ghost">{item.resolution.subject}</p>
              </div>}
              {item.resolution_hidden && <p className="mt-1 text-xs text-ghost">{tr('关联结论含敏感内容，开启显示后查看。', 'Enable sensitive content to view the linked resolution.')}</p>}
              <ReviewHistory events={item.history} />
            </div>
            <Button variant="ghost" onClick={() => setSelected(item)}>{item.review_status === 'open' ? tr('关联解决结论', 'Link resolution') : tr('重新打开', 'Reopen')}</Button>
          </div>
        </li>)}
      </ul>
      <ReviewPager page={query.data.page} onOffset={setOffset} />
    </>}
    {selected && <ResolveQuestion key={selected.id} question={selected} includeSensitive={includeSensitive} onClose={() => setSelected(null)} />}
  </Card>
}

function ResolveQuestion({ question, includeSensitive, onClose }: { question: Question; includeSensitive: boolean; onClose: () => void }) {
  const tr = useReviewText()
  const t = useT()
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [resolution, setResolution] = useState('')
  const [note, setNote] = useState('')
  const outcomes = useOutcomeReview({ kind: 'resolution', status: 'live', q, include_sensitive: includeSensitive, limit: 10, offset })
  const update = useReviewUpdate('questions')
  const resolving = question.review_status === 'open'
  return <Modal open onClose={onClose} title={resolving ? tr('关联解决结论', 'Link a resolution') : tr('重新打开问题', 'Reopen question')}
    footer={<><Button variant="ghost" onClick={onClose}>{t.common.cancel}</Button>
      <Button loading={update.isPending} disabled={resolving && !resolution} onClick={() => update.mutate({
        id: question.id, status: resolving ? 'resolved' : 'open', expected_version: question.version,
        include_sensitive: includeSensitive, resolution_id: resolving ? resolution : undefined, note,
      }, { onSuccess: () => { toast.success(tr('问题状态已保存', 'Question status saved')); onClose() },
        onError: e => toast.error((e as Error).message) })}>{t.common.confirm}</Button></>}>
    <div className="max-h-[65vh] space-y-3 overflow-y-auto">
      <p className="break-words text-sm">{question.display || question.text}</p>
      {resolving ? <>
        <input className="input w-full" aria-label={tr('搜索解决结论', 'Search resolution outcomes')} placeholder={tr('搜索决定、发现或教训', 'Search decisions, findings or lessons')}
          value={q} onChange={e => { setQ(e.target.value); setOffset(0); setResolution('') }} />
        {outcomes.isLoading && <Spinner />}
        {outcomes.isError && <ErrorState message={(outcomes.error as Error).message} />}
        {outcomes.data && <>
          {!outcomes.data.items.length && <p className="text-xs text-ghost">{tr('没有可关联结论，请先在结论页创建或提炼。', 'No resolution outcomes found. Add or distill conclusions first.')}</p>}
          <div className="space-y-2">
            {outcomes.data.items.map(item => <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-lg border border-line p-2 text-xs">
              <input type="radio" name="resolution" checked={resolution === item.id} onChange={() => setResolution(item.id)} />
              <span className="min-w-0 break-words">{item.display || item.text}<span className="mt-1 block break-all text-ghost">{item.subject}</span></span>
            </label>)}
          </div>
          <ReviewPager page={outcomes.data.page} onOffset={n => { setOffset(n); setResolution('') }} />
        </>}
      </> : <p className="text-xs text-ghost">{tr('重新打开后将移除当前解决关联，历史记录仍保留。', 'Reopening clears the current resolution link and retains its history.')}</p>}
      <textarea className="input w-full" maxLength={2000} value={note} onChange={e => setNote(e.target.value)} aria-label={tr('处理备注', 'Handling note')} placeholder={tr('处理备注（可选）', 'Optional handling note')} />
    </div>
  </Modal>
}
