import { useState, type FormEvent } from 'react'
import { MessageSquare, Search, Sparkles, Zap } from 'lucide-react'

import { Button, Card, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { useRecall } from '../hooks/queries'
import { useT } from '../i18n'
import { copyText } from '../lib/clipboard'
import type { Dict } from '../i18n/en'
import { toast } from '../components/Toast'

// The lean-context blocks (engram.memory.lean_context) are emitted with English ALL-CAPS
// headers regardless of UI language; we map each header to a localized section title.
type SectionKey = Extract<
  keyof Dict['ask'],
  'sectionUserProfile' | 'sectionFacts' | 'sectionTimeline' | 'sectionSummaries' | 'sectionConversations'
>
const SECTION_KEYS: Array<[RegExp, SectionKey]> = [
  [/^USER PROFILE/i, 'sectionUserProfile'],
  [/^FACTS/i, 'sectionFacts'],
  [/^TIMELINE/i, 'sectionTimeline'],
  [/^SESSION SUMMARIES/i, 'sectionSummaries'],
  [/^RELEVANT CONVERSATIONS/i, 'sectionConversations'],
]

function splitBlocks(context: string) {
  return context
    .split(/\n\n(?=[A-Z][A-Z ]+(?:\([^)]*\))?:)/)
    .map((block) => {
      const nl = block.indexOf('\n')
      const head = nl === -1 ? block : block.slice(0, nl)
      const body = nl === -1 ? '' : block.slice(nl + 1)
      const key = SECTION_KEYS.find(([re]) => re.test(head))?.[1]
      // `rawLabel` is the server's own header — used as-is when no localized title matches.
      return { key, rawLabel: head.replace(/:$/, ''), body: body.trim() || head }
    })
    .filter((b) => b.body)
}

export default function Ask() {
  const recall = useRecall()
  const t = useT()
  const [query, setQuery] = useState('')
  const [chunks, setChunks] = useState(6)
  const [asOf, setAsOf] = useState('')
  const [redactSensitive, setRedactSensitive] = useState(true)
  const [showEvidence, setShowEvidence] = useState(false)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const q = query.trim()
    if (q) {
      recall.mutate({
        query: q,
        n_chunks: chunks,
        as_of: asOf ? new Date(`${asOf}T23:59:59`).getTime() / 1000 : undefined,
        redact_sensitive: redactSensitive,
      })
      setShowEvidence(false)
    }
  }

  const result = recall.data
  const blocks = result ? splitBlocks(result.context) : []

  return (
    <div className="space-y-6">
      <PageHeader title={t.ask.title} subtitle={t.ask.subtitle} />

      <Card>
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3 focus-within:border-brand-cyan/60 focus-within:ring-2 focus-within:ring-brand-cyan/20">
            <Search className="h-4 w-4 text-ghost" />
            <input
              aria-label={t.ask.placeholder}
              className="w-full bg-transparent py-2.5 text-sm outline-none placeholder:text-ghost/70"
              placeholder={t.ask.placeholder}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Button type="submit" loading={recall.isPending} disabled={!query.trim()}>
            {t.ask.searchBtn}
          </Button>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-ghost">
            <label className="flex items-center gap-2">
              {t.ask.chunksLabel}
              <input
                className="w-16 rounded-lg border border-line bg-white/[0.05] px-2 py-1 text-slate-100 outline-none"
                type="number"
                min={1}
                max={12}
                value={chunks}
                onChange={(e) => setChunks(Math.max(1, Math.min(12, Number(e.target.value) || 1)))}
              />
            </label>
            <label className="flex items-center gap-2">
              {t.ask.asOfLabel}
              <input
                className="rounded-lg border border-line bg-white/[0.05] px-2 py-1 text-slate-100 outline-none"
                type="date"
                value={asOf}
                onChange={(e) => setAsOf(e.target.value)}
              />
            </label>
            <label className="flex select-none items-center gap-2">
              <input type="checkbox" checked={redactSensitive} onChange={(e) => setRedactSensitive(e.target.checked)} className="accent-brand-rose" />
              {t.ask.redactSensitive}
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="self-center text-xs text-ghost">{t.ask.examplesTitle}</span>
            {t.ask.examples.map((ex) => (
              <button
                key={ex}
                type="button"
                className="rounded-lg border border-line bg-white/[0.04] px-2.5 py-1 text-xs text-brand-cyan transition hover:border-brand-cyan/50"
                onClick={() => setQuery(ex)}
              >
                {ex}
              </button>
            ))}
          </div>
        </form>
      </Card>

      {recall.isPending && <Spinner label={t.ask.searching} />}
      {recall.isError && <ErrorState message={(recall.error as Error).message} />}

      {result && (
        <>
          {result.answer && (
            <Card className="!border-brand-mint/30 !bg-brand-mint/[0.06]">
              <div className="mb-1.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-brand-mint">
                <MessageSquare className="h-4 w-4" /> {t.ask.answer}
              </div>
              <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-slate-100">{result.answer}</p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <p className="flex-1 text-xs text-ghost">{t.ask.answerNote}</p>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    try {
                      await copyText(result.answer || '')
                      toast.success(t.common.copied)
                    } catch {
                      toast.error(t.common.copyFailed)
                    }
                  }}
                >
                  {t.ask.copyAnswer}
                </Button>
              </div>
            </Card>
          )}

          <div className="rounded-xl border border-brand-cyan/20 bg-brand-cyan/5 p-4">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
              <span className="inline-flex items-center gap-2">
                <Zap className="h-5 w-5 text-brand-cyan" />
                <span className="font-semibold text-brand-cyan">{t.ask.lean(result.tokens_est)}</span>
              </span>
              {result.full_tokens != null && (
                <span className="text-ghost">
                  {t.ask.fullLabel} <span className="text-slate-200">{result.full_tokens.toLocaleString()}</span> tokens
                </span>
              )}
              {result.full_tokens != null && result.full_tokens > result.tokens_est && (
                <span className="rounded-md bg-brand-mint/15 px-2 py-0.5 font-semibold text-brand-mint">
                  {t.ask.savings(
                    (result.full_tokens / Math.max(1, result.tokens_est)).toFixed(1),
                    Math.max(1, Math.round((result.tokens_est / result.full_tokens) * 100)),
                  )}
                </span>
              )}
            </div>
            <p className="mt-1.5 text-xs text-ghost">{t.ask.leanNote}</p>
          </div>

          {blocks.length ? (
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-brand-cyan">{t.ask.evidenceTitle}</div>
                  <p className="mt-1 text-xs text-ghost">{t.ask.leanNote}</p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    onClick={async () => {
                      try {
                        await copyText(result.context)
                        toast.success(t.common.copied)
                      } catch {
                        toast.error(t.common.copyFailed)
                      }
                    }}
                  >
                    {t.ask.copyContext}
                  </Button>
                  <Button variant="ghost" onClick={() => setShowEvidence((v) => !v)}>
                    {showEvidence ? t.ask.evidenceHide : t.ask.evidenceShow}
                  </Button>
                </div>
              </div>
              {showEvidence && (
              <div className="mt-4 gap-4 md:columns-2 xl:columns-3">
              {blocks.map((b, i) => (
                <section key={i} className="mb-4 break-inside-avoid rounded-xl border border-line bg-white/[0.03] p-4">
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-brand-cyan">
                    {b.key ? t.ask[b.key] : b.rawLabel}
                  </div>
                  <pre className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{b.body}</pre>
                </section>
              ))}
              </div>
              )}
            </Card>
          ) : (
            <EmptyState title={t.ask.emptyTitle} hint={t.ask.emptyHint} icon={<Sparkles className="h-6 w-6" />} />
          )}
        </>
      )}
    </div>
  )
}
