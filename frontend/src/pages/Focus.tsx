import { useEffect, useState } from 'react'
import { BellOff, Save, Star } from 'lucide-react'

import { Button, Card, CardTitle, ErrorState, PageHeader, Spinner } from '../components/ui'
import { TagInput } from '../components/TagInput'
import { toast } from '../components/Toast'
import { useFocus, useSetFocus } from '../hooks/queries'

export default function FocusPage() {
  const { data, isLoading, isError, error } = useFocus()
  const save = useSetFocus()

  const [track, setTrack] = useState<string[]>([])
  const [mute, setMute] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)

  // Sync from server unless the user has unsaved edits.
  useEffect(() => {
    if (data && !dirty) {
      setTrack(data.track)
      setMute(data.mute)
    }
  }, [data, dirty])

  if (isLoading) return <Spinner label="加载关注点…" />
  if (isError) return <ErrorState message={(error as Error).message} />

  const update = (which: 'track' | 'mute', next: string[]) => {
    setDirty(true)
    if (which === 'track') setTrack(next)
    else setMute(next)
  }

  const onSave = () =>
    save.mutate(
      { track, mute },
      {
        onSuccess: () => {
          setDirty(false)
          toast.success('已保存——重点话题已加权，屏蔽话题已隐藏')
        },
        onError: (e) => toast.error(String((e as Error).message)),
      },
    )

  return (
    <div className="space-y-6">
      <PageHeader
        title="关注点"
        subtitle="定制记忆的重点。这不是摆设——会真正改变检索排名与是否出现在回忆里。"
        actions={
          <Button onClick={onSave} loading={save.isPending} disabled={!dirty}>
            <Save className="h-4 w-4" /> 保存
          </Button>
        }
      />

      <Card>
        <CardTitle hint="提升 salience（检索打分 + 抗遗忘/驱逐）">
          <span className="inline-flex items-center gap-2">
            <Star className="h-4 w-4 text-brand-amber" /> 重点关注
          </span>
        </CardTitle>
        <p className="mb-3 text-sm text-ghost">
          这些话题的相关记忆会被<span className="text-slate-200">提升权重</span>：检索时排名更高、更不容易被遗忘或被挤出热区。
        </p>
        <TagInput tags={track} onChange={(n) => update('track', n)} placeholder="例如：工作、健康、家人、AI" tone="cyan" />
      </Card>

      <Card>
        <CardTitle hint="从回忆上下文与画像中剔除">
          <span className="inline-flex items-center gap-2">
            <BellOff className="h-4 w-4 text-brand-rose" /> 屏蔽不记
          </span>
        </CardTitle>
        <p className="mb-3 text-sm text-ghost">
          含这些词的事实<span className="text-slate-200">不会出现</span>在回忆上下文和用户画像里（数据仍保留，可随时取消屏蔽）。
        </p>
        <TagInput tags={mute} onChange={(n) => update('mute', n)} placeholder="例如：体重、某个旧项目" tone="rose" />
      </Card>
    </div>
  )
}
