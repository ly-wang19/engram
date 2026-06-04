import { useState, type FormEvent } from 'react'
import { MessageSquare, Search, Sparkles, Zap } from 'lucide-react'

import { Button, Card, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { useRecall } from '../hooks/queries'

// Pretty section titles for the assembled lean context blocks (engram.memory.lean_context).
const SECTION_LABELS: Array<[RegExp, string]> = [
  [/^USER PROFILE/i, '用户画像'],
  [/^FACTS/i, '相关事实（含日期）'],
  [/^TIMELINE/i, '时间线'],
  [/^SESSION SUMMARIES/i, '会话摘要'],
  [/^RELEVANT CONVERSATIONS/i, '原文片段'],
]

function splitBlocks(context: string) {
  return context
    .split(/\n\n(?=[A-Z][A-Z ]+(?:\([^)]*\))?:)/)
    .map((block) => {
      const nl = block.indexOf('\n')
      const head = nl === -1 ? block : block.slice(0, nl)
      const body = nl === -1 ? '' : block.slice(nl + 1)
      const label = SECTION_LABELS.find(([re]) => re.test(head))?.[1] ?? head.replace(/:$/, '')
      return { label, body: body.trim() || head }
    })
    .filter((b) => b.body)
}

export default function Ask() {
  const recall = useRecall()
  const [query, setQuery] = useState('')

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const q = query.trim()
    if (q) recall.mutate(q)
  }

  const result = recall.data
  const blocks = result ? splitBlocks(result.context) : []

  return (
    <div className="space-y-6">
      <PageHeader
        title="记忆问答"
        subtitle="检索出一小片精炼上下文来回答——这正是 Engram 比塞入全部历史更省、更准的地方"
      />

      <Card>
        <form onSubmit={submit} className="flex gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3 focus-within:border-brand-cyan/60 focus-within:ring-2 focus-within:ring-brand-cyan/20">
            <Search className="h-4 w-4 text-ghost" />
            <input
              className="w-full bg-transparent py-2.5 text-sm outline-none placeholder:text-ghost/70"
              placeholder="问点什么，例如：我对咖啡的偏好是什么？"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Button type="submit" loading={recall.isPending} disabled={!query.trim()}>
            检索
          </Button>
        </form>
      </Card>

      {recall.isPending && <Spinner label="正在检索记忆…" />}
      {recall.isError && <ErrorState message={(recall.error as Error).message} />}

      {result && (
        <>
          {result.answer && (
            <Card className="!border-brand-mint/30 !bg-brand-mint/[0.06]">
              <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-brand-mint">
                <MessageSquare className="h-4 w-4" /> 回答
              </div>
              <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-slate-100">{result.answer}</p>
              <p className="mt-2 text-xs text-ghost">由答题模型基于下面这片记忆上下文生成。</p>
            </Card>
          )}

          <div className="flex items-center gap-3 rounded-xl border border-brand-cyan/20 bg-brand-cyan/5 p-4">
            <Zap className="h-5 w-5 text-brand-cyan" />
            <div className="text-sm">
              <span className="font-semibold text-brand-cyan">约 {result.tokens_est} tokens</span>
              <span className="text-ghost"> 的精炼上下文（而非整段历史）就能作答——这就是“lean 胜过 full-context”。</span>
            </div>
          </div>

          {blocks.length ? (
            <div className="space-y-4">
              {blocks.map((b, i) => (
                <Card key={i}>
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-brand-cyan">{b.label}</div>
                  <pre className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{b.body}</pre>
                </Card>
              ))}
            </div>
          ) : (
            <EmptyState title="这条问题暂时没有检索到记忆" hint="换个说法，或先去存一些记忆。" icon={<Sparkles className="h-6 w-6" />} />
          )}
        </>
      )}
    </div>
  )
}
