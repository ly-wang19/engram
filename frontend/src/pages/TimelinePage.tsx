import { Card, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { Timeline } from '../components/Timeline'
import { useMemories } from '../hooks/queries'

export default function TimelinePage() {
  const { data, isLoading, isError, error } = useMemories()

  if (isLoading) return <Spinner label="加载时间线…" />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  return (
    <div className="space-y-6">
      <PageHeader title="记忆时间线" subtitle="双时间轴视图：看记忆如何随时间演化、被更新、被替代（最新在上）" />

      <div className="flex flex-wrap items-center gap-4 text-xs text-ghost">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-brand-mint" /> 当前有效
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-slate-500" /> 已被替代
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="rounded border border-brand-violet/40 px-1 text-brand-violet">↑ 更新</span> 这条更新了更早的值
        </span>
      </div>

      <Card>
        {data.facts.length ? (
          <Timeline facts={data.facts} />
        ) : (
          <EmptyState title="还没有事实可展示" hint="先去存一些记忆，时间线会自动生长。" />
        )}
      </Card>
    </div>
  )
}
