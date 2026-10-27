import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { buildOrb } from './orb-scene'
import { cx } from './ui'

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking'

// How each assistant state moves the orb: spin multiplier, core size and pulse, ring breathing, ripple speed.
const MOTION: Record<OrbState, { spin: number; core: number; pulse: number; rate: number; breath: number; ripple: number; bright: number }> = {
  idle: { spin: 1, core: 0.62, pulse: 0.07, rate: 2.2, breath: 0, ripple: 0.08, bright: 1 },
  listening: { spin: 1.3, core: 0.78, pulse: 0.1, rate: 4, breath: 0.05, ripple: 0.12, bright: 1.35 },
  thinking: { spin: 3.4, core: 0.58, pulse: 0.05, rate: 7, breath: 0.015, ripple: 0.1, bright: 1.15 },
  speaking: { spin: 1.4, core: 0.7, pulse: 0.2, rate: 9, breath: 0.02, ripple: 0.45, bright: 1.25 },
}

/** The animated hologram (the user's reference images). Decorative; `state` mirrors the assistant. */
export default function Orb({ className, state = 'idle', hero = false, compact = false }: { className?: string; state?: OrbState; hero?: boolean; compact?: boolean }) {
  const host = useRef<HTMLDivElement>(null)
  const stateRef = useRef(state)
  const redraw = useRef<() => void>(undefined)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    stateRef.current = state
    redraw.current?.() // a still frame (reduced motion) still shows the new state
  }, [state])

  useEffect(() => {
    const el = host.current
    if (!el) return
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      setFailed(true) // no WebGL: use the canonical static capture
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    el.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(hero ? 38 : 35, 1, 0.1, 50)
    camera.position.set(0, hero ? 0.3 : 0, hero ? 4.7 : compact ? 4.8 : 5.2)
    camera.lookAt(0, hero ? -0.25 : 0, 0)
    const o = buildOrb(scene, hero, compact)
    const pointer = { x: 0, y: 0 }
    const m = { ...MOTION[stateRef.current] } // eased towards the target state every frame
    const angle = { globe: 0, particles: 0, rings: o.rings.map(() => 0), ripple: 0 }

    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const reduced = () => compact || motion.matches
    const clock = new THREE.Clock()
    const frame = () => {
      const dt = Math.min(clock.getDelta(), 0.1)
      const t = clock.elapsedTime
      const target = MOTION[stateRef.current]
      for (const k of Object.keys(m) as (keyof typeof m)[]) m[k] += (target[k] - m[k]) * (reduced() ? 1 : Math.min(1, dt * 4))
      angle.globe += dt * 0.15 * m.spin
      angle.particles -= dt * 0.08 * m.spin * (stateRef.current === 'thinking' ? 2.5 : 1)
      angle.ripple += dt * m.ripple
      o.globe.rotation.y = angle.globe
      o.particles.rotation.y = angle.particles
      o.rings.forEach((r, i) => {
        angle.rings[i] += dt * r.speed * m.spin
        r.obj.rotation.z = angle.rings[i]
      })
      o.ringHolder.scale.setScalar(1 + Math.sin(t * 3) * m.breath)
      const wave = stateRef.current === 'speaking' ? Math.abs(Math.sin(t * m.rate) * Math.sin(t * 2.3)) : (Math.sin(t * m.rate) + 1) / 2
      o.core.scale.setScalar(m.core + wave * m.pulse)
      ;(o.halo.material as THREE.SpriteMaterial).opacity = (o.light() ? 0.035 : 0.3) * m.bright
      o.ripples.forEach((r) => {
        const p = (r.userData.phase + angle.ripple) % 1
        r.scale.setScalar(0.3 + p * 1.9)
        ;(r.material as THREE.LineBasicMaterial).opacity = 0.55 * (1 - p) * m.bright
      })
      if (o.dust) o.dust.rotation.y = t * 0.01
      o.orb.rotation.x += (pointer.y * 0.25 - o.orb.rotation.x) * 0.05
      o.orb.rotation.y += (pointer.x * 0.4 - o.orb.rotation.y) * 0.05
      renderer.render(scene, camera)
    }
    redraw.current = () => { if (reduced()) frame() }
    const resize = () => {
      const { width, height } = el.getBoundingClientRect()
      if (!width || !height) return
      renderer.setSize(width, height, false)
      camera.aspect = width / height || 1
      camera.updateProjectionMatrix()
      if (reduced()) frame() // resizing clears the canvas; with no loop running, draw the still frame again
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)
    const onAccent = () => {
      o.recolour()
      if (reduced()) frame()
    }
    window.addEventListener('peace:accent', onAccent)
    window.addEventListener('peace:theme', onAccent)

    const onPointer = (e: PointerEvent) => {
      if (reduced()) return
      pointer.x = (e.clientX / window.innerWidth) * 2 - 1
      pointer.y = (e.clientY / window.innerHeight) * 2 - 1
    }
    // Animate only while on screen and the tab is visible; a still frame when the user prefers reduced motion.
    let visible = true
    const visibility = () => {
      renderer.setAnimationLoop(visible && !document.hidden && !reduced() ? frame : null)
      if (visible && !document.hidden && reduced()) frame()
    }
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      visibility()
    })
    document.addEventListener('visibilitychange', visibility)
    motion.addEventListener('change', visibility)
    io.observe(el)
    if (!compact) window.addEventListener('pointermove', onPointer)

    return () => {
      redraw.current = undefined
      io.disconnect()
      document.removeEventListener('visibilitychange', visibility)
      motion.removeEventListener('change', visibility)
      ro.disconnect()
      window.removeEventListener('pointermove', onPointer)
      window.removeEventListener('peace:accent', onAccent)
      window.removeEventListener('peace:theme', onAccent)
      renderer.setAnimationLoop(null)
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh
        mesh.geometry?.dispose()
        ;(mesh.material as THREE.Material | undefined)?.dispose()
      })
      o.glow.dispose()
      renderer.forceContextLoss() // free the WebGL context now, not at GC (browsers cap live contexts)
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [hero, compact])

  return (
    <div ref={host} aria-hidden data-state={state} className={cx('orb relative', hero && 'orb-hero', className)} data-quality={compact ? 'mark' : hero ? 'hero' : 'standard'}>
      {failed && <img src="/icon.svg" alt="" className="absolute inset-[10%] size-4/5 object-contain" />}
    </div>
  )
}
