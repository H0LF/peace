import assert from 'node:assert/strict'
import { test } from 'node:test'
import { MARGIN, Recognizer, pose } from '../src/holo/gestures.ts'

const sx = (cameraX) => (1 - cameraX - MARGIN) / (1 - 2 * MARGIN) // where a camera x lands on screen

/** A synthetic right hand (camera coordinates). `up` lists extended fingers: i m r p. */
function hand({ up = 'i', thumb = 'in', x = 0.5, y = 0.5, vee = false, span = 0.12 } = {}) {
  const h = Array.from({ length: 21 }, () => ({ x, y }))
  h[0] = { x, y: y + 0.15 } // wrist; wrist->middle MCP = 0.15 = hand size
  const finger = (mcp, dx, key, tipDx = 0) => {
    h[mcp] = { x: x + dx, y }
    h[mcp + 1] = { x: x + dx, y: y - 0.05 } // pip
    h[mcp + 2] = { x: x + dx, y: y - 0.08 }
    h[mcp + 3] = up.includes(key) ? { x: x + dx + tipDx, y: y - 0.12 } : { x: x + dx, y: y + 0.07 } // curled: tip deep in the palm
  }
  finger(5, -0.03, 'i')
  finger(9, 0, 'm', vee ? 0.04 : 0)
  finger(13, 0.03, 'r')
  finger(17, 0.06, 'p')
  h[4] = thumb === 'pinch' ? { x: h[8].x + 0.01, y: h[8].y + 0.01 } : thumb === 'out' ? { x: x - span, y: y + 0.02 } : { x: x - 0.04, y: y + 0.06 }
  return h
}

/** Runs frames (hands per frame, `dt` ms apart) and returns every gesture type produced. */
function run(frames, dt = 33, r = new Recognizer(), t0 = 0) {
  const out = []
  frames.forEach((hands, i) => out.push(...r.frame(hands, t0 + i * dt)))
  return out
}
const types = (gs) => gs.map((g) => g.type).filter((t) => t !== 'cursor')
const repeat = (n, f) => Array.from({ length: n }, (_, i) => f(i))

test('poses', () => {
  assert.equal(pose(hand(), false), 'point')
  assert.equal(pose(hand({ up: 'im' }), false), 'two')
  assert.equal(pose(hand({ up: 'im', vee: true }), false), 'vee')
  assert.equal(pose(hand({ up: 'im', thumb: 'out' }), false), 'three')
  assert.equal(pose(hand({ up: 'imrp' }), false), 'palm')
  assert.equal(pose(hand({ up: '' }), false), 'fist')
})

test('the cursor follows the index knuckle, mirrored, with the camera margin cut away', () => {
  const [c] = run([[hand({ x: 0.3 })]])
  assert.equal(c.type, 'cursor')
  assert.ok(Math.abs(c.x - sx(0.27)) < 1e-9) // the knuckle is 0.03 left of the hand centre
})

test('the cursor stays still for a still hand with tracking noise, and ignores a one-frame glitch', () => {
  const noisy = repeat(30, (i) => [hand({ x: 0.5 + (i % 2 ? 0.006 : -0.006), y: 0.5 + (i % 3 ? 0.004 : -0.004) })])
  const xs = run(noisy).filter((g) => g.type === 'cursor').slice(10).map((g) => g.x)
  assert.ok(Math.max(...xs) - Math.min(...xs) < 0.006) // raw noise is 0.012 / 0.76 = 0.016 on screen
  const glitch = run([[hand()], [hand()], [hand({ x: 0.9 })], [hand()]]).filter((g) => g.type === 'cursor')
  assert.ok(glitch.every((c) => Math.abs(c.x - sx(0.47)) < 0.01)) // the teleport frame never reached the cursor
})

test('a dropped frame does not reset the hand', () => {
  assert.deepEqual(types(run([[hand()], [], [hand()]])), [])
})

const tap = [[hand()], [hand({ thumb: 'pinch' })], [hand({ thumb: 'pinch' })], [hand({ thumb: 'pinch' })], [hand()], [hand()]]

test('a quick pinch selects, two quick pinches open', () => {
  assert.deepEqual(types(run([...tap, ...tap])), ['select', 'open'])
})

test('a one-frame pinch flicker is not a tap', () => {
  assert.deepEqual(types(run([[hand()], [hand({ thumb: 'pinch' })], [hand()], [hand()]])), [])
})

test('two taps in different places are two selections, not an open', () => {
  const there = tap.map(([h]) => [h.map((p) => ({ x: p.x - 0.1, y: p.y }))])
  assert.deepEqual(types(run([...tap, ...there])), ['select', 'select'])
})

test('a near-miss is not a pinch', () => {
  const near = hand()
  near[4] = { x: near[8].x + 0.05, y: near[8].y } // 0.05 / 0.15 = 0.33: between PINCH_IN and PINCH_OUT
  assert.deepEqual(types(run([[hand()], [near], [near], [hand()]])), [])
})

test('a held or moving pinch drags', () => {
  const held = repeat(14, () => [hand({ thumb: 'pinch' })])
  const g = types(run([[hand()], ...held, [hand()], [hand()]]))
  assert.equal(g[0], 'dragStart')
  assert.equal(g.at(-1), 'dragEnd')
  assert.ok(g.slice(1, -1).every((t) => t === 'dragMove'))
  const moving = repeat(8, (i) => [hand({ thumb: 'pinch', x: 0.5 - i * 0.03 })])
  const m = types(run([[hand()], ...moving, [hand({ x: 0.26 })], [hand({ x: 0.26 })]]))
  assert.equal(m[0], 'dragStart')
  assert.equal(m.at(-1), 'dragEnd')
})

test('losing the hand mid-drag ends the drag', () => {
  assert.deepEqual(types(run([...repeat(14, () => [hand({ thumb: 'pinch' })]), ...repeat(10, () => [])])).slice(-2), ['dragEnd', 'lost'])
})

test('two-finger flicks scroll in their direction', () => {
  const up = types(run(repeat(6, (i) => [hand({ up: 'im', y: 0.6 - i * 0.07 })])))
  assert.deepEqual(up, ['scroll'])
  const [s] = run(repeat(6, (i) => [hand({ up: 'im', y: 0.6 - i * 0.07 })])).filter((g) => g.type === 'scroll')
  assert.ok(s.dy > 0 && s.dx === 0) // fingers up: the content moves up, like a touch screen
  const [side] = run(repeat(6, (i) => [hand({ up: 'im', x: 0.3 + i * 0.07 })])).filter((g) => g.type === 'scroll')
  assert.ok(side.dx > 0 && side.dy === 0) // camera x grows = hand moves to the screen's left: content moves left
})

test('slow two-finger movement does not scroll', () => {
  assert.deepEqual(types(run(repeat(20, (i) => [hand({ up: 'im', y: 0.6 - i * 0.003 })]))), [])
})

test('an open hand swiped left goes back', () => {
  assert.deepEqual(types(run(repeat(6, (i) => [hand({ up: 'imrp', x: 0.3 + i * 0.07 })]))), ['back'])
  assert.deepEqual(types(run(repeat(6, (i) => [hand({ up: 'imrp', x: 0.7 - i * 0.07 })]))), []) // right: nothing
})

test('a held fist closes once, a held peace sign resets once', () => {
  assert.deepEqual(types(run(repeat(40, () => [hand({ up: '' })]))), ['escape'])
  assert.deepEqual(types(run(repeat(40, () => [hand({ up: 'im', vee: true })]))), ['reset'])
  assert.deepEqual(types(run(repeat(10, () => [hand({ up: 'im', vee: true })]))), []) // not held long enough
})

test('one hand opening zooms in, closing zooms out', () => {
  const opening = run(repeat(5, (i) => [hand({ up: 'im', thumb: 'out', span: 0.12 + i * 0.02 })])).filter((g) => g.type === 'zoom')
  assert.ok(opening.length >= 3 && opening.every((g) => g.factor > 1))
  const closing = run(repeat(5, (i) => [hand({ up: 'im', thumb: 'out', span: 0.2 - i * 0.02 })])).filter((g) => g.type === 'zoom')
  assert.ok(closing.every((g) => g.factor < 1))
})

test('two index fingers apart zoom in, together zoom out', () => {
  const apart = run(repeat(5, (i) => [hand({ x: 0.4 - i * 0.02 }), hand({ x: 0.6 + i * 0.02 })])).filter((g) => g.type === 'zoom')
  assert.ok(apart.length === 4 && apart.every((g) => g.factor > 1))
  const together = run(repeat(5, (i) => [hand({ x: 0.3 + i * 0.02 }), hand({ x: 0.7 - i * 0.02 })])).filter((g) => g.type === 'zoom')
  assert.ok(together.every((g) => g.factor < 1))
})

test('a flick with the two fingers spread still scrolls (no surprise reset)', () => {
  const g = types(run(repeat(6, (i) => [hand({ up: 'im', vee: true, y: 0.6 - i * 0.07 })])))
  assert.deepEqual(g, ['scroll'])
})

test('a fist must be held longer than a peace sign', () => {
  assert.deepEqual(types(run(repeat(24, () => [hand({ up: '' })]))), []) // ~0.8 s: not yet
})

test('with a second hand resting in view, the cursor stays on the hand it was following', () => {
  const r = new Recognizer()
  run([[hand({ x: 0.3 })]], 33, r)
  const out = run(repeat(5, () => [hand({ x: 0.7, up: 'imrp' }), hand({ x: 0.3 })]), 33, r, 100)
  const cursors = out.filter((g) => g.type === 'cursor')
  assert.ok(cursors.every((c) => Math.abs(c.x - sx(0.27)) < 0.02))
})

test('a second hand pointing ends a drag before zooming', () => {
  const r = new Recognizer()
  run(repeat(14, () => [hand({ thumb: 'pinch' })]), 33, r)
  assert.deepEqual(types(run([[hand({ x: 0.4 }), hand({ x: 0.6 })]], 33, r, 500)), ['dragEnd'])
})
