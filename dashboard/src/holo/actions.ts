/**
 * What each Holo Hands gesture does to the page, as the mouse and keyboard would: hover, click, drag, scroll, Esc,
 * back. Pages that know better listen for `peace:reset` (✌ default positions) and `peace:zoom`.
 */
import type { Gesture } from './gestures'

type At = { x: number; y: number }

const px = (g: At) => ({ clientX: g.x * innerWidth, clientY: g.y * innerHeight })
const under = (g: At) => document.elementFromPoint(px(g).clientX, px(g).clientY)

/** Mouse-shaped events: pointer id 1 is the mouse's, so pages that capture the pointer (calendar drag) accept them. */
function fire(el: Element, type: string, g: At, buttons = 0) {
  const init = { bubbles: true, cancelable: true, composed: true, ...px(g), button: 0, buttons, pointerId: 1, pointerType: 'mouse', isPrimary: true }
  el.dispatchEvent(type.startsWith('pointer') ? new PointerEvent(type, init) : new MouseEvent(type, init))
}

/** Inside the knowledge graph, gestures steer the 3D view itself (KnowledgeGraph listens for these). */
const inGraph = (el: Element | null) => !!el?.closest('[data-holo-graph]')
const tell = (name: string, detail?: object) => window.dispatchEvent(new CustomEvent(name, { detail }))

const CLICKABLE = 'a,button,input,select,textarea,label,summary,[role=button],[role=tab],[role=link],[role=menuitem],[tabindex]'

function scrollable(el: Element | null, axis: 'x' | 'y'): Element {
  for (let e = el; e && e !== document.body; e = e.parentElement) {
    const style = getComputedStyle(e)
    const overflow = axis === 'y' ? style.overflowY : style.overflowX
    const room = axis === 'y' ? e.scrollHeight > e.clientHeight : e.scrollWidth > e.clientWidth
    if (room && /(auto|scroll)/.test(overflow)) return e
  }
  return document.scrollingElement ?? document.documentElement
}

/** One performer per camera session: remembers what is hovered and what is being dragged. */
export function performer(back: () => void) {
  let hovered: Element | null = null
  let dragging: Element | null = null
  let orbit: At | null = null // dragging inside the graph: the last point, to send rotation deltas
  return (g: Gesture) => {
    switch (g.type) {
      case 'cursor': {
        if (dragging) return
        const el = under(g)
        if (el !== hovered) {
          if (hovered) fire(hovered, 'pointerout', g), fire(hovered, 'mouseout', g)
          if (el) fire(el, 'pointerover', g), fire(el, 'mouseover', g)
          hovered = el
        }
        if (el) fire(el, 'pointermove', g), fire(el, 'mousemove', g)
        return
      }
      case 'lost':
        if (hovered) fire(hovered, 'pointerout', { x: -1, y: -1 })
        hovered = null
        return
      case 'select': {
        const el = under(g)
        if (el && inGraph(el)) {
          fire(el, 'pointerdown', g, 1), fire(el, 'pointerup', g), fire(el, 'click', g) // the graph picks the node
          return
        }
        const target = el?.closest(CLICKABLE) as HTMLElement | null
        target?.focus({ preventScroll: true })
        return
      }
      case 'open': {
        const el = under(g)
        if (!el) return
        if (inGraph(el)) {
          fire(el, 'pointerdown', g, 1), fire(el, 'pointerup', g), fire(el, 'click', g)
          setTimeout(() => tell('peace:open'), 60) // after the page has the node this pinch picked
          return
        }
        fire(el, 'pointerdown', g, 1), fire(el, 'mousedown', g, 1), fire(el, 'pointerup', g), fire(el, 'mouseup', g)
        const target = el.closest(CLICKABLE) as HTMLElement | null
        if (target) {
          target.focus({ preventScroll: true })
          target.click()
        } else {
          fire(el, 'click', g)
          fire(el, 'dblclick', g) // free space that reacts to a double click (a new calendar event)
        }
        return
      }
      case 'dragStart': {
        if (inGraph(under(g))) {
          orbit = g
          return
        }
        dragging = under(g)
        if (dragging) fire(dragging, 'pointerdown', g, 1), fire(dragging, 'mousedown', g, 1)
        return
      }
      case 'dragMove':
        if (orbit) {
          tell('peace:orbit', { dx: g.x - orbit.x, dy: g.y - orbit.y })
          orbit = g
          return
        }
        if (dragging) fire(dragging, 'pointermove', g, 1), fire(dragging, 'mousemove', g, 1)
        return
      case 'dragEnd':
        orbit = null
        if (dragging) fire(dragging, 'pointerup', g), fire(dragging, 'mouseup', g)
        dragging = null
        return
      case 'scroll': {
        const at = hovered ?? document.body
        if (g.dy) scrollable(at, 'y').scrollBy({ top: g.dy * innerHeight, behavior: 'smooth' })
        if (g.dx) scrollable(at, 'x').scrollBy({ left: g.dx * innerWidth, behavior: 'smooth' })
        return
      }
      case 'zoom':
        tell('peace:zoom', { factor: g.factor })
        return
      case 'escape': {
        const dialogs = document.querySelectorAll<HTMLDialogElement>('dialog[open]')
        if (dialogs.length) dialogs[dialogs.length - 1].close()
        else (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
        return
      }
      case 'back':
        back()
        return
      case 'reset': {
        const e = new CustomEvent('peace:reset', { cancelable: true })
        window.dispatchEvent(e)
        if (!e.defaultPrevented) {
          document.scrollingElement?.scrollTo({ top: 0, left: 0, behavior: 'smooth' })
          if (hovered) scrollable(hovered, 'y').scrollTo({ top: 0, behavior: 'smooth' })
        }
      }
    }
  }
}
