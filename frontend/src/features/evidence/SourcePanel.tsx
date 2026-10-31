import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, ErrorState, Spinner } from '../../components/ui'
import { api } from '../../lib/api'
import { useAuth } from '../../store/auth'
import { useLang } from '../../i18n'
import { evidenceApi } from './api'

export function SourcePanel({ token, sensitive, onCorrected, onClose }: {
  token: string; sensitive: boolean; onCorrected: () => void; onClose: () => void
}) {
  const key = useAuth(s => s.apiKey)
  const zh = useLang(s => s.lang) === 'zh'
  const qc = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const source = useQuery({ queryKey: ['evidence-source', key, token, sensitive, offset],
    queryFn: () => evidenceApi.source(token, sensitive, offset), retry: false, staleTime: 0 })
  const edit = useMutation({ mutationKey: ['evidence-edit', key],
    mutationFn: () => api.editFact(source.data!.fact!.id, { object: value.trim() }),
    onSuccess: () => { qc.removeQueries({ queryKey: ['evidence-source', key] }); void qc.invalidateQueries(); onCorrected() },
  })
  const data = source.data
  return <section className="min-w-0 space-y-3 rounded-xl border border-brand-cyan/40 p-4" aria-label={zh ? '证据出处' : 'Evidence source'}>
    <div className="flex items-center justify-between gap-2"><h3 className="font-semibold">{zh ? '证据出处' : 'Evidence source'}</h3>
      <Button variant="ghost" onClick={onClose}>{zh ? '关闭' : 'Close'}</Button></div>
    {source.isPending && <Spinner />}
    {source.isError && <ErrorState message={source.error.message} />}
    {data?.fact && <>
      <p className="break-all text-xs text-ghost">{data.fact.id} · {data.fact.source}</p>
      <p className="whitespace-pre-wrap break-words">{data.fact.text}</p>
      {!editing ? <Button variant="ghost" onClick={() => { setValue(data.fact!.object); setEditing(true) }}>{zh ? '更正这条事实' : 'Correct this fact'}</Button>
        : <form className="space-y-2" onSubmit={e => { e.preventDefault(); edit.mutate() }}>
          <label className="block text-sm">{zh ? '正确的内容' : 'Correct value'}
            <textarea className="input mt-1 min-h-24 w-full" value={value} onChange={e => setValue(e.target.value)} /></label>
          <p className="text-xs text-ghost">{zh ? '保存后标为本人更正；这次回答将失效，请重新提问。' : 'Saved as your correction. Ask again to generate an answer from the updated evidence.'}</p>
          {edit.isError && <ErrorState message={edit.error.message} />}
          <Button type="submit" disabled={!value.trim()} loading={edit.isPending}>{zh ? '保存更正' : 'Save correction'}</Button>
        </form>}
      {data.raw_redacted && <p className="text-sm text-ghost">{zh ? '原文可能含敏感信息。关闭问答中的脱敏选项后重新提问，才能查看原文。' : 'Raw sources may contain sensitive information. Disable redaction and ask again to inspect them.'}</p>}
    </>}
    {(data?.episode ? [data.episode] : data?.episodes ?? []).map(ep => <article key={ep.id} className="min-w-0 border-t border-line pt-3">
      <p className="break-all text-xs text-ghost">{ep.session_id} · {ep.id} · {new Date(ep.event_time * 1000).toLocaleString()}</p>
      <pre className="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-words text-sm">{ep.content}</pre>
    </article>)}
    {data?.kind === 'fact' && !data.raw_redacted && !data.episodes?.length && <p className="text-sm text-ghost">{zh ? '没有可定位的原文；这可能是手动添加的事实。' : 'No original source is available; this may be a manually added fact.'}</p>}
    {data?.page && (offset > 0 || data.page.next_offset !== null) && <div className="flex gap-2">
      <Button variant="ghost" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 10))}>{zh ? '上一页' : 'Previous'}</Button>
      <Button variant="ghost" disabled={data.page.next_offset === null} onClick={() => setOffset(data.page!.next_offset!)}>{zh ? '下一页' : 'Next'}</Button>
    </div>}
  </section>
}
