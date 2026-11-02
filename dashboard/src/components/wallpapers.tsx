import { useMemo } from 'react'

/** Small seeded generator: a course keeps the same picture on every visit. */
function rng(seed: number) {
  let a = (seed * 2654435761) >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const W = 1200
const H = 700
const LINE = 'var(--accent)'

function Circuit({ r }: { r: () => number }) {
  const traces = useMemo(() => Array.from({ length: 34 }, () => {
    let x = Math.round(r() * 24) * 50
    let y = Math.round(r() * 14) * 50
    let d = `M${x} ${y}`
    for (let i = 0; i < 2 + Math.floor(r() * 4); i++) {
      const len = (1 + Math.floor(r() * 5)) * 50
      const turn = r()
      if (i % 2) y += turn < 0.5 ? len : -len
      else x += turn < 0.5 ? len : -len
      d += ` L${x} ${y}`
      if (r() < 0.5) {
        x += 25
        y += r() < 0.5 ? 25 : -25
        d += ` L${x} ${y}`
      }
    }
    return { d, x, y, live: r() < 0.3, delay: r() * 6 }
  }), [r])
  return (
    <>
      {traces.map((t) => <path key={t.d} d={t.d} fill="none" stroke={LINE} strokeWidth="1.2" opacity="0.42" />)}
      {traces.filter((t) => t.live).map((t) => <path key={`f${t.d}`} className="wp-flow" style={{ animationDelay: `-${t.delay}s` }} d={t.d} fill="none" stroke={LINE} strokeWidth="2" pathLength={100} />)}
      {traces.map((t) => <rect key={`n${t.d}`} x={t.x - 4} y={t.y - 4} width="8" height="8" fill="none" stroke={LINE} opacity="0.7" />)}
    </>
  )
}

function Terrain({ r }: { r: () => number }) {
  const { rows, cols } = useMemo(() => {
    const phase = r() * 10
    const point = (u: number, v: number) => {
      const depth = 1 / (0.28 + v * 1.6) // v 0 = horizon side, 1 = near
      const h = (Math.sin(u * 9 + phase) + Math.sin(u * 23 + v * 7 + phase)) * 34 * v
      return [W / 2 + (u - 0.5) * W * 2.2 * (1 / depth) * 0.55, 300 + v * 400 - Math.abs(h)]
    }
    const line = (at: (s: number) => [number, number]) => Array.from({ length: 41 }, (_, i) => at(i / 40)).map(([x, y]) => `${x.toFixed(0)},${y.toFixed(0)}`).join(' ')
    return {
      rows: Array.from({ length: 14 }, (_, i) => line((u) => point(u, ((i + 1) / 14) ** 1.6) as [number, number])),
      cols: Array.from({ length: 25 }, (_, i) => line((v) => point(i / 24, v ** 1.6) as [number, number])),
    }
  }, [r])
  return (
    <>
      <circle cx={W / 2} cy="285" r="120" fill={LINE} opacity="0.12" />
      <circle cx={W / 2} cy="285" r="70" fill={LINE} opacity="0.2" className="wp-pulse" />
      {cols.map((p) => <polyline key={p} points={p} fill="none" stroke={LINE} strokeWidth="1" opacity="0.45" />)}
      {rows.map((p, i) => <polyline key={p} points={p} fill="none" stroke={LINE} strokeWidth="1" opacity={0.3 + i * 0.04} />)}
    </>
  )
}

function Network({ r }: { r: () => number }) {
  const { nodes, edges } = useMemo(() => {
    const nodes = Array.from({ length: 46 }, () => ({ x: r() * W, y: r() * H, s: 2 + r() * 4, delay: r() * 4 }))
    const edges = nodes.flatMap((a, i) => nodes.slice(i + 1).filter((b) => Math.hypot(a.x - b.x, a.y - b.y) < 190).map((b) => ({ a, b, live: r() < 0.14, delay: r() * 6 })))
    return { nodes, edges }
  }, [r])
  return (
    <>
      {edges.map((e) => <line key={`${e.a.x}${e.b.x}`} x1={e.a.x} y1={e.a.y} x2={e.b.x} y2={e.b.y} stroke={LINE} strokeWidth="1" opacity="0.38" />)}
      {edges.filter((e) => e.live).map((e) => <line key={`f${e.a.x}${e.b.x}`} className="wp-flow" style={{ animationDelay: `-${e.delay}s` }} x1={e.a.x} y1={e.a.y} x2={e.b.x} y2={e.b.y} stroke={LINE} strokeWidth="2" pathLength={100} />)}
      {nodes.map((n) => <circle key={`${n.x}`} className="wp-pulse" style={{ animationDelay: `-${n.delay}s` }} cx={n.x} cy={n.y} r={n.s} fill={LINE} />)}
    </>
  )
}

function Hexes({ r }: { r: () => number }) {
  const cells = useMemo(() => {
    const size = 46
    const out: { d: string; lit: boolean; delay: number }[] = []
    for (let row = 0; row < 11; row++) {
      for (let col = 0; col < 18; col++) {
        const cx = col * size * 1.74 + (row % 2 ? size * 0.87 : 0)
        const cy = row * size * 1.5
        const d = Array.from({ length: 6 }, (_, k) => {
          const a = Math.PI / 3 * k + Math.PI / 6
          return `${(cx + size * Math.cos(a)).toFixed(1)},${(cy + size * Math.sin(a)).toFixed(1)}`
        }).join(' ')
        out.push({ d, lit: r() < 0.12, delay: r() * 5 })
      }
    }
    return out
  }, [r])
  return (
    <>
      {cells.map((c) => <polygon key={c.d} points={c.d} fill="none" stroke={LINE} strokeWidth="1" opacity="0.32" />)}
      {cells.filter((c) => c.lit).map((c) => <polygon key={`l${c.d}`} className="wp-pulse" style={{ animationDelay: `-${c.delay}s` }} points={c.d} fill={LINE} fillOpacity="0.28" stroke={LINE} strokeWidth="1.5" />)}
    </>
  )
}

function Scope({ r }: { r: () => number }) {
  const waves = useMemo(() => Array.from({ length: 9 }, (_, i) => {
    const f = 0.006 + r() * 0.012
    const g = 0.02 + r() * 0.03
    const p = r() * 6
    const a = 40 + r() * 70
    return Array.from({ length: 121 }, (_, k) => {
      const x = k * 10
      return `${x},${(300 + (i - 4) * 26 + Math.sin(x * f + p) * a + Math.sin(x * g + p) * a * 0.25).toFixed(1)}`
    }).join(' ')
  }), [r])
  return (
    <>
      {Array.from({ length: 25 }, (_, i) => <line key={`v${i}`} x1={i * 50} y1="0" x2={i * 50} y2={H} stroke={LINE} strokeWidth="1" opacity="0.1" />)}
      {Array.from({ length: 15 }, (_, i) => <line key={`h${i}`} x1="0" y1={i * 50} x2={W} y2={i * 50} stroke={LINE} strokeWidth="1" opacity="0.1" />)}
      {waves.map((p, i) => <polyline key={p} points={p} fill="none" stroke={LINE} strokeWidth={i === 4 ? 2 : 1} opacity={i === 4 ? 0.9 : 0.35} />)}
      {waves.filter((_, i) => i % 3 === 1).map((p, i) => <polyline key={`f${p}`} className="wp-flow" style={{ animationDelay: `-${i * 2}s` }} points={p} fill="none" stroke={LINE} strokeWidth="2.5" pathLength={100} />)}
    </>
  )
}

const HEX = '0123456789ABCDEF'

function DataRain({ r }: { r: () => number }) {
  const columns = useMemo(() => Array.from({ length: 34 }, (_, i) => ({
    x: i * 36 + 8,
    speed: 7 + r() * 11,
    delay: r() * 14,
    text: Array.from({ length: 30 }, () => HEX[Math.floor(r() * 16)] + HEX[Math.floor(r() * 16)]),
  })), [r])
  return (
    <>
      {columns.map((c) => (
        <g key={c.x} className="wp-rain" style={{ animationDuration: `${c.speed}s`, animationDelay: `-${c.delay}s` }} fontFamily="monospace" fontSize="15" fill={LINE}>
          {c.text.map((t, i) => <text key={i} x={c.x} y={i * 24} opacity={0.08 + (i / 30) * 0.55}>{t}</text>)}
        </g>
      ))}
    </>
  )
}

const KINDS = [Circuit, Terrain, Network, Hexes, Scope, DataRain]

/** A moving engineering wallpaper in the orb's colour and style; `seed` picks which one, so a course keeps its own. */
export function Wallpaper({ seed, kind }: { seed: number; kind?: number }) {
  const r = useMemo(() => rng(seed + 1), [seed])
  const Picture = KINDS[(kind ?? seed) % KINDS.length]
  return (
    <svg aria-hidden viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" className="wp absolute inset-0 size-full" style={{ background: 'color-mix(in srgb, var(--accent) 7%, #05060a)' }}>
      <Picture r={r} />
    </svg>
  )
}
