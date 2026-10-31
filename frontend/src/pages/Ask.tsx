import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Button, Card, EmptyState, ErrorState, PageHeader, Spinner } from '../components/ui'
import { useLang, useT } from '../i18n'
import { useAuth } from '../store/auth'
import { toast } from '../components/Toast'
import { evidenceApi, type AskInput } from '../features/evidence/api'
import { SourcePanel } from '../features/evidence/SourcePanel'

export default function Ask() {
  const key = useAuth(s => s.apiKey)
  return <AskPage key={key} />
}

function AskPage() {
  const t = useT()
  const zh = useLang(s => s.lang) === 'zh'
  const key = useAuth(s => s.apiKey)
  const [query, setQuery] = useState('')
  const [chunks, setChunks] = useState(6)
  const [asOf, setAsOf] = useState('')
  const [redact, setRedact] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [showContext, setShowContext] = useState(false)
  const ask = useMutation({ mutationKey: ['evidence-ask', key, redact, asOf, chunks],
    mutationFn: (input: AskInput) => evidenceApi.ask(input) })
  const reset = () => { ask.reset(); setSelected(null); setShowContext(false) }
  const submit = (e: FormEvent) => {
    e.preventDefault(); setSelected(null); setShowContext(false)
    if (query.trim()) ask.mutate({ query: query.trim(), n_chunks: chunks, redact_sensitive: redact,
      as_of: asOf ? new Date(`${asOf}T23:59:59`).getTime() / 1000 : undefined })
  }
  const result = ask.isPending ? undefined : ask.data
  const messages = {
    no_evidence: zh ? '没有可用于回答的证据。' : 'No evidence is available for this question.',
    unavailable: zh ? '回答模型尚未配置；下方是已检索的证据，不是生成的答案。' : 'No answer model is configured. Retrieved evidence is available below.',
    error: zh ? '回答模型调用失败，请重试。' : 'The answer model failed. Please retry.',
    invalid_citations: zh ? '模型没有返回有效引用，已隐藏该回答。请重试。' : 'The model returned invalid citations. Its answer was withheld. Please retry.',
    stale_evidence: zh ? '生成期间记忆发生了更改，请重新提问。' : 'Memory changed during generation. Please ask again.',
    abstained: zh ? '已有证据不足以回答这个问题。' : 'The available evidence does not answer this question.',
    completed: '',
  }
  return <div className="min-w-0 space-y-6">
    <PageHeader title={t.ask.title} subtitle={zh ? '每条回答都能定位到实际使用的事实或原文。' : 'Trace each answer to the facts or original sources it used.'} />
    <Card><form onSubmit={submit} className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row"><input className="input min-w-0 flex-1" aria-label={t.ask.placeholder}
        value={query} onChange={e => setQuery(e.target.value)} placeholder={t.ask.placeholder} />
        <Button type="submit" disabled={!query.trim()} loading={ask.isPending}>{t.ask.searchBtn}</Button></div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-ghost">
        <label className="flex items-center gap-2">{t.ask.chunksLabel}<input className="input w-16" type="number" min={1} max={12} value={chunks}
          onChange={e => { setChunks(Math.max(1, Math.min(12, Number(e.target.value) || 1))); reset() }} /></label>
        <label className="flex items-center gap-2">{t.ask.asOfLabel}<input className="input max-w-full" type="date" value={asOf}
          onChange={e => { setAsOf(e.target.value); reset() }} /></label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={redact} onChange={e => { setRedact(e.target.checked); reset() }} />{t.ask.redactSensitive}</label>
      </div>
      <p className="text-xs text-ghost">{zh ? '脱敏时不发送原文或敏感事实。查看原文需要关闭脱敏并重新提问。' : 'Redaction excludes raw conversations and sensitive facts. Disable it and ask again to inspect original sources.'}</p>
      <div className="flex flex-wrap gap-2">{t.ask.examples.map(ex => <button type="button" className="rounded-lg border border-line px-2 py-1 text-xs text-brand-cyan" key={ex} onClick={() => setQuery(ex)}>{ex}</button>)}</div>
    </form></Card>
    {ask.isPending && <Spinner label={t.ask.searching} />}
    {ask.isError && <ErrorState message={ask.error.message} />}
    {result && <>
      {messages[result.answer_status] && <Card><p role="status">{messages[result.answer_status]}</p></Card>}
      {!!result.claims.length && <Card className="!border-brand-mint/30">
        <h2 className="mb-3 text-sm font-semibold text-brand-mint">{t.ask.answer}</h2>
        <div className="space-y-3">{result.claims.map((claim, i) => <p key={i} className="whitespace-pre-wrap break-words leading-relaxed">{claim.text}{' '}
          {claim.evidence_ids.map(id => { const item = result.citations.find(e => e.id === id)
            return item ? <button type="button" key={id} className="mx-1 rounded border border-brand-cyan/40 px-1.5 text-sm text-brand-cyan" onClick={() => setSelected(item.source_token)} aria-label={`${zh ? '查看证据' : 'View evidence'} ${id}`}>[{id}]</button> : null })}
        </p>)}</div>
      </Card>}
      {selected && <SourcePanel key={`${selected}:${redact}`} token={selected} sensitive={!result.redacted_sensitive}
        onClose={() => setSelected(null)} onCorrected={() => { reset(); toast.success(zh ? '事实已更正，请重新提问。' : 'Fact corrected. Please ask again.') }} />}
      {!!result.citations.length && <Card>
        <h2 className="mb-2 font-semibold">{zh ? '回答引用的证据' : 'Cited evidence'}</h2>
        <div className="space-y-2">{result.citations.map(item => <button key={item.id} type="button" className="block w-full min-w-0 rounded-xl border border-line p-3 text-left hover:border-brand-cyan/50" onClick={() => setSelected(item.source_token)}>
          <span className="text-xs text-brand-cyan">[{item.id}] · {item.kind === 'fact' ? (zh ? '事实' : 'Fact') : (zh ? '原文' : 'Original source')}</span>
          <p className="mt-1 whitespace-pre-wrap break-words text-sm">{item.text}</p>
          {item.truncated && <span className="text-xs text-ghost">{zh ? '回答只使用了以上节选；点击查看完整出处。' : 'Only this excerpt was supplied. Click to inspect the full source.'}</span>}
        </button>)}</div>
      </Card>}
      {!!result.context_evidence.length && <Card>
        <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm text-ghost">{zh ? '本次送入回答的上下文' : 'Context supplied for this answer'} · ~{result.tokens_est} tokens</span>
          <Button variant="ghost" onClick={() => setShowContext(!showContext)}>{showContext ? t.ask.evidenceHide : t.ask.evidenceShow}</Button></div>
        <p className="mt-2 text-xs text-ghost">{zh ? '上下文中的候选证据不一定被答案引用；引用以回答旁的编号为准。' : 'Context candidates may be unused. Cited evidence is identified beside each answer claim.'}</p>
        {showContext && <div className="mt-3 space-y-3">{result.context_evidence.map(item => <section key={item.id} className="min-w-0 rounded-lg border border-line p-3">
          <button type="button" className="text-sm text-brand-cyan" onClick={() => setSelected(item.source_token)}>[{item.id}] {zh ? '定位出处 / 纠错' : 'View source / correct'}</button>
          <pre className="mt-2 whitespace-pre-wrap break-words text-sm">{item.text}</pre>
        </section>)}</div>}
      </Card>}
    </>}
    {!result && !ask.isPending && !ask.isError && <EmptyState title={t.ask.emptyTitle} hint={t.ask.emptyHint} />}
  </div>
}
