import { Button } from '../../components/ui'
import { useLang } from '../../i18n'
import type { HandlingEvent, ReviewPage } from './api'

export function useReviewText() {
  const zh = useLang(s => s.lang) === 'zh'
  return (cn: string, en: string) => zh ? cn : en
}
export function ReviewPager({ page, onOffset }: { page: ReviewPage; onOffset: (offset: number) => void }) {
  const tr = useReviewText()
  return <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-ghost">
    <span>{tr(`共 ${page.total} 条 · ${page.total ? Math.min(page.total, page.offset + 1) : 0}–${Math.min(page.total, page.offset + page.limit)}`,
      `${page.total} total · ${page.total ? Math.min(page.total, page.offset + 1) : 0}–${Math.min(page.total, page.offset + page.limit)}`)}</span>
    <div className="flex gap-2">
      <Button variant="ghost" disabled={page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>{tr('上一页', 'Previous')}</Button>
      <Button variant="ghost" disabled={!page.has_more} onClick={() => onOffset(page.next_offset ?? 0)}>{tr('下一页', 'Next')}</Button>
    </div>
  </div>
}
export function statusLabel(status: string, tr: (cn: string, en: string) => string) {
  return status === 'open' ? tr('待处理', 'Open') : status === 'resolved' ? tr('已解决', 'Resolved')
    : status === 'ignored' ? tr('已忽略', 'Ignored') : tr('全部状态', 'All statuses')
}
export function ReviewHistory({ events }: { events: HandlingEvent[] }) {
  const tr = useReviewText()
  if (!events.length) return null
  const reasons: Record<string, string> = {
    user_resolved: tr('人工标记已解决', 'Marked resolved'), user_reopened: tr('重新打开', 'Reopened'),
    user_open: tr('重新打开', 'Reopened'), user_ignored: tr('忽略误报', 'Ignored as a false positive'),
    source_changed: tr('来源已变化，重新待处理', 'Source changed; reopened'),
    resolution_changed: tr('关联结论已变更或不可用，重新待处理', 'Resolution changed or unavailable; reopened'),
    no_longer_flagged: tr('修正或移除后，当前规则不再报告此项', 'No longer flagged after source correction or removal'),
  }
  return <details className="mt-2 text-xs text-ghost">
    <summary className="cursor-pointer">{tr(`处理记录（${events.length}）`, `History (${events.length})`)}</summary>
    <ul className="mt-2 max-h-48 space-y-2 overflow-y-auto">
      {[...events].reverse().map((event, i) => <li key={`${event.at}-${i}`} className="break-words">
        <time>{new Date(event.at * 1000).toLocaleString()}</time> · {reasons[event.reason] ?? statusLabel(event.status, tr)}
        {event.note && <p className="mt-0.5 text-slate-300">{event.note}</p>}
      </li>)}
    </ul>
  </details>
}
