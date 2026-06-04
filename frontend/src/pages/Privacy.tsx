import { useState } from 'react'
import { Download, ShieldCheck, Trash2 } from 'lucide-react'

import { Button, Card, CardTitle, PageHeader } from '../components/ui'
import { ConfirmDialog } from '../components/Modal'
import { toast } from '../components/Toast'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import { useForget } from '../hooks/queries'

export default function Privacy() {
  const user = useAuth((s) => s.apiKey) ?? 'me'
  const forget = useForget()
  const [exporting, setExporting] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [excludeSensitive, setExcludeSensitive] = useState(false)

  const onExport = async () => {
    setExporting(true)
    try {
      await api.exportDownload(user.replace(/[^a-zA-Z0-9]/g, '') || 'me', !excludeSensitive)
      toast.success(excludeSensitive ? '已开始下载（已排除敏感数据）' : '已开始下载你的数据')
    } catch (e) {
      toast.error(String((e as Error).message))
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="隐私与数据" subtitle="你的记忆只属于你——随时可以完整导出，或一键永久清空" />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardTitle hint="数据可携权">
            <span className="inline-flex items-center gap-2">
              <Download className="h-4 w-4 text-brand-cyan" /> 导出我的数据
            </span>
          </CardTitle>
          <p className="text-sm leading-relaxed text-ghost">
            下载一份完整 JSON，包含：用户画像、全部事实（含双时间轴、来源、provenance）、原始对话、会话摘要、关注点、关系图谱。
          </p>
          <label className="mt-3 flex select-none items-center gap-2 text-sm text-ghost">
            <input type="checkbox" checked={excludeSensitive} onChange={(e) => setExcludeSensitive(e.target.checked)} className="accent-brand-rose" />
            排除敏感数据（健康/财务/证件等）
          </label>
          <Button className="mt-4" onClick={onExport} loading={exporting}>
            <Download className="h-4 w-4" /> 导出全部数据（JSON）
          </Button>
        </Card>

        <Card>
          <CardTitle hint="被遗忘权">
            <span className="inline-flex items-center gap-2">
              <Trash2 className="h-4 w-4 text-brand-rose" /> 清空我的记忆
            </span>
          </CardTitle>
          <p className="text-sm leading-relaxed text-ghost">
            永久删除该 key 下的所有记忆——事实、对话、摘要、画像、关注点，全部抹除，不可恢复。
          </p>
          <Button variant="danger" className="mt-4" onClick={() => setConfirm(true)}>
            <Trash2 className="h-4 w-4" /> 永久清空
          </Button>
        </Card>
      </div>

      <Card>
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-brand-mint" />
          <div className="text-sm leading-relaxed text-ghost">
            <p className="font-medium text-slate-200">本地优先、可自托管</p>
            <p className="mt-1">
              Engram 开源（AGPL-3.0；商用需单独授权）。记忆默认以文件形式存放在你自己的服务器上，按 API key 隔离。没有这把 key，谁都看不到你的记忆。
            </p>
          </div>
        </div>
      </Card>

      <ConfirmDialog
        open={confirm}
        title={`永久清空“${user}”的全部记忆？`}
        danger
        confirmLabel="我确认，全部删除"
        loading={forget.isPending}
        body={
          <>
            此操作<strong className="text-brand-rose">不可撤销</strong>。建议先导出一份备份再继续。
          </>
        }
        onClose={() => setConfirm(false)}
        onConfirm={() =>
          forget.mutate(undefined, {
            onSuccess: () => {
              toast.success('已清空你的全部记忆')
              setConfirm(false)
            },
            onError: (e) => toast.error(String((e as Error).message)),
          })
        }
      />
    </div>
  )
}
