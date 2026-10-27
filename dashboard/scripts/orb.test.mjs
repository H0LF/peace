import assert from 'node:assert/strict'
import { test } from 'node:test'
import * as THREE from 'three'
import { buildOrb } from '../src/components/orb-scene.ts'
import { accentCss, accentVariants, contrast } from '../src/lib/theme.ts'

let palette = { '--holo': '#ffb547', '--holo-pale': '#ffe08a' }
globalThis.document = {
  documentElement: { dataset: { theme: 'dark' } },
  createElement: () => ({ getContext: () => ({ createRadialGradient: () => ({ addColorStop() {} }), fillRect() {} }) }),
}
globalThis.getComputedStyle = () => ({ getPropertyValue: (key) => palette[key] || '' })
const dispose = (scene, orb) => {
  scene.traverse((obj) => { obj.geometry?.dispose(); obj.material?.dispose() })
  orb.glow.dispose()
}

test('light materials keep canonical geometry, use normal alpha, and have a restrained halo', () => {
  const scene = new THREE.Scene()
  const orb = buildOrb(scene, true)
  const geometries = []
  scene.traverse((obj) => { if (obj.geometry) geometries.push(obj.geometry) })
  assert.equal(orb.halo.material.blending, THREE.AdditiveBlending)
  document.documentElement.dataset.theme = 'light'
  palette = { '--holo': '#875924', '--holo-pale': '#9f7a50' }
  orb.recolour()
  assert.equal(scene.background, null)
  assert.equal(orb.halo.material.blending, THREE.NormalBlending)
  assert.ok(orb.halo.material.opacity < 0.05)
  assert.equal(orb.light(), true)
  const after = []
  scene.traverse((obj) => {
    if (obj.geometry) after.push(obj.geometry)
    if (obj.material) assert.equal(obj.material.blending, THREE.NormalBlending)
  })
  assert.deepEqual(after, geometries)
  document.documentElement.dataset.theme = 'dark'
  orb.recolour()
  assert.equal(orb.light(), false)
  assert.equal(orb.halo.material.blending, THREE.AdditiveBlending)
  dispose(scene, orb)
})

test('all orb sizes reuse globe and orbital geometry with bounded particle counts', () => {
  const counts = []
  for (const [hero, compact] of [[true, false], [false, false], [false, true]]) {
    const scene = new THREE.Scene()
    const orb = buildOrb(scene, hero, compact)
    assert.equal(orb.globe.children.length, 20)
    assert.equal(orb.rings.length, 5)
    counts.push(orb.particles.geometry.getAttribute('position').count)
    dispose(scene, orb)
  }
  assert.deepEqual(counts, [3600, 1400, 500])
})

test('gold, purple, cyan, red, green and arbitrary accents stay readable in both themes', () => {
  for (const hex of ['#ffb547', '#a78bfa', '#6fd3ff', '#ff3333', '#4ade80', '#123456', '#ffffff', '#000000']) {
    const v = accentVariants(hex)
    assert.ok(contrast(v.dark, '#12100d') >= 4.5, `${hex} dark`)
    assert.ok(contrast(v.light, '#f5f5f7') >= 4, `${hex} light scene`)
    assert.ok(contrast(v.light, '#ffffff') >= 4.5, `${hex} light`)
    const css = accentCss(hex)
    assert.ok(css.includes(`--holo: ${v.dark};`))
    assert.ok(css.includes(`--holo: ${v.light};`))
  }
})
