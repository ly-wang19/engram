import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Database, FileText, History, MessagesSquare, Send, Sparkles } from 'lucide-react'

import {
  Button,
  Card,
  CardTitle,
  EmptyState,
  ErrorState,
  PageHeader,
  Spinner,
  StatCard,
} from '../components/ui'
import { Badge } from '../components/ui'
import { toast } from '../components/Toast'
import { useMemories, useRemember } from '../hooks/queries'

export default function Dashboard() {
  const { data, isLoading, isError, error } = useMemories()
  const remember = useRemember()
  const [text, setText] = useState('')

  const submit = () => {
    const content = text.trim()
    if (!content) return
    remember.mutate(content, {
      onSuccess: (r) => {
        setText('')
        if (r.scope === 'working') toast.info('临时状态 → 记入历史（可查询），但不进长期画像')
        else if (r.degraded) toast.info('已存为原始记忆（抽取暂时降级）')
        else toast.success(`已记住，抽取出 ${r.extracted} 条新事实`)
      },
      onError: (e) => toast.error(String((e as Error).message)),
    })
  }

  if (isLoading) return <Spinner label="加载你的记忆…" />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const c = data.counts
  const recent = data.facts.filter((f) => f.status === 'live').slice(0, 6)

  return (
    <div className="space-y-6">
      <PageHeader title="总览" subtitle={`你好，${data.user} —— 这是你的长期记忆全貌`} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="当前事实" value={c.facts_live} icon={<Database className="h-5 w-5" />} accent="cyan" />
        <StatCard label="历史（已更新）" value={c.facts_superseded} icon={<History className="h-5 w-5" />} accent="violet" />
        <StatCard label="对话" value={c.episodes} icon={<MessagesSquare className="h-5 w-5" />} accent="mint" />
        <StatCard label="会话摘要" value={c.summaries} icon={<FileText className="h-5 w-5" />} accent="amber" />
      </div>

      <Card>
        <CardTitle hint="System-1 写入 · System-2 异步抽取/消解冲突">存一条新记忆</CardTitle>
        <textarea
          className="input min-h-[84px] resize-y"
          placeholder="例如：我下周二要去上海出差，住在外滩附近。"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit()
          }}
        />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-xs text-ghost">⌘/Ctrl + Enter 快速保存</span>
          <Button onClick={submit} loading={remember.isPending} disabled={!text.trim()}>
            <Send className="h-4 w-4" /> 记住
          </Button>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardTitle hint="L3 画像 · 由当前事实合成">用户画像</CardTitle>
          {data.profile ? (
            <pre className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{data.profile}</pre>
          ) : (
            <EmptyState title="画像还在形成中" hint="多聊几句，或在“事实管理”里补充几条，画像就会自动生成。" icon={<Sparkles className="h-6 w-6" />} />
          )}
        </Card>

        <Card>
          <CardTitle hint={<Link to="/facts" className="text-brand-cyan hover:underline">查看全部 →</Link>}>
            最近的事实
          </CardTitle>
          {recent.length ? (
            <ul className="divide-y divide-line">
              {recent.map((f) => (
                <li key={f.id} className="flex items-baseline gap-2 py-2 text-sm">
                  <time className="shrink-0 text-[11px] tabular-nums text-brand-cyan">{f.valid_at}</time>
                  <span className="flex-1 text-slate-200">{f.text}</span>
                  {f.source === 'user' && <Badge tone="user">🔒</Badge>}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="还没有事实" hint="存一条记忆试试。" />
          )}
        </Card>
      </div>
    </div>
  )
}
