import ForceGraph3D, { type ForceGraph3DInstance } from '3d-force-graph'
import { useEffect, useRef, useState } from 'react'
import { Color, Vector2 } from 'three'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import type { GraphLink, GraphNode } from '../lib/api'
import { cssVar, useAccentVersion, useTheme, withAlpha } from '../lib/theme'
import { kindColor } from './ui'

type Node = GraphNode & { x?: number; y?: number; z?: number }
type Link = Omit<GraphLink, 'source' | 'target'> & { source: string | Node; target: string | Node }
const endId = (end: string | Node) => (typeof end === 'string' ? end : end.id)

/** Interactive 3D graph (WebGL). `nodes`/`links` are already filtered; `selected` dims everything else. */
export default function KnowledgeGraph({
  nodes,
  links,
  selected,
  onSelect,
}: {
  nodes: GraphNode[]
  links: GraphLink[]
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const graph = useRef<ForceGraph3DInstance<Node, Link>>(undefined)
  const objects = useRef(new Map<string, Node>()) // same objects across filters, so nodes keep their place
  const select = useRef(onSelect)
  const refit = useRef(true) // frame the graph when the layout settles after new data
  const current = useRef(selected)
  const [failed, setFailed] = useState(false)
  const [theme] = useTheme()
  const bloomRef = useRef<UnrealBloomPass>(undefined)
  const accent = useAccentVersion() // recolour when the user changes the accent
  useEffect(() => {
    select.current = onSelect
  })

  useEffect(() => {
    const el = host.current
    if (!el) return
    let g: ForceGraph3DInstance<Node, Link>
    try {
      g = new ForceGraph3D(el, { controlType: 'orbit' }) as unknown as ForceGraph3DInstance<Node, Link> // the constructor is not generic
    } catch {
      setFailed(true)
      return
    }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    g.backgroundColor(cssVar('--scene-bg', '#090b0d'))
      .showNavInfo(false)
      .nodeId('id')
      .nodeVal((n) => (n.kind === 'course' ? 18 : n.kind === 'concept' ? 0.6 + n.degree * 0.15 : 1.5 + Math.min(n.degree, 20) * 0.3))
      .nodeResolution(10)
      .nodeOpacity(0.95)
      // Labels are scraped text: build the tooltip with textContent, never as HTML.
      .nodeLabel((n) => {
        const d = document.createElement('div')
        d.className = 'graph-tip'
        d.textContent = n.label
        return d
      })
      .linkOpacity(0.22)
      .linkDirectionalParticleSpeed(0.005)
      .linkDirectionalParticleWidth(1.6)
      .onNodeClick((n) => select.current(n.id))
      .onBackgroundClick(() => select.current(null))
      .cooldownTime(reduced ? 2000 : 6000)
      .onEngineStop(() => {
        if (refit.current && !current.current) g.zoomToFit(reduced ? 0 : 800, 30)
        refit.current = false
      })
    g.scene().background = new Color(cssVar('--scene-bg', '#090b0d'))
    const composer = g.postProcessingComposer()
    const bloom = new UnrealBloomPass(new Vector2(el.clientWidth, el.clientHeight), 1.1, 0.05, 0.45)
    const output = new OutputPass()
    bloom.enabled = document.documentElement.dataset.theme !== 'light'
    bloomRef.current = bloom
    composer.addPass(bloom)
    composer.addPass(output) // linear -> sRGB, so colours match the rest of the page
    graph.current = g

    const ro = new ResizeObserver(() => {
      if (el.clientWidth && el.clientHeight) g.width(el.clientWidth).height(el.clientHeight)
    })
    ro.observe(el)
    let visible = true
    const visibility = () => {
      if (visible && !document.hidden) g.resumeAnimation()
      else g.pauseAnimation()
    }
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      visibility()
    })
    io.observe(el)
    document.addEventListener('visibilitychange', visibility)
    return () => {
      ro.disconnect()
      io.disconnect()
      document.removeEventListener('visibilitychange', visibility)
      bloom.dispose() // the composer does not dispose passes it was given
      output.dispose()
      g.renderer().forceContextLoss()
      g._destructor()
      el.replaceChildren()
      graph.current = undefined
      bloomRef.current = undefined
    }
  }, [])

  // Data: reuse node objects by id; links are fresh (the library replaces their ends with node objects).
  useEffect(() => {
    const g = graph.current
    if (!g) return
    const cache = objects.current
    const data = {
      nodes: nodes.map((n) => Object.assign(cache.get(n.id) ?? (cache.set(n.id, { ...n }), cache.get(n.id)!), n)),
      links: links.map((l) => ({ ...l })),
    }
    refit.current = true
    g.graphData(data)
  }, [nodes, links])

  // Highlight the selection and its neighbours; particles flow on the links that matter.
  useEffect(() => {
    const g = graph.current
    const changed = current.current !== selected
    current.current = selected
    if (!g) return
    const near = new Set<string>()
    if (selected) {
      near.add(selected)
      for (const l of links) {
        if (l.source === selected) near.add(l.target)
        if (l.target === selected) near.add(l.source)
      }
    }
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const touches = (l: Link) => endId(l.source) === selected || endId(l.target) === selected
    const light = theme === 'light'
    const [pale, dim] = [cssVar(light ? '--holo' : '--holo-pale', '#ffe08a'), cssVar('--holo-dim', '#a5762e')]
    const background = cssVar('--scene-bg', '#090b0d')
    g.backgroundColor(background)
    g.scene().background = new Color(background)
    if (bloomRef.current) bloomRef.current.enabled = !light
    g.nodeColor((n) => (!selected || near.has(n.id) ? kindColor(n.kind) : withAlpha(dim, light ? 0.25 : 0.12)))
      .linkOpacity(light ? 0.42 : 0.22)
      .linkColor((l) => (selected && touches(l) ? pale : l.inferred ? cssVar('--graph-inferred', '#7cc6e8') : dim))
      .linkDirectionalParticleColor(() => pale)
      .linkWidth((l) => (selected && touches(l) ? 1.2 : l.relation === 'study_with' ? 0.8 : 0))
      .linkDirectionalParticles((l) => {
        if (reduced) return 0
        if (selected) return touches(l) ? 3 : 0
        return l.relation === 'study_with' ? 3 : l.relation === 'mentions' ? 0 : 1
      })
    if (selected && changed) {
      const n = objects.current.get(selected)
      if (n?.x !== undefined) {
        const ratio = 1 + 180 / Math.max(1, Math.hypot(n.x, n.y ?? 0, n.z ?? 0))
        g.cameraPosition({ x: n.x * ratio, y: (n.y ?? 0) * ratio, z: (n.z ?? 0) * ratio }, { x: n.x, y: n.y ?? 0, z: n.z ?? 0 }, reduced ? 0 : 1200)
      }
    }
  }, [selected, links, accent, theme])

  return (
    <div ref={host} className="holo-canvas absolute inset-0" data-testid="knowledge-graph">
      {failed && <p className="p-6 text-[13px] text-muted">Este navegador não consegue desenhar o grafo 3D (WebGL indisponível). Usa a pesquisa e a lista ao lado.</p>}
    </div>
  )
}
