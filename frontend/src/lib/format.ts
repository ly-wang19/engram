// Small presentation helpers. No date math on the client — the server already renders
// human dates (bi-temporal stamps), we just format counts and truncate text.

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
