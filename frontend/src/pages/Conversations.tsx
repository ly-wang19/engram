import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { CircleAlert, FileSearch, Hourglass, RefreshCw, Search, Send, ShieldAlert, Trash2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, Field, PageHeader, Spinner } from '../components/ui'
import { Modal } from '../components/Modal'
import { toast } from '../components/Toast'
import {
  useAgentStatus,
  useClearWorking,
  useMemories,
  useRemember,
  useSessionReport,
  useSessions,
  useWorking,
} from '../hooks/queries'
import { useLang, useT } from '../i18n'
import { truncate } from '../lib/format'
import type { RememberScope } from '../types'
import { conversationsText } from './conversationsText'
import { SessionDistillButton } from '../features/jobs/SessionDistillButton'
import { jobsText } from '../features/jobs/text'

export default function Conversations() {
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedSession = searchParams.get('session_id') || ''
  const [episodeOffset, setEpisodeOffset] = useState(0)
  const [episodeDraft, setEpisodeDraft] = useState('')
  const [episodeSearch, setEpisodeSearch] = useState('')
  const { data, isLoading, isError, error, refetch, isFetching } = useMemories({
    facts_limit: 0,
    episodes_limit: 20,
    episodes_offset: episodeOffset,
    session_id: selectedSession || undefined,
    q: episodeSearch,
    include_sensitive: true,
  })
  const remember = useRemember()
  const [sessionId, setSessionId] = useState(selectedSession || 'console:manual')
  const currentSession = sessionId.trim() || 'default'
  const [scope, setScope] = useState<RememberScope>('auto')
  const status = useAgentStatus(currentSession)
  const working = useWorking(currentSession)
  const clearWorking = useClearWorking()
  const t = useT()
  const lang = useLang((s) => s.lang)
  const copy = conversationsText(lang)
  const [text, setText] = useState('')
  const [sessionDraft, setSessionDraft] = useState('')
  const [sessionSearch, setSessionSearch] = useState('')
  const [sessionOffset, setSessionOffset] = useState(0)
  const [sessionStatus, setSessionStatus] = useState<'all' | 'pending' | 'with_facts' | 'working'>('all')
  const [sessionSort, setSessionSort] = useState<'newest' | 'oldest' | 'most_facts'>('newest')
  const sessions = useSessions({ limit: 12, offset: sessionOffset, q: sessionSearch, status: sessionStatus, sort: sessionSort })
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [auditSession, setAuditSession] = useState<string | null>(null)
  const report = useSessionReport(auditSession)

  useEffect(() => {
    setEpisodeOffset(0)
    setEpisodeDraft('')
    setEpisodeSearch('')
    setExpanded({})
    if (selectedSession) setSessionId(selectedSession)
  }, [selectedSession])

  const selectSession = (id: string) => {
    setEpisodeOffset(0)
    setEpisodeDraft('')
    setEpisodeSearch('')
    setExpanded({})
    const next = new URLSearchParams(searchParams)
    if (id) {
      next.set('session_id', id)
      setSessionId(id)
    } else next.delete('session_id')
    setSearchParams(next)
  }

  const submit = () => {
    const content = text.trim()
    if (!content) return
    remember.mutate({ content, session_id: currentSession, scope }, {
      onSuccess: (r) => {
        setText('')
        working.refetch()
        if (r.scope === 'working') toast.info(t.conversations.toastWorking)
        else if (r.degraded) toast.info(t.common.toastDegraded)
        else toast.success(t.common.toastRemembered(r.extracted))
      },
      onError: (e) => toast.error(String((e as Error).message)),
    })
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t.conversations.title} subtitle={t.conversations.subtitle} actions={
        <Link to="/jobs" className="btn-ghost">{jobsText(lang).center}</Link>
      } />

      <Card>
        <CardTitle hint={t.conversations.sessionHint}>{t.common.saveNewMemory}</CardTitle>
        <div className="mb-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_180px_auto] md:items-end">
          <Field label={t.conversations.sessionLabel}>
            <input
              className="input"
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
              placeholder={t.conversations.sessionPlaceholder}
            />
          </Field>
          <Field label={t.conversations.scopeLabel}>
            <select
              className="input"
              value={scope}
              onChange={(e) => setScope(e.target.value as RememberScope)}
            >
              <option value="auto">{t.conversations.scopeAuto}</option>
              <option value="long">{t.conversations.scopeLong}</option>
              <option value="working">{t.conversations.scopeWorking}</option>
            </select>
          </Field>
          <SessionDistillButton sessionId={currentSession} onComplete={() => working.refetch()} />
        </div>
        {status.isError && <div className="mb-3 break-words"><ErrorState message={(status.error as Error).message} /></div>}
        {status.data && (
          <div className="mb-3 rounded-xl border border-line bg-white/[0.03] p-3 text-xs text-ghost">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 max-w-full break-all"><Badge tone="cyan">{t.conversations.statusNamespace(status.data.user)}</Badge></span>
              <span className="min-w-0 max-w-full break-all">{t.conversations.statusSession(status.data.session.id ?? currentSession)}</span>
              <span>{t.conversations.statusFacts(status.data.counts.facts_live)}</span>
              <span>{t.conversations.statusEpisodes(status.data.session.episodes)}</span>
              <span>{t.conversations.statusWorking(status.data.session.working_live)}</span>
              {status.data.session.episodes_pending > 0 && (
                <span className="text-brand-amber">{t.conversations.statusPending(status.data.session.episodes_pending)}</span>
              )}
              {status.data.consolidation_backlog && (
                <span className="inline-flex items-center gap-1 text-brand-amber">
                  <CircleAlert className="h-3.5 w-3.5" /> {t.conversations.statusBacklog}
                </span>
              )}
            </div>
            {status.data.recommended_next_actions.length > 0 && (
              <div className="mt-2 truncate">{status.data.recommended_next_actions[0]}</div>
            )}
          </div>
        )}
        <div className="flex flex-col gap-3 sm:flex-row">
          <textarea
            className="input min-h-[44px] flex-1 resize-y"
            placeholder={t.conversations.rememberPlaceholder}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit()
            }}
          />
          <Button onClick={submit} loading={remember.isPending} disabled={!text.trim()} className="sm:self-end">
            <Send className="h-4 w-4" /> {t.common.remember}
          </Button>
        </div>
      </Card>

      {working.isError && <ErrorState message={(working.error as Error).message} />}

      {(working.data?.items.length ?? 0) > 0 && (
        <Card>
          <CardTitle
            hint={
              <button
                onClick={() =>
                  clearWorking.mutate(currentSession, {
                    onSuccess: () => {
                      working.refetch()
                      toast.success(t.conversations.clearedWorking)
                    },
                    onError: (e) => toast.error(String((e as Error).message)),
                  })
                }
                className="inline-flex items-center gap-1 text-brand-rose hover:underline"
              >
                <Trash2 className="h-3.5 w-3.5" /> {t.conversations.clearSession}
              </button>
            }
          >
            <span className="inline-flex items-center gap-2">
              <Hourglass className="h-4 w-4 text-brand-amber" /> {t.conversations.workingTitle}
            </span>
          </CardTitle>
          <ul className="space-y-1.5">
            {working.data!.items.map((w) => (
              <li key={w.id} className="flex items-center gap-2 text-sm text-slate-200">
                <Badge tone="cyan">{w.kind}</Badge>
                <span className="min-w-0 flex-1 break-words">{w.content}</span>
                {w.expires_at && <span className="text-[11px] text-ghost">{t.conversations.expires(w.expires_at)}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardTitle hint={t.conversations.sessionsHint}>{t.conversations.sessionsTitle}</CardTitle>
        {sessions.data?.summary && (
          <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
            {([
              ['all', copy.all, sessions.data.summary.total],
              ['pending', copy.pending, sessions.data.summary.pending],
              ['with_facts', copy.withFacts, sessions.data.summary.with_facts],
              ['working', copy.working, sessions.data.summary.working],
            ] as const).map(([value, label, count]) => (
              <button
                key={value}
                aria-pressed={sessionStatus === value}
                onClick={() => { setSessionStatus(value); setSessionOffset(0) }}
                className={`min-w-0 rounded-xl border p-3 text-left transition ${sessionStatus === value ? 'border-brand-cyan/50 bg-brand-cyan/10' : 'border-line bg-white/[0.02] hover:border-brand-cyan/30'}`}
              >
                <span className="block text-xl font-semibold tabular-nums text-slate-100">{count}</span>
                <span className="mt-1 block text-xs text-ghost">{label}</span>
              </button>
            ))}
          </div>
        )}
        <form
          className="mb-4 grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_160px_160px]"
          onSubmit={(e) => { e.preventDefault(); setSessionSearch(sessionDraft.trim()); setSessionOffset(0) }}
        >
          <div className="flex min-w-0 items-end gap-2 sm:col-span-2 lg:col-span-1">
            <div className="min-w-0 flex-1">
              <Field label={t.conversations.sessionsSearch}>
                <input className="input" value={sessionDraft} onChange={(e) => setSessionDraft(e.target.value)} placeholder={t.conversations.sessionsSearch} />
              </Field>
            </div>
            <Button type="submit" variant="ghost"><Search className="h-4 w-4" />{copy.search}</Button>
          </div>
          <Field label={copy.state}>
            <select className="input" value={sessionStatus} onChange={(e) => { setSessionStatus(e.target.value as typeof sessionStatus); setSessionOffset(0) }}>
              <option value="all">{copy.all}</option>
              <option value="pending">{copy.pending}</option>
              <option value="with_facts">{copy.withFacts}</option>
              <option value="working">{copy.working}</option>
            </select>
          </Field>
          <Field label={copy.sort}>
            <select className="input" value={sessionSort} onChange={(e) => { setSessionSort(e.target.value as typeof sessionSort); setSessionOffset(0) }}>
              <option value="newest">{copy.newest}</option>
              <option value="oldest">{copy.oldest}</option>
              <option value="most_facts">{copy.mostFacts}</option>
            </select>
          </Field>
        </form>
        {sessions.isLoading && <Spinner label={t.conversations.sessionsLoading} />}
        {sessions.isError && <ErrorState message={(sessions.error as Error).message} />}
        {sessions.data && (
          sessions.data.sessions.length ? (
            <ul className="grid gap-2 md:grid-cols-2">
              {sessions.data.sessions.map((s) => (
                <li key={s.id} className={`min-w-0 rounded-xl border p-3 ${selectedSession === s.id ? 'border-brand-cyan/50 bg-brand-cyan/5' : 'border-line bg-white/[0.02]'}`}>
                  <button
                    className="block max-w-full truncate font-mono text-xs text-brand-cyan hover:underline"
                    onClick={() => selectSession(s.id)}
                    title={s.id}
                  >
                    {s.id}
                  </button>
                  <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-ghost">
                    <span>{t.conversations.sessionCardEpisodes(s.episodes)}</span>
                    <span>{t.conversations.sessionCardFacts(s.facts_added)}</span>
                    {s.working_live > 0 && <span>{t.conversations.sessionCardWorking(s.working_live)}</span>}
                    {s.episodes_pending > 0 && <span className="text-brand-amber">{t.conversations.sessionCardPending(s.episodes_pending)}</span>}
                    {s.facts_sensitive > 0 && <span className="text-brand-rose">{t.conversations.sessionCardSensitive(s.facts_sensitive)}</span>}
                  </div>
                  {s.last_event_at_h && <div className="mt-1 text-[11px] text-ghost">{t.conversations.sessionCardLast(s.last_event_at_h)}</div>}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setAuditSession(s.id)}>
                      <FileSearch className="h-3.5 w-3.5" /> {t.conversations.auditSession}
                    </Button>
                    <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => selectSession(s.id)}>
                      {copy.view}
                    </Button>
                  </div>
                  <SessionDistillButton sessionId={s.id} className="mt-3" />
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title={sessionSearch || sessionStatus !== 'all' ? copy.filteredEmpty : t.conversations.sessionsEmptyTitle} hint={sessionSearch || sessionStatus !== 'all' ? copy.filteredHint : t.conversations.sessionsEmptyHint} />
          )
        )}
        {sessions.data && (
          <BrowsePager offset={sessionOffset} size={12} count={sessions.data.sessions.length} total={sessions.data.page.total} nextOffset={sessions.data.page.next_offset} busy={sessions.isFetching} onChange={setSessionOffset} lang={lang} />
        )}
      </Card>

      <Card>
        <CardTitle hint={data ? t.conversations.episodesHint(data.episodes_page?.total ?? data.episodes.length) : undefined}>{t.conversations.episodesTitle}</CardTitle>
        {selectedSession && (
          <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2 rounded-xl border border-brand-cyan/25 bg-brand-cyan/5 p-3 text-xs">
            <span className="text-ghost">{copy.selected}</span>
            <span className="min-w-0 flex-1 break-all font-mono text-brand-cyan">{selectedSession}</span>
            <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => selectSession('')}>{copy.clear}</Button>
          </div>
        )}
        <form className="mb-4 flex min-w-0 flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); setEpisodeSearch(episodeDraft.trim()); setEpisodeOffset(0); setExpanded({}) }}>
          <div className="min-w-0 flex-1 basis-48">
            <Field label={copy.searchEpisodes}>
              <input className="input" value={episodeDraft} onChange={(e) => setEpisodeDraft(e.target.value)} placeholder={copy.searchEpisodes} />
            </Field>
          </div>
          <Button type="submit" variant="ghost"><Search className="h-4 w-4" />{copy.search}</Button>
          <Button type="button" variant="ghost" loading={isFetching} onClick={() => refetch()} aria-label={copy.refresh} title={copy.refresh}><RefreshCw className="h-4 w-4" /></Button>
        </form>
        {isLoading && <Spinner label={t.conversations.loading} />}
        {isError && <ErrorState message={(error as Error).message} />}
        {data && (data.episodes.length ? (
          <>
          <ul className="gap-3 md:columns-2">
            {data.episodes.map((e) => {
              const key = e.id
              const isOpen = expanded[key]
              const long = e.content.length > 320
              return (
                <li key={key} className="mb-3 break-inside-avoid rounded-xl border border-line bg-white/[0.02] p-4">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] text-ghost">
                    <span className="rounded bg-white/5 px-1.5 py-px tabular-nums text-brand-cyan">{e.date}</span>
                    <button onClick={() => selectSession(e.session)} className="inline-flex min-w-0 max-w-full whitespace-normal break-all rounded bg-white/5 px-1.5 py-px font-mono text-brand-cyan hover:underline">{e.session}</button>
                    <button
                      className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-px text-ghost transition hover:border-brand-cyan/50 hover:text-brand-cyan"
                      onClick={() => setAuditSession(e.session)}
                      title={t.conversations.auditSession}
                    >
                      <FileSearch className="h-3 w-3" /> {t.conversations.auditSession}
                    </button>
                  </div>
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-200">
                    {isOpen || !long ? e.content : truncate(e.content, 320)}
                  </p>
                  {long && (
                    <button
                      className="mt-2 text-xs font-medium text-brand-cyan hover:underline"
                      onClick={() => setExpanded((v) => ({ ...v, [key]: !isOpen }))}
                    >
                      {isOpen ? t.conversations.collapseEntry : t.conversations.expandEntry}
                    </button>
                  )}
                  {e.summary && (
                    <p className="mt-2 break-words border-l-2 border-brand-violet/40 pl-3 text-xs leading-relaxed text-ghost">
                      {t.conversations.summaryPrefix}
                      {e.summary}
                    </p>
                  )}
                </li>
              )
            })}
          </ul>
          </>
        ) : (
          <EmptyState title={selectedSession || episodeSearch ? copy.filteredEmpty : t.conversations.emptyTitle} hint={selectedSession || episodeSearch ? copy.filteredHint : t.conversations.emptyHint} />
        ))}
        {data && (
          <BrowsePager offset={episodeOffset} size={20} count={data.episodes.length} total={data.episodes_page?.total ?? data.episodes.length} nextOffset={data.episodes_page?.next_offset ?? null} busy={isFetching} onChange={(offset) => { setEpisodeOffset(offset); setExpanded({}) }} lang={lang} />
        )}
      </Card>

      <Modal
        open={!!auditSession}
        onClose={() => setAuditSession(null)}
        title={<span className="block max-w-[calc(100vw-8rem)] break-all sm:max-w-sm">{auditSession ? t.conversations.auditTitle(auditSession) : t.conversations.auditTitle('')}</span>}
      >
        {auditSession && <SessionDistillButton sessionId={auditSession} className="mb-4" />}
        {report.isLoading && <Spinner label={t.conversations.auditLoading} />}
        {report.isError && <ErrorState message={(report.error as Error).message} />}
        {report.data && (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <AuditMetric label={t.conversations.auditEpisodes} value={report.data.episodes} />
              <AuditMetric label={t.conversations.auditFacts} value={report.data.facts_added} />
              <AuditMetric label={t.conversations.auditPending} value={report.data.episodes_pending} />
              <AuditMetric label={t.conversations.auditWorking} value={report.data.working_live} />
            </div>
            {report.data.facts_redacted > 0 && (
              <p className="inline-flex items-center gap-2 rounded-lg border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-xs text-brand-amber">
                <ShieldAlert className="h-3.5 w-3.5" /> {t.conversations.auditRedacted(report.data.facts_redacted)}
              </p>
            )}
            {report.data.facts.length ? (
              <ul className="max-h-[360px] space-y-2 overflow-auto pr-1">
                {report.data.facts.map((f) => (
                  <li key={f.id} className="rounded-lg border border-line bg-white/[0.03] p-3">
                    <div className="break-words text-slate-100">{f.display || f.text}</div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-ghost">
                      <Badge tone={f.status === 'live' ? 'live' : 'old'}>{f.status}</Badge>
                      {f.category && <span className="rounded bg-brand-violet/15 px-1.5 py-px text-brand-violet">{f.category}</span>}
                      {f.sensitive && (
                        <span className="inline-flex items-center gap-1 rounded bg-brand-rose/15 px-1.5 py-px text-brand-rose">
                          <ShieldAlert className="h-3 w-3" /> {t.conversations.auditSensitive}
                        </span>
                      )}
                      <span>{f.valid_at}</span>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title={t.conversations.auditEmptyTitle} hint={t.conversations.auditEmptyHint} />
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}

function AuditMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-line bg-white/[0.03] px-3 py-2">
      <div className="text-lg font-semibold leading-none text-slate-100">{value}</div>
      <div className="mt-1 text-[11px] text-ghost">{label}</div>
    </div>
  )
}

function BrowsePager({ offset, size, count, total, nextOffset, busy, onChange, lang }: {
  offset: number
  size: number
  count: number
  total: number
  nextOffset: number | null
  busy: boolean
  onChange: (offset: number) => void
  lang: 'zh' | 'en'
}) {
  const copy = conversationsText(lang)
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
      <span className="text-xs tabular-nums text-ghost" aria-live="polite">{copy.range(count ? offset + 1 : 0, count ? offset + count : 0, total)}</span>
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" disabled={offset === 0 || busy} onClick={() => onChange(Math.max(0, offset - size))}>{copy.previous}</Button>
        <Button variant="ghost" disabled={nextOffset === null || busy} onClick={() => nextOffset !== null && onChange(nextOffset)}>{copy.next}</Button>
      </div>
    </div>
  )
}
