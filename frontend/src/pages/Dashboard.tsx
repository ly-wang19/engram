import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Database, History, Lightbulb, MessagesSquare, Send, Sparkles } from 'lucide-react'

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
import { toast } from '../components/Toast'
import { useMemories, useRemember, useStructuredProfile } from '../hooks/queries'
import { useT } from '../i18n'
import { groupOutcomes, SessionOutcomes } from './Journal'
import type { StructuredProfile } from '../types'

export default function Dashboard() {
  const { data, isLoading, isError, error } = useMemories({ facts_limit: 6, episodes_limit: 0, status: 'live' })
  // The conclusions the owner actually reads. Separate query so the counts above stay unfiltered.
  // include_sensitive matches counts.facts_outcomes (which ignores sensitivity) and the Journal: without
  // it, editing a conclusion into something classify() calls sensitive drops it from this list while the
  // count still reports it, and the card renders as a heading with nothing under it.
  const outcomes = useMemories({
    kind: 'outcomes',
    facts_limit: 12,
    episodes_limit: 0,
    status: 'live',
    include_sensitive: true,
  })
  const profile = useStructuredProfile()
  const remember = useRemember()
  const t = useT()
  const [text, setText] = useState('')

  const submit = () => {
    const content = text.trim()
    if (!content) return
    remember.mutate(content, {
      onSuccess: (r) => {
        setText('')
        if (r.scope === 'working') toast.info(t.dashboard.toastWorking)
        else if (r.degraded) toast.info(t.common.toastDegraded)
        else toast.success(t.common.toastRemembered(r.extracted))
      },
      onError: (e) => toast.error(String((e as Error).message)),
    })
  }

  if (isLoading) return <Spinner label={t.dashboard.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const c = data.counts
  const groups = groupOutcomes(outcomes.data?.facts ?? []).slice(0, 2)

  return (
    <div className="space-y-6">
      <PageHeader title={t.dashboard.title} subtitle={t.dashboard.subtitle(data.user)} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label={t.dashboard.statLive} value={c.facts_live} icon={<Database className="h-5 w-5" />} accent="cyan" />
        <StatCard label={t.dashboard.statSuperseded} value={c.facts_superseded} icon={<History className="h-5 w-5" />} accent="violet" />
        <StatCard label={t.dashboard.statEpisodes} value={c.episodes} icon={<MessagesSquare className="h-5 w-5" />} accent="mint" />
        <StatCard label={t.dashboard.statOutcomes} value={c.facts_outcomes} icon={<Lightbulb className="h-5 w-5" />} accent="amber" />
      </div>

      <Card>
        <CardTitle hint={t.dashboard.rememberHint}>{t.common.saveNewMemory}</CardTitle>
        <textarea
          className="input min-h-[84px] resize-y"
          placeholder={t.dashboard.rememberPlaceholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit()
          }}
        />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-xs text-ghost">{t.dashboard.quickSave}</span>
          <Button onClick={submit} loading={remember.isPending} disabled={!text.trim()}>
            <Send className="h-4 w-4" /> {t.common.remember}
          </Button>
        </div>
      </Card>

      {/* The journal, not the fragment stream: a session's conclusions are what a person can read. */}
      <Card>
        <CardTitle hint={<Link to="/journal" className="text-brand-cyan hover:underline">{t.dashboard.conclusionsAll}</Link>}>
          {t.dashboard.conclusionsTitle}
        </CardTitle>
        {/* The counter comes from the already-loaded main query, so the empty state never flashes
            while the conclusions themselves are still in flight. */}
        {c.facts_outcomes > 0 ? (
          <div className="space-y-3">
            {groups.map((g, i) => (
              <SessionOutcomes key={`${g.session}-${i}`} group={g} className="!bg-white/[0.02] !p-4" />
            ))}
          </div>
        ) : (
          <EmptyState
            title={t.dashboard.conclusionsEmptyTitle}
            hint={t.dashboard.conclusionsEmptyHint}
            icon={<Lightbulb className="h-6 w-6" />}
          />
        )}
      </Card>

      <Card>
        <CardTitle hint={t.dashboard.structuredHint}>{t.dashboard.personaTitle}</CardTitle>
        {profile.data && !profile.isError ? (
          <StructuredProfileMini data={profile.data} />
        ) : (
          <EmptyState title={t.dashboard.personaEmptyTitle} hint={t.dashboard.personaEmptyHint} icon={<Sparkles className="h-6 w-6" />} />
        )}
      </Card>
    </div>
  )
}

function StructuredProfileMini({ data }: { data: StructuredProfile }) {
  const t = useT()
  const prefItems = Object.entries(data.preferences).flatMap(([cat, items]) =>
    items.slice(0, 4).map((item) => ({ ...item, cat })),
  )
  const empty = data.counts.basic + data.counts.preferences + data.counts.habits === 0
  if (empty) return <EmptyState title={t.dashboard.personaEmptyTitle} hint={t.dashboard.personaEmptyHint} />
  return (
    <div className="space-y-4">
      {data.basic.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {data.basic.slice(0, 4).map((b) => (
            <div key={b.field} className="rounded-xl border border-line bg-white/[0.03] p-3">
              <div className="text-[11px] uppercase tracking-wide text-ghost">{b.label}</div>
              <div className="mt-1 break-words text-sm font-medium text-slate-100">{b.value}</div>
            </div>
          ))}
        </div>
      )}
      {prefItems.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {prefItems.slice(0, 12).map((it) => (
            <span key={it.fact_id} className="inline-flex min-w-0 max-w-full whitespace-normal break-all rounded-lg bg-brand-mint/10 px-2.5 py-1 text-sm text-slate-100">
              {it.item}
            </span>
          ))}
        </div>
      )}
      {data.habits.length > 0 && (
        <ul className="space-y-1.5 text-sm text-slate-200">
          {data.habits.slice(0, 4).map((h) => (
            <li key={h.fact_id} className="flex gap-2">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-violet" />
              <span className="break-words">{h.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
