import type { DistillationJob, JobStatus } from './api'

export function jobsText(lang: 'zh' | 'en') {
  const zh = lang === 'zh'
  const states: Record<JobStatus, string> = zh
    ? { pending: '待处理', running: '运行中', succeeded: '成功', failed: '失败' }
    : { pending: 'Pending', running: 'Running', succeeded: 'Succeeded', failed: 'Failed' }
  const reasons: Record<string, string> = zh ? {
    queued: '任务已保存，等待执行。', executing: '正在提炼，请稍候。',
    completed: '提炼完成。', model_unavailable: '未配置可用的提炼模型。配置后可重试。',
    outcomes_disabled: '服务器已关闭结论提炼，本次未提炼结论。',
    no_episodes: '会话没有可提炼的原文。', extraction_error: '模型提炼失败，原文仍保留。检查模型连接后重试。',
    interrupted: '服务中断，未确认提炼完成。原文仍保留，可重试。',
    execution_error: '任务执行失败，未确认提炼完成。检查服务配置后重试。',
    invalid_result: '未收到有效的提炼完成回执，请检查服务后重试。', cancelled: '任务已取消。',
  } : {
    queued: 'Saved and waiting to run.', executing: 'Distillation is running.',
    completed: 'Distillation completed.', model_unavailable: 'No distillation model is configured. Configure one, then retry.',
    outcomes_disabled: 'Session outcomes are disabled by the server. No conclusions were distilled.',
    no_episodes: 'This session has no transcript to distill.',
    extraction_error: 'Model extraction failed. The transcript is retained. Check the model connection, then retry.',
    interrupted: 'The service was interrupted; completion is unconfirmed. The transcript is retained. You can retry.',
    execution_error: 'Execution failed; completion is unconfirmed. Check the service configuration, then retry.',
    invalid_result: 'No valid completion receipt was received. Check the service, then retry.', cancelled: 'The job was cancelled.',
  }
  return {
    title: zh ? '提炼任务' : 'Distillation jobs',
    subtitle: zh ? '查看会话提炼进度、失败原因和重试记录。' : 'Track session distillation, failure reasons, and retry history.',
    states, all: zh ? '全部' : 'All', status: zh ? '状态' : 'Status',
    session: zh ? '会话 ID' : 'Session ID', filter: zh ? '筛选' : 'Filter',
    distill: zh ? '提炼会话' : 'Distill session', again: zh ? '重新提炼' : 'Distill again',
    retry: zh ? '重试' : 'Retry', refresh: zh ? '刷新' : 'Refresh',
    loading: zh ? '正在读取任务…' : 'Loading jobs…',
    empty: zh ? '没有符合条件的任务' : 'No matching jobs',
    emptyHint: zh ? '从会话或结论页发起提炼，进度会显示在这里。' : 'Start distillation from Conversations or Journal to track it here.',
    created: zh ? '创建时间' : 'Created', previous: zh ? '上一页' : 'Previous', next: zh ? '下一页' : 'Next',
    details: zh ? '尝试记录' : 'Attempt history', view: zh ? '查看会话' : 'View session',
    center: zh ? '任务中心' : 'Job center',
    submitted: zh ? '任务已保存，可在任务中心查看进度。' : 'Job saved. Track progress in the job center.',
    duplicate: zh ? '会话已有运行中的任务，继续查看原任务。' : 'This session already has an active job. Following that job.',
    attempt: (n: number) => zh ? `第 ${n} 次尝试` : `Attempt ${n}`,
    count: (n: number) => zh ? `共 ${n} 项` : `${n} jobs`,
    reason: (reason: string) => reasons[reason] || (zh ? '未知状态，请刷新后查看。' : 'Unknown state. Refresh to check again.'),
    result: (job: DistillationJob) => job.status === 'succeeded'
      ? (zh ? `提炼完成，新增 ${job.result?.outcomes ?? 0} 条结论。` : `Completed: ${job.result?.outcomes ?? 0} new conclusions.`)
      : reasons[job.reason] || (zh ? '未确认提炼完成。' : 'Completion is unconfirmed.'),
  }
}
