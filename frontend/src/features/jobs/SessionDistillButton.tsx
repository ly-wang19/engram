import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2 } from 'lucide-react'
import { Button } from '../../components/ui'
import { toast } from '../../components/Toast'
import { useLang } from '../../i18n'
import { useAuth } from '../../store/auth'
import type { DistillationJob } from './api'
import { useJobActions, useJobs } from './hooks'
import { jobsText } from './text'

export interface SessionDistillButtonProps {
  sessionId: string
  className?: string
  label?: ReactNode
  onComplete?: (job: DistillationJob) => void
}

export function SessionDistillButton(props: SessionDistillButtonProps) {
  const apiKey = useAuth((s) => s.apiKey)
  return <SessionButton key={`${apiKey}:${props.sessionId}`} {...props} />
}

function SessionButton({ sessionId, className, label, onComplete }: SessionDistillButtonProps) {
  const lang = useLang((s) => s.lang)
  const copy = jobsText(lang)
  const query = useJobs({ session_id: sessionId, limit: 1 })
  const { enqueue, retry, invalidateMemory } = useJobActions()
  const [tracked, setTracked] = useState<string | null>(null)
  const notified = useRef<string | null>(null)
  const job = query.data?.jobs[0]
  const active = job?.status === 'pending' || job?.status === 'running'
  const busy = enqueue.isPending || retry.isPending || active
  const failure = enqueue.error || retry.error || query.error

  useEffect(() => {
    if (!job || active || `${job.id}:${job.attempt}` !== tracked || notified.current === tracked) return
    notified.current = tracked
    invalidateMemory()
    if (job.status === 'succeeded') toast.success(copy.result(job))
    else toast.error(copy.result(job))
    onComplete?.(job)
  }, [job, active, tracked, copy, invalidateMemory, onComplete])

  const submit = () => {
    const options = {
      onSuccess: (value: DistillationJob) => {
        setTracked(`${value.id}:${value.attempt}`)
        toast.info(value.deduplicated ? copy.duplicate : copy.submitted)
      },
      onError: (error: Error) => toast.error(error.message),
    }
    if (job?.status === 'failed') retry.mutate(job.id, options)
    else enqueue.mutate(sessionId, options)
  }

  return (
    <div className={`min-w-0 space-y-2 ${className ?? ''}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" loading={busy} disabled={!sessionId.trim() || query.isLoading} onClick={submit}>
          <CheckCircle2 className="h-4 w-4" />
          {active ? copy.states[job.status] : job?.status === 'failed' ? copy.retry : label || (job ? copy.again : copy.distill)}
        </Button>
        <Link className="text-xs text-brand-cyan hover:underline" to={`/jobs?session_id=${encodeURIComponent(sessionId)}`}>{copy.center}</Link>
      </div>
      {job && <p role="status" className={`break-words text-xs ${job.status === 'failed' ? 'text-brand-amber' : 'text-ghost'}`}>{copy.result(job)}</p>}
      {failure && <p role="alert" className="break-words text-xs text-brand-rose">{(failure as Error).message}</p>}
    </div>
  )
}

export default SessionDistillButton
