/**
 * Holo Hands gesture recognition: MediaPipe hand landmarks in, gestures out. Pure (no DOM, no camera), so the whole
 * vocabulary is tested with synthetic hands (scripts/holo.test.mjs). Thresholds are named and relative to the hand's
 * size, so distance from the camera does not matter. Pinch hysteresis (PINCH_IN/PINCH_OUT) after holo-gestures.
 */

/** One landmark, normalised to the camera frame (0..1), as MediaPipe gives it. */
export type Point = { x: number; y: number; z?: number }
/** The 21 landmarks of one hand. */
export type Hand = Point[]

export type Gesture =
  | { type: 'cursor'; x: number; y: number } // screen fraction 0..1, already mirrored
  | { type: 'lost' } // no hand in view
  | { type: 'select'; x: number; y: number }
  | { type: 'open'; x: number; y: number }
  | { type: 'dragStart'; x: number; y: number }
  | { type: 'dragMove'; x: number; y: number }
  | { type: 'dragEnd'; x: number; y: number }
  | { type: 'scroll'; dx: number; dy: number } // screen fractions: positive dy = content moves up (page scrolls down)
  | { type: 'zoom'; factor: number } // > 1 zooms in
  | { type: 'escape' }
  | { type: 'back' }
  | { type: 'reset' }

const WRIST = 0
const THUMB_TIP = 4
const INDEX_MCP = 5
const INDEX_TIP = 8
const MIDDLE_MCP = 9
const MIDDLE_TIP = 12
const FINGERS = [
  [8, 6], // index tip, pip
  [12, 10], // middle
  [16, 14], // ring
  [20, 18], // pinky
] as const

export const PINCH_IN = 0.28 // thumb-index distance / hand size: closer than this pinches
export const PINCH_OUT = 0.42 // ... and further than this lets go (near-misses are ignored)
export const REACH = 0.75 // index tip distance from the wrist, relative to its knuckle's, below which it is a fist
export const PINCH_FRAMES = 2 // a pinch starts or ends only after this many frames agree (no flicker)
export const TAP_MS = 450 // a pinch shorter than this, that barely moved, is a tap
export const DOUBLE_MS = 600 // two taps closer than this in time ...
export const DOUBLE_NEAR = 0.06 // ... and in place (screen fraction) are a double pinch
export const HOLD_MS = 260 // a pinch held this long (or moved) starts a drag
export const DRAG_MOVE = 0.03 // screen fraction the pinch may move before it counts as a drag
export const FLICK_SPEED = 1.6 // screen fractions per second for a two-finger flick
export const FLICK_SCREENS = 0.25 // a flick scrolls this many screens per unit of speed (speed capped at 5)
export const SWIPE_SPEED = 1.8 // open-hand swipe left (back): deliberate, so pulling the hand away does not count
export const STILL_MS = 600 // ✌ held this long
export const FIST_MS = 900 // a fist held this long closes: longer, a resting fist must not close a half-filled form
export const STILL_SPEED = 0.35 // ... while moving slower than this
export const COOLDOWN_MS = 350 // after a flick, swipe, fist or ✌, before the next one
// Cursor: the index knuckle (it barely moves when the fingers pinch), through a One Euro filter: heavy smoothing when
// the hand moves slowly (no jitter), light when it moves fast (no lag). A camera margin is left out, so the screen's
// edges are reachable without the hand leaving the frame, where tracking fails.
export const MARGIN = 0.12
export const MIN_CUTOFF = 1.0 // Hz
export const BETA = 8 // how quickly smoothing lets go as speed grows (screens per second)
export const JUMP = 0.18 // a one-frame jump bigger than this is a tracking glitch: ignored unless it repeats
export const LOST_MS = 250 // a hand missing for less than this is a dropped frame, not a hand gone

const dist = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y)
const size = (h: Hand) => dist(h[WRIST], h[MIDDLE_MCP]) || 1e-6
const extended = (h: Hand, tip: number, pip: number) => dist(h[tip], h[WRIST]) > dist(h[pip], h[WRIST]) * 1.08
const screen = (p: Point) => ({ x: 1 - p.x, y: p.y }) // the camera sees a mirror image
const mid = (a: Point, b: Point) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 })
const clamp = (v: number) => Math.min(1, Math.max(0, v))
/** Camera point -> screen fraction for the cursor: mirrored, with the frame's margin cut away. */
export const toScreen = (p: Point) => ({ x: clamp((1 - p.x - MARGIN) / (1 - 2 * MARGIN)), y: clamp((p.y - MARGIN) / (1 - 2 * MARGIN)) })

/** One Euro filter (Casiez et al. 2012) for one coordinate. */
class OneEuro {
  private x: number | null = null
  private dx = 0
  private t = 0
  filter(v: number, t: number): number {
    if (this.x === null) {
      this.x = v
      this.t = t
      return v
    }
    const dt = Math.max(1e-3, (t - this.t) / 1000)
    const alpha = (cutoff: number) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt))
    this.dx += alpha(1) * ((v - this.x) / dt - this.dx)
    this.x += alpha(MIN_CUTOFF + BETA * Math.abs(this.dx)) * (v - this.x)
    this.t = t
    return this.x
  }
  reset() {
    this.x = null
    this.dx = 0
  }
}

export type Pose = 'pinch' | 'point' | 'two' | 'vee' | 'three' | 'palm' | 'fist' | 'other'

/** The hand's shape this frame. Pinch wins over everything: it is the gesture people mean most often. */
export function pose(h: Hand, pinched: boolean, previous: Pose = 'other'): Pose {
  if (pinched) return 'pinch'
  const [index, middle, ring, pinky] = FINGERS.map(([tip, pip]) => extended(h, tip, pip))
  if (!index && !middle && !ring && !pinky) return 'fist'
  if (index && middle && ring && pinky) return 'palm'
  if (index && middle && !ring && !pinky) {
    if (dist(h[THUMB_TIP], h[MIDDLE_MCP]) / size(h) > 0.75) return 'three' // thumb out too: one-hand zoom
    // Hysteresis: between the two thresholds the pose stays what it was, so it does not flicker.
    const spread = dist(h[INDEX_TIP], h[MIDDLE_TIP]) / size(h)
    if (spread > 0.38) return 'vee'
    if (spread < 0.28) return 'two'
    return previous === 'vee' || previous === 'two' ? previous : 'two'
  }
  if (index && !middle && !ring && !pinky) return 'point'
  return 'other'
}

type Sample = { t: number; x: number; y: number }

const nearest = (hands: Hand[], at: { x: number; y: number }) =>
  hands.reduce((best, h) => (dist(toScreen(h[INDEX_MCP]), at) < dist(toScreen(best[INDEX_MCP]), at) ? h : best))

/** Feed one frame at a time; returns the gestures that frame produced. One instance per camera. */
export class Recognizer {
  /** What the last frame measured, for the Settings meter: thumb-index closeness and the pose. */
  last: { ratio: number | null; pose: Pose; pinched: boolean } = { ratio: null, pose: 'other', pinched: false }
  private cursor: { x: number; y: number } | null = null
  private fx = new OneEuro()
  private fy = new OneEuro()
  private jumps = 0
  private lostAt: number | null = null
  private flips = 0 // frames in a row that disagree with the current pinch state
  private pinched = false
  private pinchAt = 0
  private pinchFrom = { x: 0, y: 0 }
  private dragging = false
  private lastTap = -Infinity
  private lastTapAt = { x: -1, y: -1 }
  private trail: Sample[] = [] // recent positions of the pose's anchor, for flicks and swipes
  private poseNow: Pose = 'other'
  private poseSince = 0
  private fired = false // a held pose (✌, fist) fires once until the pose changes
  private cooldownUntil = 0
  private spread: number | null = null // one-hand zoom: thumb-middle span last frame
  private twoHands: number | null = null // two-hand zoom: index-index distance last frame

  frame(hands: Hand[], t: number): Gesture[] {
    const out: Gesture[] = []
    if (!hands.length) {
      this.lostAt ??= t
      if (t - this.lostAt < LOST_MS) return out // a dropped frame: keep everything as it was
      if (this.dragging && this.cursor) out.push({ type: 'dragEnd', ...this.cursor })
      if (this.cursor) out.push({ type: 'lost' })
      this.clear()
      return out
    }
    if (hands.length >= 2) {
      if (this.dragging && this.cursor) {
        out.push({ type: 'dragEnd', ...this.cursor })
        this.dragging = false
        this.pinched = false
      }
      const pointing = (h: Hand) => extended(h, 8, 6) && !extended(h, 16, 14) && !extended(h, 20, 18)
      const [a, b] = hands
      if (pointing(a) && pointing(b)) {
        const d = dist(a[INDEX_TIP], b[INDEX_TIP])
        if (this.twoHands) out.push({ type: 'zoom', factor: d / this.twoHands })
        this.twoHands = d
        return out
      }
    }
    this.lostAt = null
    this.twoHands = null
    // MediaPipe's hand order is not stable: follow the hand nearest to where the cursor was.
    const h = this.cursor && hands.length > 1 ? nearest(hands, this.cursor) : hands[0]
    const ratio = dist(h[THUMB_TIP], h[INDEX_TIP]) / size(h)
    // In a fist the thumb also rests on the index. A pinch curls the index too, so only a tip pulled right back into
    // the palm (as in a fist) is refused.
    const reaching = dist(h[INDEX_TIP], h[WRIST]) > dist(h[INDEX_MCP], h[WRIST]) * REACH
    const raw = toScreen(h[INDEX_MCP])
    if (this.cursor && dist(raw, this.cursor) > JUMP && ++this.jumps < 2) return out // a glitch, unless it repeats
    if (this.jumps >= 2) this.fx.reset(), this.fy.reset() // the hand really is somewhere else now
    this.jumps = 0
    const wasPinched = this.pinched
    const wants = reaching && (this.pinched ? ratio < PINCH_OUT : ratio < PINCH_IN)
    this.flips = wants === this.pinched ? 0 : this.flips + 1
    if (this.flips >= PINCH_FRAMES) {
      this.pinched = wants
      this.flips = 0
    }
    const p = pose(h, this.pinched, this.poseNow)
    this.last = { ratio, pose: p, pinched: this.pinched }
    this.cursor = { x: this.fx.filter(raw.x, t), y: this.fy.filter(raw.y, t) }
    // A pinch that has not become a drag keeps the cursor where it started, so taps land where the student aimed.
    const at = this.pinched && !this.dragging && wasPinched ? { ...this.pinchFrom } : { ...this.cursor }
    out.push({ type: 'cursor', ...at })

    if (p !== this.poseNow) {
      const fingersOnly = (p === 'two' || p === 'vee') && (this.poseNow === 'two' || this.poseNow === 'vee')
      this.poseNow = p
      this.poseSince = t
      this.fired = false
      if (!fingersOnly) this.trail = [] // two <-> vee is the same hand moving: keep its speed
      this.spread = null
    }
    const anchor = p === 'palm' || p === 'fist' ? screen(h[MIDDLE_MCP]) : p === 'two' || p === 'vee' ? screen(mid(h[INDEX_TIP], h[MIDDLE_TIP])) : screen(h[INDEX_MCP])
    this.trail.push({ t, ...anchor })
    while (this.trail.length && t - this.trail[0].t > 160) this.trail.shift()

    // Pinch family: tap = select, double tap = open, hold or move = drag.
    if (this.pinched && !wasPinched) {
      this.pinchAt = t
      this.pinchFrom = at
    } else if (this.pinched) {
      if (!this.dragging && (dist(this.cursor, this.pinchFrom) > DRAG_MOVE || t - this.pinchAt > HOLD_MS)) {
        this.dragging = true
        out.push({ type: 'dragStart', ...this.pinchFrom }, { type: 'dragMove', ...this.cursor })
      } else if (this.dragging) out.push({ type: 'dragMove', ...at })
    } else if (wasPinched) {
      if (this.dragging) {
        out.push({ type: 'dragEnd', ...at })
        this.dragging = false
      } else if (t - this.pinchAt < TAP_MS) {
        const tap = this.pinchFrom // where the pinch started: the cursor was held there
        if (t - this.lastTap < DOUBLE_MS && dist(tap, this.lastTapAt) < DOUBLE_NEAR) {
          out.push({ type: 'open', ...tap })
          this.lastTap = -Infinity
        } else {
          out.push({ type: 'select', ...tap })
          this.lastTap = t
          this.lastTapAt = tap
        }
      }
    }
    if (this.pinched) return out

    if (p === 'three') {
      const span = dist(h[THUMB_TIP], h[MIDDLE_TIP]) / size(h)
      if (this.spread) out.push({ type: 'zoom', factor: span / this.spread })
      this.spread = span
      return out
    }

    const v = this.velocity()
    if (t < this.cooldownUntil) return out
    if ((p === 'two' || p === 'vee') && Math.hypot(v.x, v.y) > FLICK_SPEED) {
      const amount = Math.min(Math.hypot(v.x, v.y), 5) * FLICK_SCREENS
      out.push(Math.abs(v.y) >= Math.abs(v.x) ? { type: 'scroll', dx: 0, dy: -Math.sign(v.y) * amount } : { type: 'scroll', dx: -Math.sign(v.x) * amount, dy: 0 })
      this.cool(t)
    } else if (p === 'palm' && v.x < -SWIPE_SPEED && Math.abs(v.x) > Math.abs(v.y) * 1.5) {
      out.push({ type: 'back' })
      this.cool(t)
    } else if ((p === 'vee' || p === 'fist') && !this.fired && t - this.poseSince > (p === 'fist' ? FIST_MS : STILL_MS) && Math.hypot(v.x, v.y) < STILL_SPEED) {
      out.push({ type: p === 'vee' ? 'reset' : 'escape' })
      this.fired = true
      this.cool(t)
    }
    return out
  }

  private velocity() {
    const a = this.trail[0]
    const b = this.trail[this.trail.length - 1]
    const dt = a && b ? (b.t - a.t) / 1000 : 0
    return dt > 0.04 ? { x: (b.x - a.x) / dt, y: (b.y - a.y) / dt } : { x: 0, y: 0 }
  }

  private cool(t: number) {
    this.cooldownUntil = t + COOLDOWN_MS
    this.trail = []
  }

  private clear() {
    this.cursor = null
    this.fx.reset()
    this.fy.reset()
    this.jumps = 0
    this.flips = 0
    this.lostAt = null
    this.pinched = false
    this.dragging = false
    this.trail = []
    this.poseNow = 'other'
    this.spread = null
    this.twoHands = null
  }
}
