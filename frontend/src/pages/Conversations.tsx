import { useState } from 'react'
import { CheckCircle2, CircleAlert, FileSearch, Hourglass, Send, ShieldAlert, Trash2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, Field, PageHeader, Spinner } from '../components/ui'
import { Modal } from '../components/Modal'
import { toast } from '../components/Toast'
import {
  useAgentStatus,
  useClearWorking,
  useCloseSession,
  useMemories,
  useRemember,
  useSessionReport,
  useSessions,
  useWorking,
} from '../hooks/queries'
import { useT } from '../i18n'
import { truncate } from '../lib/format'
import type { RememberScope } from '../types'

export default function Conversations() {
  const [limit, setLimit] = useState(20)
  const { data, isLoading, isError, error } = useMemories({ facts_limit: 0, episodes_limit: limit })
  const remember = useRemember()
  const [sessionId, setSessionId] = useState('console:manual')
  const currentSession = sessionId.trim() || 'default'
  const [scope, setScope] = useState<RememberScope>('auto')
  const status = useAgentStatus(currentSession)
  const working = useWorking(currentSession)
  const clearWorking = useClearWorking()
  const closeSession = useCloseSession()
  const t = useT()
  const [text, setText] = useState('')
  const [sessionSearch, setSessionSearch] = useState('')
  const sessions = useSessions({ limit: 12, q: sessionSearch })
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [auditSession, setAuditSession] = useState<string | null>(null)
  const report = useSessionReport(auditSession)

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

  if (isLoading) return <Spinner label={t.conversations.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  return (
    <div className="space-y-6">
      <PageHeader title={t.conversations.title} subtitle={t.conversations.subtitle} />

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
          <Button
            variant="ghost"
            loading={closeSession.isPending}
            onClick={() =>
              closeSession.mutate(currentSession, {
                onSuccess: (r) => {
                  toast.success(t.conversations.closedSession(r.facts_added))
                  setAuditSession(currentSession)
                  working.refetch()
                },
                onError: (e) => toast.error(String((e as Error).message)),
              })
            }
          >
            <CheckCircle2 className="h-4 w-4" /> {t.conversations.closeSession}
          </Button>
        </div>
        {status.data && (
          <div className="mb-3 rounded-xl border border-line bg-white/[0.03] p-3 text-xs text-ghost">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="cyan">{t.conversations.statusNamespace(status.data.user)}</Badge>
              <span>{t.conversations.statusSession(status.data.session.id ?? currentSession)}</span>
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
                <span className="flex-1">{w.content}</span>
                {w.expires_at && <span className="text-[11px] text-ghost">{t.conversations.expires(w.expires_at)}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardTitle hint={t.conversations.sessionsHint}>{t.conversations.sessionsTitle}</CardTitle>
        <input
          className="input mb-3"
          value={sessionSearch}
          onChange={(e) => setSessionSearch(e.target.value)}
          placeholder={t.conversations.sessionsSearch}
        />
        {sessions.isLoading && <Spinner label={t.conversations.sessionsLoading} />}
        {sessions.isError && <ErrorState message={(sessions.error as Error).message} />}
        {sessions.data && (
          sessions.data.sessions.length ? (
            <ul className="grid gap-2 md:grid-cols-2">
              {sessions.data.sessions.map((s) => (
                <li key={s.id} className="rounded-xl border border-line bg-white/[0.02] p-3">
                  <button
                    className="block max-w-full truncate font-mono text-xs text-brand-cyan hover:underline"
                    onClick={() => setSessionId(s.id)}
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
                  <div className="mt-3 flex gap-2">
                    <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setAuditSession(s.id)}>
                      <FileSearch className="h-3.5 w-3.5" /> {t.conversations.auditSession}
                    </Button>
                    <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setSessionId(s.id)}>
                      {t.conversations.useSession}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title={t.conversations.sessionsEmptyTitle} hint={t.conversations.sessionsEmptyHint} />
          )
        )}
      </Card>

      <Card>
        <CardTitle hint={t.conversations.episodesHint(data.episodes_page?.total ?? data.episodes.length)}>{t.conversations.episodesTitle}</CardTitle>
        {data.episodes.length ? (
          <>
          <ul className="gap-3 md:columns-2">
            {data.episodes.map((e, i) => {
              const key = `${e.date}-${e.session}-${i}`
              const isOpen = expanded[key]
              const long = e.content.length > 320
              return (
                <li key={key} className="mb-3 break-inside-avoid rounded-xl border border-line bg-white/[0.02] p-4">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] text-ghost">
                    <span className="rounded bg-white/5 px-1.5 py-px tabular-nums text-brand-cyan">{e.date}</span>
                    <span className="inline-flex min-w-0 max-w-full whitespace-normal break-all rounded bg-white/5 px-1.5 py-px font-mono">{e.session}</span>
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
                    <p className="mt-2 border-l-2 border-brand-violet/40 pl-3 text-xs leading-relaxed text-ghost">
                      {t.conversations.summaryPrefix}
                      {e.summary}
                    </p>
                  )}
                </li>
              )
            })}
          </ul>
          {data.episodes_page?.has_more && (
            <div className="mt-4 flex justify-center">
              <Button variant="ghost" onClick={() => setLimit((n) => n + 20)}>
                {t.conversations.loadMore}
              </Button>
            </div>
          )}
          </>
        ) : (
          <EmptyState title={t.conversations.emptyTitle} hint={t.conversations.emptyHint} />
        )}
      </Card>

      <Modal
        open={!!auditSession}
        onClose={() => setAuditSession(null)}
        title={auditSession ? t.conversations.auditTitle(auditSession) : t.conversations.auditTitle('')}
      >
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
