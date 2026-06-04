import { Share2 } from 'lucide-react'

import { Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { ForceGraph } from '../components/ForceGraph'
import { useGraph } from '../hooks/queries'

export default function GraphPage() {
  const { data, isLoading, isError, error } = useGraph()

  if (isLoading) return <Spinner label="构建关系图谱…" />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const hasGraph = data.nodes.length > 0

  return (
    <div className="space-y-6">
      <PageHeader title="关系图谱" subtitle="语义记忆的实体与关系——多跳推理就在这张图上行走" />

      <Card>
        <CardTitle
          hint={
            hasGraph ? (
              <span className="flex items-center gap-3">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-px w-4 bg-brand-cyan" /> 当前关系
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-px w-4 border-t border-dashed border-slate-500" /> 已失效
                </span>
              </span>
            ) : undefined
          }
        >
          {data.nodes.length} 个实体 · {data.edges.length} 条关系
        </CardTitle>

        {hasGraph ? (
          <ForceGraph data={data} />
        ) : (
          <EmptyState
            title="图谱还是空的"
            hint="多聊几句或加几条事实，实体（人、地点、物）和它们之间的关系就会浮现。悬停节点可高亮其邻居。"
            icon={<Share2 className="h-6 w-6" />}
          />
        )}
      </Card>
    </div>
  )
}
