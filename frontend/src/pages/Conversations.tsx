import { useState } from 'react'
import { Send } from 'lucide-react'

import { Button, Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { toast } from '../components/Toast'
import { useMemories, useRemember } from '../hooks/queries'

export default function Conversations() {
  const { data, isLoading, isError, error } = useMemories()
  const remember = useRemember()
  const [text, setText] = useState('')

  const submit = () => {
    const content = text.trim()
    if (!content) return
    remember.mutate(content, {
      onSuccess: (r) => {
        setText('')
        if (r.degraded) toast.info('已存为原始记忆（抽取暂时降级）')
        else toast.success(`已记住，抽取出 ${r.extracted} 条新事实`)
      },
      onError: (e) => toast.error(String((e as Error).message)),
    })
  }

  if (isLoading) return <Spinner label="加载对话…" />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  return (
    <div className="space-y-6">
      <PageHeader title="原始对话" subtitle="无损保存的原始记忆流（System-1），以及每条的会话摘要（L2）" />

      <Card>
        <CardTitle>存一条新记忆</CardTitle>
        <div className="flex flex-col gap-3 sm:flex-row">
          <textarea
            className="input min-h-[44px] flex-1 resize-y"
            placeholder="把想让它记住的话写在这里…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit()
            }}
          />
          <Button onClick={submit} loading={remember.isPending} disabled={!text.trim()} className="sm:self-end">
            <Send className="h-4 w-4" /> 记住
          </Button>
        </div>
      </Card>

      <Card>
        <CardTitle hint={`${data.episodes.length} 条`}>对话记录</CardTitle>
        {data.episodes.length ? (
          <ul className="space-y-3">
            {data.episodes
              .slice()
              .reverse()
              .map((e, i) => (
                <li key={i} className="rounded-xl border border-line bg-white/[0.02] p-4">
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] text-ghost">
                    <span className="rounded bg-white/5 px-1.5 py-px tabular-nums text-brand-cyan">{e.date}</span>
                    <span className="rounded bg-white/5 px-1.5 py-px font-mono">{e.session}</span>
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{e.content}</p>
                  {e.summary && (
                    <p className="mt-2 border-l-2 border-brand-violet/40 pl-3 text-xs leading-relaxed text-ghost">
                      摘要：{e.summary}
                    </p>
                  )}
                </li>
              ))}
          </ul>
        ) : (
          <EmptyState title="还没有对话" hint="在上面存一条试试。" />
        )}
      </Card>
    </div>
  )
}
