import * as THREE from 'three'
import { cssVar } from '../lib/theme.ts'

const accent = () => new THREE.Color(cssVar('--holo', '#ffb547'))

/** A circle of `n` points in the XY plane. */
function circle(radius: number, n = 128): THREE.BufferGeometry {
  const pts = Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2
    return new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0)
  })
  return new THREE.BufferGeometry().setFromPoints(pts)
}

/** Short radial ticks around a ring, like a dial. */
function ticks(radius: number, n: number, length: number): THREE.BufferGeometry {
  const pts: THREE.Vector3[] = []
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2
    const l = i % 5 === 0 ? length * 2 : length
    pts.push(new THREE.Vector3(Math.cos(a) * radius, Math.sin(a) * radius, 0), new THREE.Vector3(Math.cos(a) * (radius + l), Math.sin(a) * (radius + l), 0))
  }
  return new THREE.BufferGeometry().setFromPoints(pts)
}

function glowTexture(): THREE.Texture {
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64)
  grad.addColorStop(0, 'rgba(255,255,255,1)')
  grad.addColorStop(0.25, 'rgba(255,255,255,0.45)')
  grad.addColorStop(1, 'rgba(255,255,255,0)')
  g.fillStyle = grad
  g.fillRect(0, 0, 128, 128)
  return new THREE.CanvasTexture(c)
}

export function buildOrb(scene: THREE.Scene, hero: boolean, compact = false) {
  const tinted: THREE.Material[] = [] // recoloured when the accent changes
  const glow = hero ? 1.45 : 1 // the big orb needs brighter lines to read like the reference
  const line = (opacity: number, dashed = false) => {
    const params = { transparent: true, opacity: Math.min(1, opacity * glow), blending: THREE.AdditiveBlending, depthWrite: false }
    const m = dashed ? new THREE.LineDashedMaterial({ ...params, dashSize: 0.06, gapSize: 0.04 }) : new THREE.LineBasicMaterial(params)
    tinted.push(m)
    return m
  }

  const orb = new THREE.Group()
  scene.add(orb)

  // Wireframe globe: latitude rings and meridians.
  const globe = new THREE.Group()
  for (let i = 1; i < 9; i++) {
    const lat = -Math.PI / 2 + (i / 9) * Math.PI
    const ring = new THREE.LineLoop(circle(Math.cos(lat), 96), line(0.22))
    ring.rotation.x = Math.PI / 2
    ring.position.y = Math.sin(lat)
    globe.add(ring)
  }
  for (let i = 0; i < 12; i++) {
    const m = new THREE.LineLoop(circle(1, 96), line(0.16))
    m.rotation.y = (i / 12) * Math.PI
    globe.add(m)
  }
  orb.add(globe)

  // Orbital rings at different tilts, some dashed, some with dial ticks.
  const rings: { obj: THREE.Object3D; speed: number }[] = []
  const ringHolder = new THREE.Group()
  const specs = [
    { r: 1.18, tilt: [1.2, 0.2], speed: 0.35, opacity: 0.7, dashed: false, tick: 0 },
    { r: 1.32, tilt: [0.4, -0.6], speed: -0.22, opacity: 0.45, dashed: true, tick: 0 },
    { r: 0.62, tilt: [0.9, 0.9], speed: 0.8, opacity: 0.8, dashed: false, tick: 0 },
    { r: 1.05, tilt: [-0.3, 0.5], speed: -0.5, opacity: 0.4, dashed: false, tick: 60 },
    { r: 0.38, tilt: [-1.1, 0.3], speed: -1.2, opacity: 0.9, dashed: true, tick: 0 },
  ]
  for (const s of specs) {
    const holder = new THREE.Group()
    holder.rotation.set(s.tilt[0], s.tilt[1], 0)
    const ring = new THREE.LineLoop(circle(s.r), line(s.opacity, s.dashed))
    if (s.dashed) ring.computeLineDistances()
    const spin = new THREE.Group()
    spin.add(ring)
    if (s.tick) spin.add(new THREE.LineSegments(ticks(s.r, s.tick, 0.035), line(s.opacity)))
    holder.add(spin)
    ringHolder.add(holder)
    rings.push({ obj: spin, speed: s.speed })
  }
  orb.add(ringHolder)

  // Shell of light particles (denser near the equator, like the reference).
  const n = compact ? 500 : hero ? 3600 : 1400
  const pos = new Float32Array(n * 3)
  const mix = new Float32Array(n) // 0 = accent, 1 = pale, kept to recolour
  for (let i = 0; i < n; i++) {
    const u = Math.random() * 2 - 1
    const y = Math.sign(u) * Math.abs(u) ** 1.6
    const a = Math.random() * Math.PI * 2
    const r = 0.92 + Math.random() * 0.22
    const h = Math.sqrt(1 - y * y) * r
    pos.set([Math.cos(a) * h, y * r, Math.sin(a) * h], i * 3)
    mix[i] = Math.random()
  }
  const pg = new THREE.BufferGeometry()
  pg.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  pg.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3), 3))
  const particles = new THREE.Points(
    pg,
    new THREE.PointsMaterial({ size: compact ? 0.05 : hero ? 0.022 : 0.028, vertexColors: true, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending, depthWrite: false }),
  )
  orb.add(particles)

  // Bright core with a soft halo.
  const glowMap = glowTexture()
  const core = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowMap, color: accent(), blending: THREE.AdditiveBlending, depthWrite: false }))
  core.scale.setScalar(0.7)
  const haloMat = new THREE.SpriteMaterial({ map: glowMap, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false })
  tinted.push(haloMat)
  const halo = new THREE.Sprite(haloMat)
  halo.scale.setScalar(hero ? 3.6 : 3.2)
  scene.add(halo, core)

  // Hero only: a still outline circle, floor ripples, vertical light beams and drifting dust.
  const ripples: THREE.LineLoop[] = []
  let dust: THREE.Points | undefined
  if (hero) {
    const outline = new THREE.LineLoop(circle(1.42, 160), line(0.28))
    scene.add(outline)
    const floor = new THREE.Group()
    floor.position.y = -1.3
    floor.rotation.x = Math.PI / 2
    for (let i = 0; i < 7; i++) {
      const r = new THREE.LineLoop(circle(1, 160), line(0.5))
      r.userData.phase = i / 7
      floor.add(r)
      ripples.push(r)
    }
    const spot = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowMap, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false }))
    tinted.push(spot.material)
    spot.scale.set(2.6, 0.5, 1)
    spot.position.y = -1.3
    scene.add(floor, spot)

    const beams: THREE.Vector3[] = []
    for (let i = 0; i < 7; i++) {
      const x = (Math.random() - 0.5) * 6
      const z = -1.5 - Math.random() * 2
      beams.push(new THREE.Vector3(x, 4, z), new THREE.Vector3(x, 0.4 + Math.random() * 2.5, z))
    }
    scene.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(beams), line(0.18)))

    const d = 500
    const dp = new Float32Array(d * 3)
    for (let i = 0; i < d; i++) dp.set([(Math.random() - 0.5) * 9, (Math.random() - 0.5) * 6, -Math.random() * 4], i * 3)
    const dg = new THREE.BufferGeometry()
    dg.setAttribute('position', new THREE.BufferAttribute(dp, 3))
    const dm = new THREE.PointsMaterial({ size: 0.02, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false })
    tinted.push(dm)
    dust = new THREE.Points(dg, dm)
    scene.add(dust)
  }

  let light = false
  const recolour = () => {
    light = document.documentElement.dataset.theme === 'light'
    haloMat.opacity = light ? 0.035 : 0.3
    const c = accent()
    const pale = new THREE.Color(cssVar('--holo-pale', '#ffe08a'))
    scene.traverse((obj) => {
      const material = (obj as THREE.Mesh).material as THREE.Material | undefined
      if (material) {
        material.blending = light ? THREE.NormalBlending : THREE.AdditiveBlending
        material.needsUpdate = true
      }
    })
    core.material.color.copy(light ? pale : new THREE.Color('#fff1d0'))
    for (const m of tinted) (m as THREE.LineBasicMaterial).color.copy(c)
    const col = pg.getAttribute('color') as THREE.BufferAttribute
    for (let i = 0; i < n; i++) {
      const p = c.clone().lerp(pale, mix[i])
      col.setXYZ(i, p.r, p.g, p.b)
    }
    col.needsUpdate = true
  }
  recolour()

  return { orb, globe, ringHolder, rings, particles, core, halo, ripples, dust, glow: glowMap, recolour, light: () => light }
}
