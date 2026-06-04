import { Share2 } from 'lucide-react'

import { Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { ForceGraph } from '../components/ForceGraph'
import { useGraph } from '../hooks/queries'
import { useT } from '../i18n'

export default function GraphPage() {
  const { data, isLoading, isError, error } = useGraph()
  const t = useT()

  if (isLoading) return <Spinner label={t.graph.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const hasGraph = data.nodes.length > 0

  return (
    <div className="space-y-6">
      <PageHeader title={t.graph.title} subtitle={t.graph.subtitle} />

      <Card>
        <CardTitle
          hint={
            hasGraph ? (
              <span className="flex items-center gap-3">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-px w-4 bg-brand-cyan" /> {t.graph.legendLive}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-px w-4 border-t border-dashed border-slate-500" /> {t.graph.legendOld}
                </span>
              </span>
            ) : undefined
          }
        >
          {t.graph.stats(data.nodes.length, data.edges.length)}
        </CardTitle>

        {hasGraph ? (
          <ForceGraph data={data} />
        ) : (
          <EmptyState title={t.graph.emptyTitle} hint={t.graph.emptyHint} icon={<Share2 className="h-6 w-6" />} />
        )}
      </Card>
    </div>
  )
}
