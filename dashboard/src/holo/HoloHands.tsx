/**
 * Holo Hands: the webcam's hand landmarks drive the dashboard (see GLOSSARY.md, .scratch/holo-hands/spec.md).
 * One switch, kept by the server, so the sidebar button, the H key and the assistant ("liga as mãos") agree; the open
 * dashboard follows it every 2 s. The camera runs only while it is on, and frames never leave the browser.
 */
import { Hand } from 'lucide-react'
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { useToast } from '../components/ui'
import { api } from '../lib/api'
import { performer } from './actions'
import { Recognizer, type Gesture } from './gestures'

const FRAME_MS = 33 // ~30 detections a second is plenty for a hand and spares the GPU
const POLL_MS = 2000

export type Reading = { ratio: number | null; pose: string; pinched: boolean; gesture: string | null }
type Holo = {
  on: boolean
  starting: boolean
  toggle: () => void
  /** The latest measurement, for the Settings meter (a ref: read it on a timer, it changes 30 times a second). */
  reading: { current: Reading }
  /** Records the hand landmarks for `seconds` and saves them on the PC, to tune the thresholds to a real hand. */
  record: (seconds: number) => Promise<number>
}
const Ctx = createContext<Holo>({
  on: false,
  starting: false,
  toggle: () => undefined,
  reading: { current: { ratio: null, pose: 'other', pinched: false, gesture: null } },
  record: async () => 0,
})
export const useHolo = () => useContext(Ctx)

/** Why the camera could not start, in words the student can act on. */
function why(err: unknown): string {
  const name = (err as { name?: string })?.name
  if (name === 'NotAllowedError') return 'O navegador não deixou usar a câmara. Autoriza-a no cadeado da barra de endereço e tenta outra vez.'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'Não encontrei nenhuma câmara ligada a este PC.'
  if (name === 'NotReadableError') return 'A câmara está a ser usada por outra aplicação.'
  return 'Não foi possível ligar o Holo Hands neste navegador.'
}

export function HoloHandsProvider({ children }: { children: ReactNode }) {
  const toast = useToast()
  const navigate = useNavigate()
  const [on, setOn] = useState(false)
  const [starting, setStarting] = useState(false)
  const cursor = useRef<HTMLDivElement>(null)
  const changedAt = useRef(0) // a poll that started before the student's own switch is stale
  const reading = useRef<Reading>({ ratio: null, pose: 'other', pinched: false, gesture: null })
  const recording = useRef<{ t: number; hands: { x: number; y: number; z?: number }[][] }[] | null>(null)

  // Follow the server's switch (the assistant may change it).
  useEffect(() => {
    let alive = true
    const poll = () => {
      const asked = Date.now()
      api.holo().then((h) => alive && asked > changedAt.current && setOn(h.on), () => undefined)
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  const toggle = useCallback(() => {
    const next = !on
    changedAt.current = Infinity // until the server has it, every poll is stale
    setOn(next)
    api.setHolo(next).then(
      () => (changedAt.current = Date.now()),
      () => {
        changedAt.current = Date.now()
        setOn(!next)
      },
    )
  }, [on])

  useEffect(() => {
    if (!on) return
    let stopped = false
    let stream: MediaStream | null = null
    let landmarker: { detectForVideo: (v: HTMLVideoElement, t: number) => { landmarks: { x: number; y: number }[][] }; close: () => void } | null = null
    let frame = 0
    let finish: (() => void) | null = null // ends a drag in progress and hides hover, when switched off
    setStarting(true)
    ;(async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, facingMode: 'user' }, audio: false })
        if (stopped) return stream.getTracks().forEach((track) => track.stop()) // switched off while the camera started
        const { FilesetResolver, HandLandmarker } = await import('@mediapipe/tasks-vision')
        const files = await FilesetResolver.forVisionTasks('/holo/wasm')
        const options = (delegate: 'GPU' | 'CPU') => ({ baseOptions: { modelAssetPath: '/holo/hand_landmarker.task', delegate }, runningMode: 'VIDEO' as const, numHands: 2 })
        landmarker = await HandLandmarker.createFromOptions(files, options('GPU')).catch(() => HandLandmarker.createFromOptions(files, options('CPU')))
        if (stopped) {
          landmarker.close()
          return stream.getTracks().forEach((track) => track.stop())
        }
        const video = document.createElement('video')
        video.srcObject = stream
        video.muted = true
        video.playsInline = true
        await video.play()
        const recognizer = new Recognizer()
        // Back only within PEACE: on its first page a swipe must not leave the app.
        const perform = performer(() => (window.history.state?.idx ?? 0) > 0 && navigate(-1))
        finish = () => recognizer.frame([], performance.now()).forEach(perform)
        let last = 0
        const loop = (t: number) => {
          if (stopped) return
          frame = requestAnimationFrame(loop)
          if (t - last < FRAME_MS || video.readyState < 2) return
          last = t
          const hands = landmarker!.detectForVideo(video, t).landmarks
          recording.current?.push({ t, hands })
          for (const g of recognizer.frame(hands, t)) {
            show(g)
            perform(g)
            if (g.type !== 'cursor') reading.current.gesture = g.type
          }
          Object.assign(reading.current, recognizer.last)
        }
        setStarting(false)
        frame = requestAnimationFrame(loop)
      } catch (err) {
        stream?.getTracks().forEach((track) => track.stop())
        if (stopped) return
        setStarting(false)
        toast(why(err), 'error')
        changedAt.current = Infinity
        setOn(false)
        api.setHolo(false).finally(() => (changedAt.current = Date.now()))
      }
    })()
    const show = (g: Gesture) => {
      const el = cursor.current
      if (!el) return
      if (g.type === 'cursor') {
        el.style.opacity = '1'
        el.style.transform = `translate(${g.x * innerWidth}px, ${g.y * innerHeight}px)`
      } else if (g.type === 'lost') el.style.opacity = '0'
      else if (g.type === 'dragStart') el.dataset.pressed = ''
      else if (g.type === 'dragEnd') delete el.dataset.pressed
    }
    return () => {
      stopped = true
      cancelAnimationFrame(frame)
      finish?.()
      stream?.getTracks().forEach((track) => track.stop()) // the camera light goes off
      landmarker?.close()
      setStarting(false)
      if (cursor.current) cursor.current.style.opacity = '0'
    }
  }, [on]) // eslint-disable-line react-hooks/exhaustive-deps

  // Test hook: browser tests drive gestures without a camera (localStorage "peace-holo-test" = "1").
  useEffect(() => {
    try {
      if (localStorage.getItem('peace-holo-test') !== '1') return
    } catch {
      return
    }
    const perform = performer(() => navigate(-1))
    ;(window as unknown as { peaceHolo?: (g: Gesture) => void }).peaceHolo = perform
  }, [navigate])

  const record = useCallback(async (seconds: number) => {
    recording.current = []
    await new Promise((resolve) => setTimeout(resolve, seconds * 1000))
    const frames = recording.current
    recording.current = null
    await api.saveHoloRecording(frames)
    return frames.length
  }, [])

  return (
    <Ctx.Provider value={{ on, starting, toggle, reading, record }}>
      {children}
      {/* The Hand cursor: a glow at the index fingertip. Never catches the pointer itself. */}
      <div
        ref={cursor}
        aria-hidden
        className="holo-cursor pointer-events-none fixed top-0 left-0 z-[200] -mt-3 -ml-3 size-6 rounded-full opacity-0 transition-opacity"
      />
    </Ctx.Provider>
  )
}

/** The sidebar switch (lit while on). Also the H key. */
export function HoloButton() {
  const { on, starting, toggle } = useHolo()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'h' || e.repeat || e.isComposing || e.metaKey || e.ctrlKey || e.altKey) return
      if ((e.target as HTMLElement).closest('input, textarea, select, [contenteditable], [role=textbox], dialog[open]')) return
      toggle()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle])
  return (
    <button
      type="button"
      title={on ? 'Desligar Holo Hands (h)' : 'Ligar Holo Hands (h)'}
      aria-label="Holo Hands"
      aria-pressed={on}
      onClick={toggle}
      className={
        on
          ? 'grid size-10 place-items-center rounded-lg bg-accent-soft text-accent ring-1 ring-accent/40'
          : 'grid size-10 place-items-center rounded-lg text-faint hover:bg-surface-2 hover:text-text'
      }
    >
      <Hand className={starting ? 'size-4 animate-pulse' : 'size-4'} />
    </button>
  )
}
