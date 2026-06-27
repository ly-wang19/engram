import { useState } from 'react'
import { Download, ShieldCheck, Trash2 } from 'lucide-react'

import { Button, Card, CardTitle, PageHeader } from '../components/ui'
import { ConfirmDialog } from '../components/Modal'
import { toast } from '../components/Toast'
import { api } from '../lib/api'
import { useAuth } from '../store/auth'
import { useForget } from '../hooks/queries'
import { useT } from '../i18n'

export default function Privacy() {
  const user = useAuth((s) => s.apiKey) ?? 'me'
  const forget = useForget()
  const t = useT()
  const [exporting, setExporting] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [excludeSensitive, setExcludeSensitive] = useState(true)
  const protectedNamespace = user === '1' || user === 'anonymous'

  const onExport = async () => {
    setExporting(true)
    try {
      await api.exportDownload(user.replace(/[^a-zA-Z0-9]/g, '') || 'me', !excludeSensitive)
      toast.success(excludeSensitive ? t.privacy.exportedExcl : t.privacy.exported)
    } catch (e) {
      toast.error(String((e as Error).message))
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t.privacy.title} subtitle={t.privacy.subtitle} />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardTitle hint={t.privacy.exportHint}>
            <span className="inline-flex items-center gap-2">
              <Download className="h-4 w-4 text-brand-cyan" /> {t.privacy.exportTitle}
            </span>
          </CardTitle>
          <p className="text-sm leading-relaxed text-ghost">{t.privacy.exportDesc}</p>
          <label className="mt-3 flex select-none items-center gap-2 text-sm text-ghost">
            <input type="checkbox" checked={excludeSensitive} onChange={(e) => setExcludeSensitive(e.target.checked)} className="accent-brand-rose" />
            {t.privacy.excludeSensitive}
          </label>
          <Button className="mt-4" onClick={onExport} loading={exporting}>
            <Download className="h-4 w-4" /> {t.privacy.exportBtn}
          </Button>
        </Card>

        <Card>
          <CardTitle hint={t.privacy.forgetHint}>
            <span className="inline-flex items-center gap-2">
              <Trash2 className="h-4 w-4 text-brand-rose" /> {t.privacy.forgetTitle}
            </span>
          </CardTitle>
          <p className="text-sm leading-relaxed text-ghost">{t.privacy.forgetDesc}</p>
          {protectedNamespace && (
            <p className="mt-3 rounded-lg border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-sm text-brand-amber">
              {t.privacy.protectedDemo}
            </p>
          )}
          <Button variant="danger" className="mt-4" disabled={protectedNamespace} onClick={() => setConfirm(true)}>
            <Trash2 className="h-4 w-4" /> {t.privacy.forgetBtn}
          </Button>
        </Card>
      </div>

      <Card>
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-brand-mint" />
          <div className="text-sm leading-relaxed text-ghost">
            <p className="font-medium text-slate-200">{t.privacy.selfHostTitle}</p>
            <p className="mt-1">{t.privacy.selfHostBody}</p>
          </div>
        </div>
      </Card>

      <ConfirmDialog
        open={confirm}
        title={t.privacy.confirmTitle(user)}
        danger
        confirmLabel={t.privacy.confirmLabel}
        loading={forget.isPending}
        confirmDisabled={confirmText !== user}
        body={
          <div className="space-y-4">
            <p>
              {t.privacy.confirmBodyPre}
              <strong className="text-brand-rose">{t.privacy.confirmBodyEmph}</strong>
              {t.privacy.confirmBodyPost}
            </p>
            <label className="block">
              <span className="label">{t.privacy.confirmTypeLabel}</span>
              <input
                className="input mt-1.5"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={t.privacy.confirmTypePlaceholder}
              />
            </label>
          </div>
        }
        onClose={() => {
          setConfirm(false)
          setConfirmText('')
        }}
        onConfirm={() =>
          forget.mutate(undefined, {
            onSuccess: () => {
              toast.success(t.privacy.forgotten)
              setConfirm(false)
              setConfirmText('')
            },
            onError: (e) => toast.error(String((e as Error).message)),
          })
        }
      />
    </div>
  )
}
