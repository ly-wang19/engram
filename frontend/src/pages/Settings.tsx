import { useEffect, useState } from 'react'
import { ChevronDown, RotateCcw, Save, Sparkles, Wand2 } from 'lucide-react'

import { Badge, Button, Card, CardTitle, ErrorState, PageHeader, Spinner, cx } from '../components/ui'
import { toast } from '../components/Toast'
import { usePolicy, useSetPolicy } from '../hooks/queries'
import type { Policy } from '../types'

const PROMPT_FIELDS: Array<{ key: keyof Policy; label: string; hint: string }> = [
  { key: 'extract_system', label: '抽取提示词', hint: '决定从对话里抽出哪些原子事实（主语·谓语·宾语）' },
  { key: 'summary_system', label: '会话摘要提示词', hint: '决定每段会话如何被压缩成 L2 摘要' },
  { key: 'persona_system', label: '用户画像提示词', hint: '决定如何把事实合成为 L3 用户画像' },
]

export default function Settings() {
  const { data, isLoading, isError, error } = usePolicy()
  const save = useSetPolicy()

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

  if (isLoading) return <Spinner label="加载记忆策略…" />
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
        toast.success('已保存——下次记忆/整理时即按新策略执行')
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
        title="记忆策略"
        subtitle="自定义“要记录什么记忆”，以及抽取/摘要/画像的提示词。改动只作用于你自己，下次整理即生效。"
        actions={
          <Button onClick={onSave} loading={save.isPending} disabled={!dirty}>
            <Save className="h-4 w-4" /> 保存
          </Button>
        }
      />

      {/* headline: what to record */}
      <Card>
        <CardTitle hint="会追加到抽取提示词，作用于真实抽取">
          <span className="inline-flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-brand-cyan" /> 要记录哪些记忆
          </span>
        </CardTitle>
        <p className="mb-3 text-sm leading-relaxed text-ghost">
          用自然语言告诉记忆引擎：<span className="text-slate-200">重点记录什么、忽略什么</span>。例如
          “重点记录我的工作项目、健康数据、家人信息；忽略寒暄和无关闲聊”。
        </p>
        <textarea
          className="input min-h-[110px] resize-y leading-relaxed"
          placeholder={'例如：\n· 重点记录我的健身计划、体检指标、读书笔记\n· 不要记录天气、问候这类闲聊\n· 涉及金额时务必带上日期'}
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
            <Sparkles className="h-4 w-4" /> 高级：直接编辑提示词
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
                      {isDefault(f.key) ? <Badge>默认</Badge> : <Badge tone="violet">已自定义</Badge>}
                    </span>
                  </div>
                  <button
                    onClick={() => resetPrompt(f.key)}
                    disabled={isDefault(f.key)}
                    className="inline-flex items-center gap-1 text-xs text-ghost transition hover:text-brand-cyan disabled:opacity-40"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> 恢复默认
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
            <p className="text-xs text-ghost">
              提示：留为默认即可享受内置的高质量提示词；只有当你想改变抽取行为时才需要编辑。改坏了点“恢复默认”。
            </p>
          </div>
        )}
      </Card>
    </div>
  )
}
