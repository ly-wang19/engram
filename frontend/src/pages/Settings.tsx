import { useEffect, useState } from 'react'
import { ChevronDown, RotateCcw, Save, Sparkles, Wand2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, ErrorState, PageHeader, Spinner, cx } from '../components/ui'
import { toast } from '../components/Toast'
import { usePolicy, useSetPolicy } from '../hooks/queries'
import { useT } from '../i18n'
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
