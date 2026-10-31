import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHeader, Spinner } from '../../components/ui'
import { toast } from '../../components/Toast'
import { useLang } from '../../i18n'
import { useAuth } from '../../store/auth'
import type { JobStatus } from './api'
import { useJobActions, useJobs } from './hooks'
import { jobsText } from './text'

export default function JobsPage() {
  const apiKey = useAuth((s) => s.apiKey)
  return <JobsContent key={apiKey} />
}

function JobsContent() {
  const [params, setParams] = useSearchParams()
  const sessionId = params.get('session_id') || ''
  const [draft, setDraft] = useState(sessionId)
  const [status, setStatus] = useState<JobStatus | ''>('')
  const [offset, setOffset] = useState(0)
  const lang = useLang((s) => s.lang)
  const copy = jobsText(lang)
  const query = useJobs({ status: status || undefined, session_id: sessionId || undefined, offset, limit: 20 })
  const { retry, invalidateMemory } = useJobActions()
  const activeSeen = useRef(new Set<string>())

  useEffect(() => {
    for (const job of query.data?.jobs ?? []) {
      const key = `${job.id}:${job.attempt}`
      if (job.status === 'pending' || job.status === 'running') activeSeen.current.add(key)
      else if (activeSeen.current.delete(key)) invalidateMemory()
    }
  }, [query.data, invalidateMemory])

  useEffect(() => { setDraft(sessionId); setOffset(0) }, [sessionId])

  return (
    <div className="min-w-0 space-y-5">
      <PageHeader title={copy.title} subtitle={copy.subtitle} actions={
        <Button variant="ghost" loading={query.isFetching} onClick={() => query.refetch()}><RefreshCw className="h-4 w-4" />{copy.refresh}</Button>
      } />
      <Card>
        <form className="flex min-w-0 flex-wrap items-end gap-3" onSubmit={(event) => {
          event.preventDefault()
          const next = new URLSearchParams(params)
          if (draft.trim()) next.set('session_id', draft.trim())
          else next.delete('session_id')
          setOffset(0); setParams(next)
        }}>
          <div className="min-w-0 flex-1 basis-48"><Field label={copy.session}><input className="input" value={draft} onChange={(event) => setDraft(event.target.value)} /></Field></div>
          <Field label={copy.status}><select className="input" value={status} onChange={(event) => { setStatus(event.target.value as JobStatus | ''); setOffset(0) }}>
            <option value="">{copy.all}</option>
            {Object.entries(copy.states).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select></Field>
          <Button type="submit" variant="ghost">{copy.filter}</Button>
        </form>
      </Card>
      {query.isLoading && <Spinner label={copy.loading} />}
      {query.isError && <ErrorState message={(query.error as Error).message} />}
      {retry.isError && <ErrorState message={(retry.error as Error).message} />}
      {query.data && (query.data.jobs.length ? <ul className="space-y-3">
        {query.data.jobs.map((job) => <li key={job.id} className="min-w-0 rounded-xl border border-line bg-white/[0.02] p-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
            <Link to={`/conversations?session_id=${encodeURIComponent(job.session_id)}`} title={copy.view} className="min-w-0 break-all font-mono text-sm text-brand-cyan hover:underline">{job.session_id}</Link>
            <Badge tone={job.status === 'succeeded' ? 'live' : job.status === 'failed' ? 'user' : 'cyan'}>{copy.states[job.status]}</Badge>
          </div>
          <p role="status" className="mt-3 break-words text-sm text-slate-200">{copy.result(job)}</p>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-ghost">
            <span>{copy.attempt(job.attempt)}</span><span>{copy.created}: {new Date(job.created_at).toLocaleString(lang === 'zh' ? 'zh-CN' : 'en-US')}</span>
            {job.status === 'failed' && <Button variant="ghost" loading={retry.isPending && retry.variables === job.id} disabled={retry.isPending} onClick={() => retry.mutate(job.id, {
              onSuccess: (value) => toast.info(value.deduplicated ? copy.duplicate : copy.submitted),
              onError: (error) => toast.error(error.message),
            })}>{copy.retry}</Button>}
          </div>
          {job.history.length > 0 && <details className="mt-3 text-xs text-ghost"><summary className="cursor-pointer">{copy.details}</summary>
            <ul className="mt-2 space-y-2">{job.history.map((attempt) => <li key={attempt.attempt} className="break-words border-l border-line pl-3">{copy.attempt(attempt.attempt)} · {copy.states[attempt.status]} · {copy.reason(attempt.reason)}</li>)}</ul>
          </details>}
        </li>)}
      </ul> : <Card><EmptyState title={copy.empty} hint={copy.emptyHint} /></Card>)}
      {query.data && <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-ghost">{copy.count(query.data.page.total)}</span>
        <div className="flex gap-2"><Button variant="ghost" disabled={offset === 0 || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 20))}>{copy.previous}</Button>
          <Button variant="ghost" disabled={query.data.page.next_offset === null || query.isFetching} onClick={() => setOffset(query.data!.page.next_offset!)}>{copy.next}</Button></div>
      </div>}
    </div>
  )
}
