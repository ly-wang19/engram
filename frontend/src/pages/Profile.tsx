import { Check, HelpCircle, Lock, ThumbsDown, ThumbsUp, Trash2, UserRound } from 'lucide-react'

import { Badge, Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner, cx } from '../components/ui'
import { toast } from '../components/Toast'
import { useAddFact, useDeleteFact, useStructuredProfile } from '../hooks/queries'
import type { Evidence, ProfileItem } from '../types'

function evidenceLabel(e: Evidence): string {
  if (e.kind === 'user') return '你设定'
  if (e.kind === 'reinforced') return '已强化'
  return e.count > 1 ? `${e.count} 个会话提到` : '提及一次'
}

function EvidenceChip({ e }: { e: Evidence }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px]',
        e.kind === 'user' ? 'bg-brand-amber/15 text-brand-amber' : 'bg-white/5 text-ghost',
      )}
      title="证据来源（诚实标注，非编造权重）"
    >
      {e.kind === 'user' && <Lock className="h-2.5 w-2.5" />}
      {evidenceLabel(e)}
    </span>
  )
}

function PrefChip({ it }: { it: ProfileItem }) {
  const like = it.polarity === 'like'
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm',
        like ? 'bg-brand-mint/12 text-brand-mint' : 'bg-brand-rose/12 text-brand-rose',
      )}
    >
      {like ? <ThumbsUp className="h-3.5 w-3.5" /> : <ThumbsDown className="h-3.5 w-3.5" />}
      <span className="text-slate-100">{it.item}</span>
      <EvidenceChip e={it.evidence} />
    </span>
  )
}

export default function Profile() {
  const { data, isLoading, isError, error } = useStructuredProfile()
  const addFact = useAddFact()
  const deleteFact = useDeleteFact()

  if (isLoading) return <Spinner label="构建结构化画像…" />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const confirm = (it: ProfileItem) =>
    addFact.mutate(
      { subject: it.subject, predicate: it.predicate, object: it.object },
      {
        onSuccess: () => toast.success(`已确认：${it.item}（升为你设定 🔒）`),
        onError: (e) => toast.error(String((e as Error).message)),
      },
    )

  const dismiss = (it: ProfileItem) =>
    deleteFact.mutate(it.fact_id, {
      onSuccess: () => toast.success('已删除该候选'),
      onError: (e) => toast.error(String((e as Error).message)),
    })

  const prefCats = Object.entries(data.preferences)
  const empty = data.counts.basic + data.counts.preferences + data.counts.tentative + data.counts.habits === 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="用户画像"
        subtitle="从记忆里归纳的结构化画像。分「已确认」与「待确认」——待确认的不进画像、也不影响回忆，只等你拍板。"
      />

      {empty && <EmptyState title="画像还在形成中" hint="多聊几句或补几条事实，画像会自动归纳。" icon={<UserRound className="h-6 w-6" />} />}

      {/* basic info */}
      {data.basic.length > 0 && (
        <Card>
          <CardTitle hint="单值身份槽位">基本信息</CardTitle>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            {data.basic.map((b) => (
              <div key={b.field}>
                <div className="text-xs text-ghost">{b.label}</div>
                <div className="mt-0.5 flex items-center gap-2">
                  <span className="text-sm text-slate-100">{b.value}</span>
                  <EvidenceChip e={b.evidence} />
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* preferences by category */}
      {prefCats.length > 0 && (
        <Card>
          <CardTitle hint="已确认 · 含喜好/厌恶极性，不编权重">偏好</CardTitle>
          <div className="space-y-4">
            {prefCats.map(([cat, items]) => (
              <div key={cat}>
                <div className="mb-2 text-xs font-medium uppercase tracking-wider text-brand-cyan">{cat}</div>
                <div className="flex flex-wrap gap-2">
                  {items.map((it) => (
                    <PrefChip key={it.fact_id} it={it} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* habits */}
      {data.habits.length > 0 && (
        <Card>
          <CardTitle hint="行为/习惯">习惯</CardTitle>
          <ul className="space-y-1.5">
            {data.habits.map((h) => (
              <li key={h.fact_id} className="flex items-center gap-2 text-sm text-slate-200">
                <span className="h-1.5 w-1.5 rounded-full bg-brand-violet" />
                {h.text}
                <EvidenceChip e={h.evidence} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* tentative — the L1→L2 gate, display-only */}
      {data.tentative.length > 0 && (
        <Card>
          <CardTitle hint="单次提及，未进画像、不影响回忆">
            <span className="inline-flex items-center gap-2">
              <HelpCircle className="h-4 w-4 text-brand-amber" /> 待确认候选 · {data.tentative.length}
            </span>
          </CardTitle>
          <p className="mb-3 text-sm text-ghost">
            这些是只提到一次的推断，<span className="text-slate-200">还没进结构化画像</span>（但仍存在记忆里、回忆时照常能用）。确认后升为「你设定」🔒。
          </p>
          <ul className="divide-y divide-line">
            {data.tentative.map((it) => (
              <li key={it.fact_id} className="flex items-center gap-3 py-2.5">
                <Badge tone={it.polarity === 'like' ? 'live' : 'old'}>{it.polarity === 'like' ? '喜欢' : '不喜欢'}</Badge>
                <span className="flex-1 text-sm text-slate-100">
                  {it.item} <span className="text-xs text-ghost">· {it.category}</span>
                </span>
                <EvidenceChip e={it.evidence} />
                <button
                  onClick={() => confirm(it)}
                  className="inline-flex items-center gap-1 rounded-lg border border-brand-mint/30 bg-brand-mint/10 px-2.5 py-1 text-xs text-brand-mint transition hover:bg-brand-mint/20"
                >
                  <Check className="h-3.5 w-3.5" /> 确认
                </button>
                <button
                  onClick={() => dismiss(it)}
                  className="rounded-lg border border-line p-1.5 text-ghost transition hover:border-brand-rose/50 hover:text-brand-rose"
                  title="删除候选"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
