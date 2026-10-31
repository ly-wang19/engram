import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../../store/auth'
import { jobsApi, type JobsQuery } from './api'

export function useJobs(params: JobsQuery = {}) {
  const apiKey = useAuth((s) => s.apiKey)
  return useQuery({
    queryKey: ['distillation-jobs', apiKey, params], queryFn: () => jobsApi.list(params), enabled: !!apiKey,
    refetchInterval: (query) => (query.state.data?.counts.pending || query.state.data?.counts.running) ? 1500 : false,
  })
}

export function useJobActions() {
  const apiKey = useAuth((s) => s.apiKey)
  const client = useQueryClient()
  const invalidate = () => client.invalidateQueries({ queryKey: ['distillation-jobs', apiKey] })
  const enqueue = useMutation({ mutationKey: ['distillation-enqueue', apiKey], mutationFn: jobsApi.enqueue, onSuccess: invalidate })
  const retry = useMutation({ mutationKey: ['distillation-retry', apiKey], mutationFn: jobsApi.retry, onSuccess: invalidate })
  const invalidateMemory = () => {
    for (const prefix of ['memories', 'sessions', 'session-report', 'agent-status', 'working', 'profile', 'audit', 'graph', 'review', 'connections', 'connection-events']) {
      client.invalidateQueries({ queryKey: [prefix, apiKey] })
    }
  }
  return { enqueue, retry, invalidateMemory }
}
