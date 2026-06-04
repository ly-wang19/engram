import { Card, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { Timeline } from '../components/Timeline'
import { useMemories } from '../hooks/queries'
import { useT } from '../i18n'

export default function TimelinePage() {
  const { data, isLoading, isError, error } = useMemories()
  const t = useT()

  if (isLoading) return <Spinner label={t.timeline.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  return (
    <div className="space-y-6">
      <PageHeader title={t.timeline.title} subtitle={t.timeline.subtitle} />

      <div className="flex flex-wrap items-center gap-4 text-xs text-ghost">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-brand-mint" /> {t.timeline.legendLive}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-slate-500" /> {t.timeline.legendOld}
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="rounded border border-brand-violet/40 px-1 text-brand-violet">{t.timeline.legendUpdate}</span>{' '}
          {t.timeline.legendUpdateNote}
        </span>
      </div>

      <Card>
        {data.facts.length ? (
          <Timeline facts={data.facts} />
        ) : (
          <EmptyState title={t.timeline.emptyTitle} hint={t.timeline.emptyHint} />
        )}
      </Card>
    </div>
  )
}
