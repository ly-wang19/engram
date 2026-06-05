// Small presentation helpers. No date math on the client — the server already renders
// human stamps (bi-temporal, UTC), we just split/format them and truncate text.

/** Split a server stamp ("YYYY-MM-DD HH:MM:SS", Beijing) into its date and time parts.
 *  `time` is '' for legacy/date-only stamps, so callers can render it optionally. */
export function splitStamp(s: string | null | undefined): { date: string; time: string } {
  if (!s) return { date: '', time: '' }
  const i = s.indexOf(' ')
  return i === -1 ? { date: s, time: '' } : { date: s.slice(0, i), time: s.slice(i + 1) }
}

export function compactNumber(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

export function truncate(s: string, max = 140): string {
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

/** Render a predicate (works_at) as a readable phrase (works at). */
export function humanizePredicate(p: string): string {
  return p.replace(/_/g, ' ')
}

export function estimateTokens(text: string): number {
  // ~ words; matches the server's `len(ctx.split())` heuristic.
  return text.trim() ? text.trim().split(/\s+/).length : 0
}
