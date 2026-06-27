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
import { useMemories, useRemember, useStructuredProfile } from '../hooks/queries'
import { splitStamp } from '../lib/format'
import { useT } from '../i18n'
import type { StructuredProfile } from '../types'

export default function Dashboard() {
  const { data, isLoading, isError, error } = useMemories({ facts_limit: 6, episodes_limit: 0, status: 'live' })
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
  const recent = data.facts.filter((f) => f.status === 'live').slice(0, 6)

  return (
    <div className="space-y-6">
      <PageHeader title={t.dashboard.title} subtitle={t.dashboard.subtitle(data.user)} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label={t.dashboard.statLive} value={c.facts_live} icon={<Database className="h-5 w-5" />} accent="cyan" />
        <StatCard label={t.dashboard.statSuperseded} value={c.facts_superseded} icon={<History className="h-5 w-5" />} accent="violet" />
        <StatCard label={t.dashboard.statEpisodes} value={c.episodes} icon={<MessagesSquare className="h-5 w-5" />} accent="mint" />
        <StatCard label={t.dashboard.statSummaries} value={c.summaries} icon={<FileText className="h-5 w-5" />} accent="amber" />
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

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardTitle hint={t.dashboard.structuredHint}>{t.dashboard.personaTitle}</CardTitle>
          {profile.data && !profile.isError ? (
            <StructuredProfileMini data={profile.data} />
          ) : (
            <EmptyState title={t.dashboard.personaEmptyTitle} hint={t.dashboard.personaEmptyHint} icon={<Sparkles className="h-6 w-6" />} />
          )}
        </Card>

        <Card>
          <CardTitle hint={<Link to="/facts" className="text-brand-cyan hover:underline">{t.dashboard.recentAll}</Link>}>
            {t.dashboard.recentTitle}
          </CardTitle>
          {recent.length ? (
            <ul className="divide-y divide-line">
              {recent.map((f) => {
                const { date, time } = splitStamp(f.valid_at)
                return (
                <li key={f.id} className="flex items-baseline gap-2 py-2 text-sm">
                  <time className="shrink-0 text-[11px] tabular-nums text-brand-cyan">
                    {date}{time && <span className="ml-1 text-brand-cyan/55">{time}</span>}
                  </time>
                  <span className="flex-1 text-slate-200">{f.display || f.text}</span>
                  {f.source === 'user' && <Badge tone="user">🔒</Badge>}
                </li>
                )
              })}
            </ul>
          ) : (
            <EmptyState title={t.dashboard.recentEmptyTitle} hint={t.dashboard.recentEmptyHint} />
          )}
        </Card>
      </div>
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
