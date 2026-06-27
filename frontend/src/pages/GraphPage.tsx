import { useState } from 'react'
import { Search, Share2 } from 'lucide-react'

import { Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { ForceGraph } from '../components/ForceGraph'
import { useGraph } from '../hooks/queries'
import { useT } from '../i18n'

export default function GraphPage() {
  const t = useT()
  const [q, setQ] = useState('')
  const [liveOnly, setLiveOnly] = useState(true)
  const [hideSensitive, setHideSensitive] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data, isLoading, isError, error } = useGraph({
    q,
    live_only: liveOnly,
    include_sensitive: !hideSensitive,
    limit: 120,
  })

  if (isLoading) return <Spinner label={t.graph.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const hasGraph = data.nodes.length > 0
  const selected = data.nodes.find((n) => n.id === selectedId) ?? null
  const related = selected
    ? data.edges.filter((e) => e.source === selected.id || e.target === selected.id)
    : []

  return (
    <div className="space-y-6">
      <PageHeader title={t.graph.title} subtitle={t.graph.subtitle} />

      <Card className="!p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3">
            <Search className="h-4 w-4 text-ghost" />
            <input
              aria-label={t.graph.searchPlaceholder}
              className="w-full bg-transparent py-2 text-sm outline-none placeholder:text-ghost/70"
              placeholder={t.graph.searchPlaceholder}
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setSelectedId(null)
              }}
            />
          </div>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input type="checkbox" checked={liveOnly} onChange={(e) => setLiveOnly(e.target.checked)} className="accent-brand-cyan" />
            {t.graph.currentOnly}
          </label>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input type="checkbox" checked={hideSensitive} onChange={(e) => setHideSensitive(e.target.checked)} className="accent-brand-rose" />
            {t.graph.hideSensitive}
          </label>
        </div>
      </Card>

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
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <ForceGraph data={data} selectedId={selectedId} onSelect={setSelectedId} />
            <aside className="rounded-xl border border-line bg-white/[0.03] p-4">
              {selected ? (
                <div className="space-y-3">
                  <div>
                    <div className="text-xs uppercase tracking-wide text-ghost">{selected.type}</div>
                    <div className="mt-1 break-words text-sm font-semibold text-slate-100">{selected.name}</div>
                  </div>
                  <div className="text-xs text-ghost">{t.graph.edgeCount(related.length)}</div>
                  <ul className="space-y-2">
                    {related.slice(0, 12).map((e, i) => {
                      const other = data.nodes.find((n) => n.id === (e.source === selected.id ? e.target : e.source))
                      return (
                        <li key={`${e.fact_id ?? i}`} className="rounded-lg bg-white/[0.04] p-2 text-xs leading-relaxed">
                          <div className="break-words text-slate-200">
                            <span className="text-brand-cyan">{e.predicate}</span> {other?.name ?? ''}
                          </div>
                          {e.fact_text && <div className="mt-1 break-words text-ghost">{e.fact_text}</div>}
                          <div className="mt-1 text-[11px] text-ghost">
                            {e.valid_at_h}
                            {!e.live && e.invalid_at_h ? ` · ${t.graph.legendOld} ${e.invalid_at_h}` : ''}
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </div>
              ) : (
                <EmptyState title={t.common.details} hint={t.graph.nodeHint} />
              )}
            </aside>
          </div>
        ) : (
          <EmptyState title={t.graph.emptyTitle} hint={t.graph.emptyHint} icon={<Share2 className="h-6 w-6" />} />
        )}
      </Card>
    </div>
  )
}
