import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { LockKeyhole, RefreshCw } from 'lucide-react'
import { Badge, Button, Card, CardTitle, EmptyState, ErrorState, Field, Spinner } from '../../components/ui'
import { toast } from '../../components/Toast'
import { useLang } from '../../i18n'
import { request } from '../../lib/api'
import { useAuth } from '../../store/auth'

type Role = 'extractor' | 'answerer' | 'vision'
type TestResult = { ok: boolean; error: string | null; latency_ms: number; tested_at: number }
type ModelRow = { role: Role; mode: 'inherit' | 'custom' | 'offline'; model: string; api_base: string; key_present: boolean; configured: boolean; timeout: number; last_test: TestResult | null }
type ModelView = { roles: ModelRow[]; startup_error: string | null }
const adminRequest = <T,>(path: string, key: string, init: RequestInit = {}) => request<T>(`/v1/admin${path}`, { ...init, headers: { 'X-Engram-Admin-Key': key } })
const when = (value?: number | null) => value ? new Date(value * 1000).toLocaleString() : '—'

export function ModelSettings() {
  const zh = useLang(s => s.lang) === 'zh'
  const apiKey = useAuth(s => s.apiKey)
  const access = useQuery({ queryKey: ['admin-access', apiKey], queryFn: () => adminRequest<{configured: boolean}>('/access', '') })
  // Operator credentials exist only in this mounted component, never browser storage or query keys.
  const [key, setKey] = useState('')
  const [unlocked, setUnlocked] = useState(false)
  const [view, setView] = useState<ModelView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { setKey(''); setUnlocked(false); setView(null); setError('') }, [apiKey])
  const unlock = async () => {
    setBusy(true); setError('')
    try { setView(await adminRequest<ModelView>('/models', key)); setUnlocked(true) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <Card>
    <CardTitle><span className="inline-flex items-center gap-2"><LockKeyhole className="h-4 w-4" />{zh ? '模型管理' : 'Model administration'}</span></CardTitle>
    <p className="mb-4 text-sm text-ghost">{zh ? '管理员可配置提炼、问答和图像模型。保存后用于后续请求；已有记忆保持可读。' : 'Administrators can configure extraction, answers and vision. Saved settings apply to subsequent requests.'}</p>
    {access.isLoading ? <Spinner /> : access.isError ? <ErrorState message={(access.error as Error).message} /> : !access.data?.configured ? <p className="text-sm text-brand-amber">{zh ? '服务端尚未启用管理员访问。请由部署管理员设置 ENGRAM_ADMIN_KEYS 后使用此功能。' : 'Operator access is not enabled. The deployment administrator must set ENGRAM_ADMIN_KEYS.'}</p> : !unlocked ? <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); void unlock() }}>
      <div className="min-w-0 flex-1"><Field label={zh ? '管理员密钥（仅本页临时使用）' : 'Administrator key (this page only)'}><input className="input" type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} /></Field></div>
      <Button type="submit" loading={busy} disabled={!key}>{zh ? '解锁模型管理' : 'Unlock models'}</Button>
    </form> : <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><Badge tone="user">{zh ? '管理员已解锁' : 'Administrator access'}</Badge><Button variant="ghost" onClick={() => { setKey(''); setUnlocked(false); setView(null) }}>{zh ? '锁定' : 'Lock'}</Button></div>
      {view?.startup_error && <ErrorState message={`${zh ? '配置加载失败' : 'Configuration load failed'}: ${view.startup_error}`} />}
      {view?.roles.map(row => <ModelEditor key={row.role} row={row} adminKey={key} onSaved={setView} />)}
    </div>}
    {error && <div className="mt-3"><ErrorState message={error} /></div>}
  </Card>
}

function ModelEditor({ row, adminKey, onSaved }: { row: ModelRow; adminKey: string; onSaved: (value: ModelView) => void }) {
  const zh = useLang(s => s.lang) === 'zh'
  const qc = useQueryClient()
  const [mode, setMode] = useState(row.mode)
  const [model, setModel] = useState(row.model)
  const [base, setBase] = useState(row.api_base)
  const [secret, setSecret] = useState('')
  const [clear, setClear] = useState(false)
  const [timeout, setTimeoutValue] = useState(row.timeout)
  const [busy, setBusy] = useState<'save' | 'test' | ''>('')
  const [result, setResult] = useState(row.last_test)
  const [dirty, setDirty] = useState(false)
  const label = ({extractor: zh ? '提炼模型' : 'Extraction model', answerer: zh ? '问答模型' : 'Answer model', vision: zh ? '图像模型' : 'Vision model'})[row.role]
  useEffect(() => { setMode(row.mode); setModel(row.model); setBase(row.api_base); setTimeoutValue(row.timeout); setResult(row.last_test); setDirty(false) }, [row])
  const save = async () => {
    setBusy('save')
    try {
      const view = await adminRequest<ModelView>(`/models/${row.role}`, adminKey, { method: 'PUT', body: JSON.stringify({ mode, model, api_base: base, api_key: secret || null, clear_key: clear, timeout }) })
      setSecret(''); setClear(false); onSaved(view); void qc.invalidateQueries({queryKey: ['health']})
      toast.success(zh ? '模型配置已保存' : 'Model configuration saved')
    } catch (e) { toast.error((e as Error).message) } finally { setBusy('') }
  }
  const test = async () => {
    setBusy('test')
    try { setResult(await adminRequest<TestResult>(`/models/${row.role}/test`, adminKey, {method: 'POST'})) }
    catch (e) { toast.error((e as Error).message) } finally { setBusy('') }
  }
  return <div className="min-w-0 space-y-3 rounded-xl border border-white/10 p-4" data-testid={`model-${row.role}`}>
    <div className="flex flex-wrap justify-between gap-2"><h4 className="text-sm font-semibold">{label}</h4><Badge tone={row.configured ? 'live' : 'default'}>{row.configured ? (zh ? '已配置' : 'Configured') : (zh ? '未配置' : 'Not configured')}</Badge></div>
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label={zh ? '配置方式' : 'Mode'}><select className="input" value={mode} onChange={e => { setMode(e.target.value as ModelRow['mode']); setDirty(true) }}><option value="inherit">{zh ? '继承服务端配置' : 'Inherit server configuration'}</option><option value="custom">{zh ? '自定义' : 'Custom'}</option><option value="offline">{zh ? '禁用此模型' : 'Disable this model'}</option></select></Field>
      {mode === 'custom' && <><Field label={zh ? '模型标识' : 'Model identifier'}><input className="input" value={model} placeholder="openai/your-model" onChange={e => { setModel(e.target.value); setDirty(true) }} /></Field>
      <Field label={zh ? '接口地址（可选）' : 'Endpoint (optional)'}><input className="input" value={base} placeholder="https://api.example.com/v1" onChange={e => { setBase(e.target.value); setDirty(true) }} /></Field>
      <Field label={zh ? '模型密钥' : 'Provider key'}><input className="input" type="password" autoComplete="new-password" value={secret} placeholder={row.key_present ? (zh ? '已保存，留空保留' : 'Saved; leave blank to keep') : (zh ? '未保存，或使用服务端环境凭证' : 'Not saved, or using server environment')} onChange={e => { setSecret(e.target.value); setDirty(true) }} /></Field>
      <Field label={zh ? '超时（秒）' : 'Timeout (seconds)'}><input className="input" type="number" min="1" max="120" value={timeout} onChange={e => { setTimeoutValue(Number(e.target.value)); setDirty(true) }} /></Field>
      <label className="flex items-center gap-2 text-xs text-ghost"><input type="checkbox" checked={clear} onChange={e => { setClear(e.target.checked); setDirty(true) }} />{zh ? '清除已保存的模型密钥' : 'Clear saved provider key'}</label></>}
    </div>
    <div className="flex flex-wrap gap-2"><Button onClick={() => void save()} disabled={!dirty || !!busy} loading={busy === 'save'}>{zh ? '保存模型' : 'Save model'}</Button><Button variant="ghost" onClick={() => void test()} disabled={dirty || !!busy} loading={busy === 'test'}>{zh ? '测试已保存配置' : 'Test saved configuration'}</Button></div>
    {result && <p className={`break-words text-xs ${result.ok ? 'text-brand-mint' : 'text-brand-amber'}`}>{result.ok ? (zh ? '连接成功' : 'Connection succeeded') : (zh ? '连接失败' : 'Connection failed')} · {result.error || `${result.latency_ms} ms`} · {when(result.tested_at)}</p>}
    {row.role === 'vision' && <p className="text-xs text-ghost">{zh ? '连接测试验证文本请求，图像识别需使用实际图像另行验证。' : 'The connection test checks text completion; image recognition requires a real image check.'}</p>}
  </div>
}

type SourceRow = {source: string; status: string; episodes: number; sessions: number; failures: number; last_stored_at?: number; last_event_at?: number; last_error?: string}
type SourceEvent = {id: string; source: string; status: string; operation: string; at: number; session_id?: string; error?: string}
export function ConnectionSettings() {
  const zh = useLang(s => s.lang) === 'zh'
  const key = useAuth(s => s.apiKey)
  const [source, setSource] = useState('')
  const [offset, setOffset] = useState(0)
  const index = useQuery({queryKey: ['connections', key], queryFn: () => request<{sources: SourceRow[]; event_retention: number}>('/v1/connections')})
  const events = useQuery({queryKey: ['connection-events', key, source, offset], queryFn: () => request<{items: SourceEvent[]; total: number; has_more: boolean}>(`/v1/connections/events?offset=${offset}&limit=10&source=${encodeURIComponent(source)}`)})
  const status = (value: string) => zh ? ({succeeded:'成功', failed:'失败', stored:'已入库', deferred:'待提炼', observed:'已发现原文'}[value] || value) : value
  return <Card>
    <CardTitle><span className="flex items-center gap-3">{zh ? '接入来源与同步记录' : 'Sources and synchronization'}<Button variant="ghost" aria-label={zh ? '刷新来源' : 'Refresh sources'} onClick={() => { void index.refetch(); void events.refetch() }}><RefreshCw className="h-4 w-4" /></Button></span></CardTitle>
    <p className="mb-4 text-xs text-ghost">{zh ? `显示服务端已收到的数据与客户端上报；不能据此判断设备是否在线。保留最近 ${index.data?.event_retention ?? 500} 条记录。` : `Shows received data and client reports, not live device presence. Retains the latest ${index.data?.event_retention ?? 500} events.`}</p>
    {index.isError ? <ErrorState message={(index.error as Error).message} /> : index.isLoading ? <Spinner /> : !index.data?.sources.length ? <EmptyState title={zh ? '尚无来源记录' : 'No sources yet'} /> : <div className="mb-4 grid gap-3 sm:grid-cols-2">
      {index.data.sources.map(row => <div key={row.source} className="min-w-0 rounded-lg border border-white/10 p-3 text-xs"><div className="flex flex-wrap justify-between gap-2"><strong className="break-all">{row.source}</strong><Badge tone={row.status === 'failed' ? 'user' : 'default'}>{status(row.status)}</Badge></div><p className="mt-2 text-ghost">{row.episodes} {zh ? '条原文' : 'episodes'} · {row.sessions} {zh ? '个会话' : 'sessions'} · {row.failures} {zh ? '次失败' : 'failures'}</p><p className="mt-1 text-ghost">{zh ? '最近入库' : 'Last stored'}: {when(row.last_stored_at)}</p><p className="mt-1 text-ghost">{zh ? '最近事件' : 'Last event'}: {when(row.last_event_at)} {row.last_error && `· ${row.last_error}`}</p></div>)}
    </div>}
    <label className="mb-3 block text-xs text-ghost">{zh ? '筛选来源' : 'Filter source'}<select className="input mt-1" value={source} onChange={e => {setSource(e.target.value);setOffset(0)}}><option value="">{zh ? '全部来源' : 'All sources'}</option>{index.data?.sources.map(row => <option key={row.source}>{row.source}</option>)}</select></label>
    {events.isError ? <ErrorState message={(events.error as Error).message} /> : events.isLoading ? <Spinner /> : <div className="space-y-2">{events.data?.items.map(event => <div key={event.id} className="min-w-0 rounded-lg bg-white/[0.03] p-3 text-xs"><div className="flex flex-wrap gap-2"><strong>{event.source}</strong><Badge tone={event.status === 'failed' ? 'user' : 'default'}>{status(event.status)}</Badge><span className="text-ghost">{event.operation} · {when(event.at)}</span></div>{event.session_id && <p className="mt-1 break-all text-ghost">{event.session_id}</p>}{event.error && <p className="mt-1 text-brand-amber">{event.error}</p>}</div>)}</div>}
    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-ghost"><span>{events.data?.total ?? 0} {zh ? '条记录' : 'events'}</span><div className="flex gap-2"><Button variant="ghost" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 10))}>{zh ? '上一页' : 'Previous'}</Button><Button variant="ghost" disabled={!events.data?.has_more} onClick={() => setOffset(offset + 10)}>{zh ? '下一页' : 'Next'}</Button></div></div>
  </Card>
}
