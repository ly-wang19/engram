import { useEffect, useState } from 'react'
import {
  CheckCircle2,
  ChevronDown,
  Clipboard,
  ExternalLink,
  PlugZap,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  Terminal,
  Wand2,
} from 'lucide-react'

import { Badge, Button, Card, CardTitle, ErrorState, PageHeader, Spinner, cx } from '../components/ui'
import { toast } from '../components/Toast'
import { useAgentStatus, useHealth, usePolicy, useSessions, useSetPolicy } from '../hooks/queries'
import { useT } from '../i18n'
import { apiBaseUrl } from '../lib/api'
import { copyText } from '../lib/clipboard'
import { useAuth } from '../store/auth'
import type { Policy } from '../types'

export default function Settings() {
  const { data, isLoading, isError, error } = usePolicy()
  const save = useSetPolicy()
  const t = useT()

  // Built inside the component so the labels follow the active language.
  const PROMPT_FIELDS: Array<{ key: keyof Policy; label: string; hint: string }> = [
    { key: 'extract_system', label: t.settings.promptExtractLabel, hint: t.settings.promptExtractHint },
    { key: 'summary_system', label: t.settings.promptSummaryLabel, hint: t.settings.promptSummaryHint },
    { key: 'persona_system', label: t.settings.promptPersonaLabel, hint: t.settings.promptPersonaHint },
  ]

  const [instruction, setInstruction] = useState('')
  const [prompts, setPrompts] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [advanced, setAdvanced] = useState(false)

  useEffect(() => {
    if (data && !dirty) {
      setInstruction(data.policy.extract_instruction || '')
      setPrompts({
        extract_system: data.policy.extract_system || data.defaults.extract_system,
        summary_system: data.policy.summary_system || data.defaults.summary_system,
        persona_system: data.policy.persona_system || data.defaults.persona_system,
      })
    }
  }, [data, dirty])

  if (isLoading) return <Spinner label={t.settings.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const isDefault = (key: keyof Policy) => prompts[key] === data.defaults[key]

  const onSave = () => {
    // Send "" for any prompt left at its default so it stays "use default" rather than a frozen override.
    const patch: Partial<Policy> = {
      extract_instruction: instruction,
      extract_system: isDefault('extract_system') ? '' : prompts.extract_system,
      summary_system: isDefault('summary_system') ? '' : prompts.summary_system,
      persona_system: isDefault('persona_system') ? '' : prompts.persona_system,
    }
    save.mutate(patch, {
      onSuccess: () => {
        setDirty(false)
        toast.success(t.settings.saved)
      },
      onError: (e) => toast.error(String((e as Error).message)),
    })
  }

  const resetPrompt = (key: keyof Policy) => {
    setDirty(true)
    setPrompts((p) => ({ ...p, [key]: data.defaults[key] }))
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t.settings.title}
        subtitle={t.settings.subtitle}
        actions={
          <Button onClick={onSave} loading={save.isPending} disabled={!dirty}>
            <Save className="h-4 w-4" /> {t.common.save}
          </Button>
        }
      />

      <CodexSetupCard />

      {/* headline: what to record */}
      <Card>
        <CardTitle hint={t.settings.recordHint}>
          <span className="inline-flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-brand-cyan" /> {t.settings.recordTitle}
          </span>
        </CardTitle>
        <p className="mb-3 text-sm leading-relaxed text-ghost">
          {t.settings.recordDescPre}
          <span className="text-slate-200">{t.settings.recordDescEmph}</span>
          {t.settings.recordDescPost}
        </p>
        <textarea
          className="input min-h-[110px] resize-y leading-relaxed"
          placeholder={t.settings.recordPlaceholder}
          value={instruction}
          onChange={(e) => {
            setDirty(true)
            setInstruction(e.target.value)
          }}
        />
      </Card>

      {/* advanced: full prompt editing */}
      <Card>
        <button
          className="flex w-full items-center justify-between"
          onClick={() => setAdvanced((v) => !v)}
        >
          <span className="inline-flex items-center gap-2 text-sm font-semibold text-brand-violet">
            <Sparkles className="h-4 w-4" /> {t.settings.advancedToggle}
          </span>
          <ChevronDown className={cx('h-4 w-4 text-ghost transition', advanced && 'rotate-180')} />
        </button>

        {advanced && (
          <div className="mt-5 space-y-6">
            {PROMPT_FIELDS.map((f) => (
              <div key={f.key}>
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <div>
                    <span className="text-sm font-medium text-slate-100">{f.label}</span>
                    <span className="ml-2">
                      {isDefault(f.key) ? <Badge>{t.settings.defaultBadge}</Badge> : <Badge tone="violet">{t.settings.customBadge}</Badge>}
                    </span>
                  </div>
                  <button
                    onClick={() => resetPrompt(f.key)}
                    disabled={isDefault(f.key)}
                    className="inline-flex items-center gap-1 text-xs text-ghost transition hover:text-brand-cyan disabled:opacity-40"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> {t.settings.resetDefault}
                  </button>
                </div>
                <p className="mb-2 text-xs text-ghost">{f.hint}</p>
                <textarea
                  className="input min-h-[140px] resize-y font-mono text-[12.5px] leading-relaxed"
                  value={prompts[f.key] ?? ''}
                  onChange={(e) => {
                    setDirty(true)
                    setPrompts((p) => ({ ...p, [f.key]: e.target.value }))
                  }}
                />
              </div>
            ))}
            <p className="text-xs text-ghost">{t.settings.advancedNote}</p>
          </div>
        )}
      </Card>
    </div>
  )
}

function CodexSetupCard() {
  const t = useT()
  const apiKey = useAuth((s) => s.apiKey) || ''
  const [sessionId, setSessionId] = useState('codex:engram:onboarding')
  const apiUrl = apiBaseUrl()
  const uiBase = typeof window === 'undefined' ? apiUrl : window.location.origin
  const health = useHealth()
  const status = useAgentStatus(sessionId)
  const sessions = useSessions({ q: 'codex', limit: 5 })
  const codexSessions = sessions.data?.sessions ?? []
  const hasCodexWrites = codexSessions.length > 0

  const command = [
    'python3 -m pip install -U "engram-memory[mcp] @ git+https://github.com/ly-wang19/engram.git"',
    [
      'python3 -m engram.agent_setup',
      '--client codex',
      '--api-url',
      sh(apiUrl),
      '--api-key',
      sh(apiKey),
      '--python "$(command -v python3)"',
      '--install-codex',
      '--doctor',
    ].join(' '),
  ].join(' && \\\n  ')

  const statusPrompt = t.settings.connectStatusPrompt(sessionId)
  const rememberPrompt = t.settings.connectRememberPrompt(sessionId)
  const closePrompt = t.settings.connectClosePrompt(sessionId)

  return (
    <Card>
      <CardTitle hint={t.settings.connectHint}>
        <span className="inline-flex items-center gap-2">
          <PlugZap className="h-4 w-4 text-brand-mint" /> {t.settings.connectTitle}
        </span>
      </CardTitle>

      {/* minmax(0,…): arbitrary fr values default to minmax(auto,…), so the long install command's
          min-content width would lock the column open and force page-level horizontal scroll. */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
        <div className="min-w-0 space-y-4">
          <p className="text-sm leading-relaxed text-ghost">
            {t.settings.connectDescPre}
            <span className="text-slate-200">{t.settings.connectDescEmph}</span>
            {t.settings.connectDescPost}
          </p>

          <div className="grid gap-3 sm:grid-cols-2">
            <StatusTile
              label={t.settings.connectApiLabel}
              value={health.data?.ready ? t.settings.connectReady : t.settings.connectChecking}
              ok={!!health.data?.ready}
            />
            <StatusTile
              label={t.settings.connectCodexSessions}
              value={
                sessions.isLoading
                  ? t.settings.connectChecking
                  : hasCodexWrites
                    ? t.settings.connectDetected(codexSessions.length)
                    : t.settings.connectNoneYet
              }
              ok={hasCodexWrites}
            />
          </div>

          <label className="block">
            <span className="label">{t.settings.connectSessionLabel}</span>
            <input
              className="input mt-1.5"
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
              placeholder="codex:project:thread"
            />
          </label>

          <div className="rounded-xl border border-line bg-white/[0.035] p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="inline-flex items-center gap-2 text-sm font-semibold text-slate-100">
                <Terminal className="h-4 w-4 text-brand-cyan" /> {t.settings.connectStepInstall}
              </div>
              <Button
                variant="ghost"
                aria-label={t.settings.connectCopyCommand}
                onClick={() => copy(command, t.common.copied, t.common.copyFailed)}
              >
                <Clipboard className="h-4 w-4" /> {t.settings.connectCopyCommand}
              </Button>
            </div>
            <pre className="max-h-52 overflow-auto rounded-lg bg-ink-900/70 p-3 text-xs leading-relaxed text-slate-200">
              <code>{command}</code>
            </pre>
            <p className="mt-2 flex items-start gap-2 text-xs leading-relaxed text-ghost">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-amber" />
              <span>{t.settings.connectSecretNote}</span>
            </p>
          </div>
        </div>

        <div className="min-w-0 space-y-4">
          <div className="rounded-xl border border-line bg-white/[0.035] p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="inline-flex items-center gap-2 text-sm font-semibold text-slate-100">
                <CheckCircle2 className="h-4 w-4 text-brand-mint" /> {t.settings.connectStepTest}
              </div>
              <Button
                variant="ghost"
                onClick={() => {
                  status.refetch()
                  sessions.refetch()
                }}
              >
                <RefreshCw className="h-4 w-4" /> {t.settings.connectRefresh}
              </Button>
            </div>
            <PromptCopy label={t.settings.connectPromptStatus} text={statusPrompt} />
            <PromptCopy label={t.settings.connectPromptRemember} text={rememberPrompt} />
            <PromptCopy label={t.settings.connectPromptClose} text={closePrompt} />
          </div>

          <div className="rounded-xl border border-line bg-white/[0.035] p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="text-sm font-semibold text-slate-100">{t.settings.connectLiveTitle}</div>
              <Badge tone={status.data?.ok ? 'live' : 'default'}>
                {status.data?.ok ? t.settings.connectReady : t.settings.connectChecking}
              </Badge>
            </div>
            <div className="space-y-2 text-sm text-ghost">
              <div className="flex justify-between gap-3">
                <span>{t.settings.connectNamespace}</span>
                <span className="truncate text-right text-slate-200">{status.data?.user ?? apiKey}</span>
              </div>
              <div className="flex justify-between gap-3">
                <span>{t.settings.connectSessionEpisodes}</span>
                <span className="text-slate-200">{status.data?.session?.episodes ?? 0}</span>
              </div>
              <div className="flex justify-between gap-3">
                <span>{t.settings.connectLiveFacts}</span>
                <span className="text-slate-200">{status.data?.counts?.facts_live ?? 0}</span>
              </div>
            </div>
            {codexSessions.length > 0 && (
              <div className="mt-4 space-y-2">
                <div className="text-xs font-medium uppercase text-ghost">{t.settings.connectRecentSessions}</div>
                {codexSessions.map((row) => (
                  <div key={row.id} className="rounded-lg bg-white/[0.04] px-3 py-2 text-xs text-ghost">
                    <div className="truncate font-medium text-slate-200">{row.id}</div>
                    <div className="mt-1 flex flex-wrap gap-2">
                      <span>{t.conversations.sessionCardEpisodes(row.episodes)}</span>
                      <span>{t.conversations.sessionCardFacts(row.facts_added)}</span>
                      {row.last_event_at_h && <span>{t.conversations.sessionCardLast(row.last_event_at_h)}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <a
              href={`${uiBase}/ui/conversations`}
              className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-cyan hover:text-brand-mint"
            >
              {t.settings.connectOpenSessions} <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        </div>
      </div>
    </Card>
  )
}

function StatusTile({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="rounded-xl border border-line bg-white/[0.035] p-3">
      <div className="text-xs text-ghost">{label}</div>
      <div className={cx('mt-1 flex items-center gap-2 text-sm font-semibold', ok ? 'text-brand-mint' : 'text-slate-200')}>
        {ok && <CheckCircle2 className="h-4 w-4" />}
        {value}
      </div>
    </div>
  )
}

function PromptCopy({ label, text }: { label: string; text: string }) {
  const t = useT()
  return (
    <div className="mb-3 last:mb-0">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-ghost">{label}</span>
        <button
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-brand-cyan transition hover:bg-white/5 hover:text-brand-mint"
          aria-label={`${t.settings.connectCopyPrompt}: ${label}`}
          onClick={() => copy(text, t.common.copied, t.common.copyFailed)}
        >
          <Clipboard className="h-3.5 w-3.5" /> {t.settings.connectCopyPrompt}
        </button>
      </div>
      <div className="rounded-lg bg-ink-900/70 p-3 text-xs leading-relaxed text-slate-200">{text}</div>
    </div>
  )
}

async function copy(text: string, successMessage: string, errorMessage: string) {
  try {
    await copyText(text)
    toast.success(successMessage)
  } catch {
    toast.error(errorMessage)
  }
}

function sh(value: string) {
  return `'${value.replace(/'/g, "'\\''")}'`
}
