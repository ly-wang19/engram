import { useMemo, useState } from 'react'
import { Lock, Pencil, Plus, Search, ShieldAlert, Trash2 } from 'lucide-react'

import { Badge, Button, Card, EmptyState, ErrorState, Field, PageHeader, Spinner } from '../components/ui'
import { ConfirmDialog, Modal } from '../components/Modal'
import { toast } from '../components/Toast'
import { useAddFact, useDeleteFact, useEditFact, useMemories } from '../hooks/queries'
import type { MemoryFact } from '../types'

export default function Facts() {
  const { data, isLoading, isError, error } = useMemories()
  const addFact = useAddFact()
  const editFact = useEditFact()
  const deleteFact = useDeleteFact()

  const [q, setQ] = useState('')
  const [showOld, setShowOld] = useState(true)
  const [hideSensitive, setHideSensitive] = useState(false)
  const [editing, setEditing] = useState<MemoryFact | null>(null)
  const [adding, setAdding] = useState(false)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const facts = useMemo(() => {
    let list = data?.facts ?? []
    if (!showOld) list = list.filter((f) => f.status === 'live')
    if (hideSensitive) list = list.filter((f) => !f.sensitive)
    const needle = q.trim().toLowerCase()
    if (needle) list = list.filter((f) => f.text.toLowerCase().includes(needle))
    return list
  }, [data, q, showOld, hideSensitive])

  const sensitiveCount = (data?.facts ?? []).filter((f) => f.sensitive).length

  if (isLoading) return <Spinner label="加载事实…" />
  if (isError) return <ErrorState message={(error as Error).message} />

  return (
    <div className="space-y-6">
      <PageHeader
        title="事实管理"
        subtitle="每条事实都有双时间轴与来源。你手动改/加的会被锁定 🔒，不会被自动抽取覆盖。"
        actions={
          <Button onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" /> 手动加一条
          </Button>
        }
      />

      <Card className="!p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-white/[0.05] px-3">
            <Search className="h-4 w-4 text-ghost" />
            <input
              className="w-full bg-transparent py-2 text-sm outline-none placeholder:text-ghost/70"
              placeholder="搜索事实…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input type="checkbox" checked={showOld} onChange={(e) => setShowOld(e.target.checked)} className="accent-brand-cyan" />
            显示历史
          </label>
          <label className="flex select-none items-center gap-2 px-1 text-sm text-ghost">
            <input type="checkbox" checked={hideSensitive} onChange={(e) => setHideSensitive(e.target.checked)} className="accent-brand-rose" />
            隐藏敏感{sensitiveCount > 0 && <span className="text-brand-rose">({sensitiveCount})</span>}
          </label>
        </div>
      </Card>

      <Card>
        {facts.length ? (
          <ul className="divide-y divide-line">
            {facts.map((f) => (
              <li key={f.id} className="group flex items-center gap-3 py-3">
                <Badge tone={f.status === 'live' ? 'live' : 'old'}>{f.status === 'live' ? '当前' : '历史'}</Badge>
                <time className="w-[78px] shrink-0 text-[11px] tabular-nums text-brand-cyan">{f.valid_at}</time>
                <div className="min-w-0 flex-1">
                  <div className={f.status === 'live' ? 'text-sm text-slate-100' : 'text-sm text-ghost line-through'}>
                    {f.display || f.text}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-ghost">
                    <span className="rounded bg-white/5 px-1.5 py-px font-mono">{f.predicate}</span>
                    {f.category && f.category !== '其他' && (
                      <span className="rounded bg-brand-violet/15 px-1.5 py-px text-brand-violet">{f.category}</span>
                    )}
                    {f.sensitive && (
                      <span className="inline-flex items-center gap-1 rounded bg-brand-rose/15 px-1.5 py-px text-brand-rose">
                        <ShieldAlert className="h-3 w-3" /> 敏感
                      </span>
                    )}
                    {f.source === 'user' && (
                      <span className="inline-flex items-center gap-1 text-brand-amber">
                        <Lock className="h-3 w-3" /> 我设定
                      </span>
                    )}
                    {f.invalid_at && <span>失效于 {f.invalid_at}</span>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1 opacity-0 transition group-hover:opacity-100">
                  <button
                    onClick={() => setEditing(f)}
                    className="rounded-lg border border-line p-2 text-ghost transition hover:border-brand-cyan/50 hover:text-brand-cyan"
                    title="编辑"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setConfirmId(f.id)}
                    className="rounded-lg border border-line p-2 text-ghost transition hover:border-brand-rose/50 hover:text-brand-rose"
                    title="删除"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="没有匹配的事实" hint="换个关键词，或手动加一条。" />
        )}
      </Card>

      {/* edit */}
      <FactModal
        open={!!editing}
        title="编辑事实"
        initial={editing ?? undefined}
        loading={editFact.isPending}
        onClose={() => setEditing(null)}
        onSubmit={(vals) => {
          if (!editing) return
          editFact.mutate(
            { id: editing.id, patch: vals },
            {
              onSuccess: () => {
                toast.success('已更新并锁定 🔒')
                setEditing(null)
              },
              onError: (e) => toast.error(String((e as Error).message)),
            },
          )
        }}
      />

      {/* add */}
      <FactModal
        open={adding}
        title="手动添加事实"
        loading={addFact.isPending}
        onClose={() => setAdding(false)}
        onSubmit={(vals) => {
          addFact.mutate(
            { subject: vals.subject || 'user', predicate: vals.predicate!, object: vals.object! },
            {
              onSuccess: () => {
                toast.success('已添加（来源：我设定）')
                setAdding(false)
              },
              onError: (e) => toast.error(String((e as Error).message)),
            },
          )
        }}
      />

      {/* delete */}
      <ConfirmDialog
        open={!!confirmId}
        title="永久删除这条记忆？"
        danger
        confirmLabel="删除"
        loading={deleteFact.isPending}
        body="这是用户发起的彻底擦除（不同于自动失效会保留历史）。删除后无法恢复。"
        onClose={() => setConfirmId(null)}
        onConfirm={() => {
          if (!confirmId) return
          deleteFact.mutate(confirmId, {
            onSuccess: () => {
              toast.success('已删除')
              setConfirmId(null)
            },
            onError: (e) => toast.error(String((e as Error).message)),
          })
        }}
      />
    </div>
  )
}

function FactModal({
  open,
  title,
  initial,
  loading,
  onClose,
  onSubmit,
}: {
  open: boolean
  title: string
  initial?: MemoryFact
  loading: boolean
  onClose: () => void
  onSubmit: (vals: { subject?: string; predicate?: string; object?: string; sensitive?: boolean }) => void
}) {
  const [subject, setSubject] = useState('user')
  const [predicate, setPredicate] = useState('')
  const [object, setObject] = useState('')
  const [sensitive, setSensitive] = useState(false)

  // Sync inputs whenever the dialog opens with a (possibly new) fact.
  const [seen, setSeen] = useState<string | null>(null)
  const sig = open ? (initial?.id ?? 'new') : null
  if (sig !== seen) {
    setSeen(sig)
    setSubject(initial?.subject ?? 'user')
    setPredicate(initial?.predicate ?? '')
    setObject(initial?.object ?? '')
    setSensitive(initial?.sensitive ?? false)
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button loading={loading} disabled={!predicate.trim() || !object.trim()} onClick={() => onSubmit({ subject, predicate, object, sensitive })}>
            保存
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="主语 Subject">
          <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="user" />
        </Field>
        <Field label="谓语 Predicate">
          <input className="input font-mono" value={predicate} onChange={(e) => setPredicate(e.target.value)} placeholder="works_at" />
        </Field>
        <Field label="宾语 Object">
          <input className="input" value={object} onChange={(e) => setObject(e.target.value)} placeholder="字节跳动" />
        </Field>
        <label className="flex select-none items-center gap-2 text-sm text-slate-200">
          <input type="checkbox" checked={sensitive} onChange={(e) => setSensitive(e.target.checked)} className="accent-brand-rose" />
          <ShieldAlert className="h-4 w-4 text-brand-rose" /> 标记为敏感（导出/共享时可一键排除）
        </label>
        <p className="text-xs text-ghost">保存后这条会被标记为“我设定”，自动抽取不会再覆盖它。</p>
      </div>
    </Modal>
  )
}
