import { useMemo, useState } from 'react'

import type { GraphData } from '../types'
import { useT } from '../i18n'
import { truncate } from '../lib/format'

interface Positioned {
  id: string
  name: string
  type: string
  x: number
  y: number
}

const W = 900
const H = 540

// A tiny, dependency-free force-directed layout: repulsion between all nodes,
// spring along edges, mild gravity to center. Deterministic (circle seed, no RNG),
// so the picture is stable across renders.
function layout(data: GraphData): { nodes: Positioned[]; index: Map<string, Positioned> } {
  const n = data.nodes.length || 1
  const nodes: Positioned[] = data.nodes.map((node, i) => ({
    ...node,
    x: W / 2 + Math.cos((2 * Math.PI * i) / n) * 220,
    y: H / 2 + Math.sin((2 * Math.PI * i) / n) * 170,
  }))
  const index = new Map(nodes.map((nd) => [nd.id, nd]))

  for (let iter = 0; iter < 280; iter++) {
    // repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]
        const b = nodes[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        let d = Math.sqrt(dx * dx + dy * dy) || 0.5
        const rep = 4200 / (d * d)
        dx /= d
        dy /= d
        a.x += dx * rep
        a.y += dy * rep
        b.x -= dx * rep
        b.y -= dy * rep
      }
    }
    // springs
    for (const e of data.edges) {
      const a = index.get(e.source)
      const b = index.get(e.target)
      if (!a || !b) continue
      let dx = b.x - a.x
      let dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy) || 0.5
      const force = (d - 130) * 0.03
      dx /= d
      dy /= d
      a.x += dx * force
      a.y += dy * force
      b.x -= dx * force
      b.y -= dy * force
    }
    // gravity
    for (const nd of nodes) {
      nd.x += (W / 2 - nd.x) * 0.012
      nd.y += (H / 2 - nd.y) * 0.012
    }
  }
  for (const nd of nodes) {
    nd.x = Math.max(46, Math.min(W - 46, nd.x))
    nd.y = Math.max(34, Math.min(H - 34, nd.y))
  }
  return { nodes, index }
}

export function ForceGraph({
  data,
  selectedId,
  onSelect,
}: {
  data: GraphData
  selectedId?: string | null
  onSelect?: (id: string) => void
}) {
  const t = useT()
  const { nodes, index } = useMemo(() => layout(data), [data])
  const [hover, setHover] = useState<string | null>(null)

  const adjacency = useMemo(() => {
    const m = new Map<string, Set<string>>()
    for (const e of data.edges) {
      if (!m.has(e.source)) m.set(e.source, new Set())
      if (!m.has(e.target)) m.set(e.target, new Set())
      m.get(e.source)!.add(e.target)
      m.get(e.target)!.add(e.source)
    }
    return m
  }, [data.edges])

  const activeId = hover ?? selectedId ?? null
  const isDim = (id: string) => activeId !== null && activeId !== id && !adjacency.get(activeId)?.has(id)

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full rounded-xl bg-black/20"
      role="img"
      aria-label={t.graph.ariaLabel}
    >
      <defs>
        <radialGradient id="node" cx="50%" cy="40%" r="65%">
          <stop offset="0%" stopColor="#c4b5fd" />
          <stop offset="100%" stopColor="#7c5cff" />
        </radialGradient>
      </defs>

      {/* edges */}
      {data.edges.map((e, i) => {
        const a = index.get(e.source)
        const b = index.get(e.target)
        if (!a || !b) return null
        const mx = (a.x + b.x) / 2
        const my = (a.y + b.y) / 2
        const dim = activeId !== null && activeId !== e.source && activeId !== e.target
        return (
          <g key={i} opacity={dim ? 0.12 : 0.8}>
            <line
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={e.live ? '#22d3ee' : '#64748b'}
              strokeWidth={e.live ? 1.7 : 1.1}
              strokeDasharray={e.live ? undefined : '5 5'}
            />
            <text x={mx} y={my - 4} textAnchor="middle" fontSize={9.5} fill="#8a97b8">
              {e.predicate}
            </text>
          </g>
        )
      })}

      {/* nodes */}
      {nodes.map((nd) => (
        <g
          key={nd.id}
          opacity={isDim(nd.id) ? 0.2 : 1}
          onMouseEnter={() => setHover(nd.id)}
          onMouseLeave={() => setHover(null)}
          onClick={() => onSelect?.(nd.id)}
          className="cursor-pointer"
        >
          <circle
            cx={nd.x}
            cy={nd.y}
            r={hover === nd.id || selectedId === nd.id ? 10 : 7}
            fill="url(#node)"
            stroke={selectedId === nd.id ? '#34d399' : '#0b1220'}
            strokeWidth={2}
          />
          <title>{nd.name}</title>
          <text x={nd.x} y={nd.y - 13} textAnchor="middle" fontSize={11.5} fontWeight={600} fill="#eaf0ff">
            {truncate(nd.name, 18)}
          </text>
        </g>
      ))}
    </svg>
  )
}
