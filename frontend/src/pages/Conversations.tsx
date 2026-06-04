import { useState } from 'react'
import { Hourglass, Send, Trash2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { toast } from '../components/Toast'
import { useClearWorking, useMemories, useRemember, useWorking } from '../hooks/queries'
import { useT } from '../i18n'

export default function Conversations() {
  const { data, isLoading, isError, error } = useMemories()
  const remember = useRemember()
  const working = useWorking()
  const clearWorking = useClearWorking()
  const t = useT()
  const [text, setText] = useState('')

  const submit = () => {
    const content = text.trim()
    if (!content) return
    remember.mutate(content, {
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
        <CardTitle>{t.common.saveNewMemory}</CardTitle>
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
                  clearWorking.mutate(undefined, {
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
        <CardTitle hint={t.conversations.episodesHint(data.episodes.length)}>{t.conversations.episodesTitle}</CardTitle>
        {data.episodes.length ? (
          <ul className="space-y-3">
            {data.episodes
              .slice()
              .reverse()
              .map((e, i) => (
                <li key={i} className="rounded-xl border border-line bg-white/[0.02] p-4">
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] text-ghost">
                    <span className="rounded bg-white/5 px-1.5 py-px tabular-nums text-brand-cyan">{e.date}</span>
                    <span className="rounded bg-white/5 px-1.5 py-px font-mono">{e.session}</span>
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{e.content}</p>
                  {e.summary && (
                    <p className="mt-2 border-l-2 border-brand-violet/40 pl-3 text-xs leading-relaxed text-ghost">
                      {t.conversations.summaryPrefix}
                      {e.summary}
                    </p>
                  )}
                </li>
              ))}
          </ul>
        ) : (
          <EmptyState title={t.conversations.emptyTitle} hint={t.conversations.emptyHint} />
        )}
      </Card>
    </div>
  )
}
