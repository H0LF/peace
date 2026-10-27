/** Static fallback projected from the actual orb scene; no browser, GPU, or redesign required. */
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import * as THREE from 'three'
import { buildOrb } from '../src/components/orb-scene.ts'

const palette = { '--holo': '#ffb547', '--holo-pale': '#ffe08a' }
globalThis.document = {
  documentElement: { dataset: { theme: 'dark' } },
  createElement: () => ({ getContext: () => ({ createRadialGradient: () => ({ addColorStop() {} }), fillRect() {} }) }),
}
globalThis.getComputedStyle = () => ({ getPropertyValue: (key) => palette[key] || '' })
let seed = 41
Math.random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296)
const scene = new THREE.Scene()
const orb = buildOrb(scene, false, true)
orb.orb.rotation.set(0.1, 0.2, 0)
const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 50)
camera.position.z = 4.8
camera.updateMatrixWorld()
scene.updateMatrixWorld(true)
const project = (v, obj) => v.applyMatrix4(obj.matrixWorld).project(camera)
const xy = (v) => `${(256 + v.x * 256).toFixed(2)},${(256 - v.y * 256).toFixed(2)}`
const elements = []
scene.traverse((obj) => {
  if (!obj.isLine && !obj.isPoints) return
  const position = obj.geometry.getAttribute('position')
  if (obj.isPoints) {
    const colors = obj.geometry.getAttribute('color')
    for (let i = 0; i < position.count; i++) {
      const v = project(new THREE.Vector3().fromBufferAttribute(position, i), obj)
      const color = new THREE.Color().fromBufferAttribute(colors, i).getStyle()
      const [x, y] = xy(v).split(',')
      elements.push(`<circle cx="${x}" cy="${y}" r="1" fill="${color}" opacity=".75"/>`)
    }
  } else {
    const points = []
    for (let i = 0; i < position.count; i++) points.push(xy(project(new THREE.Vector3().fromBufferAttribute(position, i), obj)))
    const width = obj.isLineSegments ? '.75' : '1.2'
    if (obj.isLineLoop) points.push(points[0])
    const path = obj.isLineSegments ? points.map((p, i) => `${i % 2 ? 'L' : 'M'}${p}`).join('') : `M${points.join('L')}`
    elements.push(`<path d="${path}" fill="none" stroke="${obj.material.color.getStyle()}" stroke-width="${width}" opacity="${obj.material.opacity.toFixed(2)}"${obj.material.isLineDashedMaterial ? ' stroke-dasharray="4 3"' : ''}/>`)
  }
})
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><style>@media(prefers-color-scheme:light){path{stroke:#a8650a}circle:not([fill^="url"]){fill:#a8650a}}</style><defs><radialGradient id="glow"><stop stop-color="#fff1d0"/><stop offset=".25" stop-color="#ffb547" stop-opacity=".45"/><stop offset="1" stop-color="#ffb547" stop-opacity="0"/></radialGradient></defs><circle cx="256" cy="256" r="90" fill="url(#glow)"/>${elements.join('')}<circle cx="256" cy="256" r="36" fill="url(#glow)"/></svg>\n`
writeFileSync(fileURLToPath(new URL('../public/icon.svg', import.meta.url)), svg)
// The same paths need a minimum screen-space stroke to survive a 16–32 px favicon.
const favicon = svg.replace('<defs>', '<style>path{stroke-width:8;opacity:.6}</style><defs>')
writeFileSync(fileURLToPath(new URL('../public/favicon.svg', import.meta.url)), favicon)
orb.glow.dispose()
scene.traverse((obj) => { obj.geometry?.dispose(); obj.material?.dispose() })
console.log('Canonical orb icon projected to public/icon.svg')
