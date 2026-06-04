import { Check, HelpCircle, Lock, ThumbsDown, ThumbsUp, Trash2, UserRound } from 'lucide-react'

import { Badge, Card, CardTitle, EmptyState, ErrorState, PageHeader, Spinner, cx } from '../components/ui'
import { toast } from '../components/Toast'
import { useAddFact, useDeleteFact, useStructuredProfile } from '../hooks/queries'
import { useT } from '../i18n'
import type { Dict } from '../i18n/en'
import type { Evidence, ProfileItem } from '../types'

function evidenceLabel(e: Evidence, t: Dict): string {
  if (e.kind === 'user') return t.profile.evidenceUser
  if (e.kind === 'reinforced') return t.profile.evidenceReinforced
  return e.count > 1 ? t.profile.evidenceMentions(e.count) : t.profile.evidenceOnce
}

function EvidenceChip({ e }: { e: Evidence }) {
  const t = useT()
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px]',
        e.kind === 'user' ? 'bg-brand-amber/15 text-brand-amber' : 'bg-white/5 text-ghost',
      )}
      title={t.profile.evidenceTitle}
    >
      {e.kind === 'user' && <Lock className="h-2.5 w-2.5" />}
      {evidenceLabel(e, t)}
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
  const t = useT()

  if (isLoading) return <Spinner label={t.profile.loading} />
  if (isError) return <ErrorState message={(error as Error).message} />
  if (!data) return null

  const confirm = (it: ProfileItem) =>
    addFact.mutate(
      { subject: it.subject, predicate: it.predicate, object: it.object },
      {
        onSuccess: () => toast.success(t.profile.confirmed(it.item)),
        onError: (e) => toast.error(String((e as Error).message)),
      },
    )

  const dismiss = (it: ProfileItem) =>
    deleteFact.mutate(it.fact_id, {
      onSuccess: () => toast.success(t.profile.dismissed),
      onError: (e) => toast.error(String((e as Error).message)),
    })

  const prefCats = Object.entries(data.preferences)
  const empty = data.counts.basic + data.counts.preferences + data.counts.tentative + data.counts.habits === 0

  return (
    <div className="space-y-6">
      <PageHeader title={t.profile.title} subtitle={t.profile.subtitle} />

      {empty && <EmptyState title={t.profile.emptyTitle} hint={t.profile.emptyHint} icon={<UserRound className="h-6 w-6" />} />}

      {/* basic info */}
      {data.basic.length > 0 && (
        <Card>
          <CardTitle hint={t.profile.basicHint}>{t.profile.basicTitle}</CardTitle>
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
          <CardTitle hint={t.profile.prefsHint}>{t.profile.prefsTitle}</CardTitle>
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
          <CardTitle hint={t.profile.habitsHint}>{t.profile.habitsTitle}</CardTitle>
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
          <CardTitle hint={t.profile.tentativeHint}>
            <span className="inline-flex items-center gap-2">
              <HelpCircle className="h-4 w-4 text-brand-amber" /> {t.profile.tentativeTitle(data.tentative.length)}
            </span>
          </CardTitle>
          <p className="mb-3 text-sm text-ghost">
            {t.profile.tentativeDescPre}
            <span className="text-slate-200">{t.profile.tentativeDescEmph}</span>
            {t.profile.tentativeDescPost}
          </p>
          <ul className="divide-y divide-line">
            {data.tentative.map((it) => (
              <li key={it.fact_id} className="flex items-center gap-3 py-2.5">
                <Badge tone={it.polarity === 'like' ? 'live' : 'old'}>{it.polarity === 'like' ? t.profile.like : t.profile.dislike}</Badge>
                <span className="flex-1 text-sm text-slate-100">
                  {it.item} <span className="text-xs text-ghost">· {it.category}</span>
                </span>
                <EvidenceChip e={it.evidence} />
                <button
                  onClick={() => confirm(it)}
                  className="inline-flex items-center gap-1 rounded-lg border border-brand-mint/30 bg-brand-mint/10 px-2.5 py-1 text-xs text-brand-mint transition hover:bg-brand-mint/20"
                >
                  <Check className="h-3.5 w-3.5" /> {t.common.confirm}
                </button>
                <button
                  onClick={() => dismiss(it)}
                  className="rounded-lg border border-line p-1.5 text-ghost transition hover:border-brand-rose/50 hover:text-brand-rose"
                  title={t.profile.deleteCandidate}
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
