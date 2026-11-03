import { ModelSelector } from '../components/ModelSelector'
import { useFeature } from '../lib/ui'
import {
  ArrowRight,
  ArrowUp,
  Check,
  ListChecks,
  Mic,
  Moon,
  Sparkles,
  Square,
  Sun,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import type { OrbState } from '../components/Orb'
import { obsidianHref, onThisPc, safeHttp } from '../components/materials'
import { Deck } from '../components/deck'
import { DeliveriesPanel, ExamsPanel } from '../components/workload'
import { Dialog, cx, useToast } from '../components/ui'
import { api, type AssistantReply, type AssistantStatus, type DesktopStepResult } from '../lib/api'
import { greeting, longDay, time } from '../lib/format'
import { micAvailable, startRecording, toWav } from '../lib/mic'
import { refreshAll, useApi, useNow } from '../lib/hooks'
import { useTheme } from '../lib/theme'

const JOB_LIVE = ['confirm', 'pending', 'running']
const JOB_STATE: Record<string, string> = {
  confirm: 'À espera da tua confirmação: responde «sim».',
  pending: 'À espera do agente do PC…',
  running: 'A executar no PC…',
}

function StepMark({ r }: { r: DesktopStepResult }) {
  if (!r.ok) return <X aria-label="Falhou" className="size-3.5 shrink-0 text-danger" />
  if (!r.verified) return <TriangleAlert aria-label="Feito, sem confirmação" className="size-3.5 shrink-0 text-warn" />
  return <Check aria-label="Feito e confirmado" className="size-3.5 shrink-0 text-accent" />
}

/** A desktop action's progress: each step as the agent reported it, never as planned. */
function DesktopJobView({ id }: { id: string }) {
  const toast = useToast()
  const [polling, setPolling] = useState(true)
  const job = useApi(() => api.desktopJob(id), [id], polling ? 1_000 : 0)
  const j = job.data
  const live = !j || JOB_LIVE.includes(j.state)
  if (polling && !live) setPolling(false) // stop asking once the agent has reported the end
  if (job.error && !j) return null
  async function cancel() {
    try {
      job.setData(await api.cancelDesktopJob(id))
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  return (
    <div className="mt-2 rounded-lg border border-line bg-surface/40 px-3 py-2 text-left text-[13px]">
      <p className="text-xs text-muted">{j && !live ? j.summary : (j && JOB_STATE[j.state]) ?? 'A ligar ao agente do PC…'}</p>
      {!!j?.results.length && (
        <ul className="mt-1 grid gap-0.5">
          {j.results.map((r, n) => (
            <li key={n} className="flex items-baseline gap-2">
              <StepMark r={r} />
              <span>{r.message}</span>
            </li>
          ))}
        </ul>
      )}
      {live && (
        <button type="button" onClick={() => void cancel()} className="mt-1 text-xs text-muted underline hover:text-text">
          Cancelar
        </button>
      )}
    </div>
  )
}
const CHIPS = ['O que tenho amanhã?', 'Prazos da semana', 'Planear a semana']

function TopBar({ status, now, onFocusOff, onAll, name }: { status?: AssistantStatus; now: Date; onFocusOff: () => void; onAll: () => void; name?: string }) {
  const [theme, setTheme] = useTheme()
  const toast = useToast()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="mr-auto text-[13px] text-muted">
        <span className="font-display font-semibold tracking-[0.3em] text-accent">PEACE</span>
        <span className="ml-3">{greeting(now)}{name && `, ${name}`}</span>
      </p>
      <button type="button" onClick={onAll} className="inline-flex h-9 items-center gap-2 rounded-full border border-line bg-surface/60 px-3.5 text-xs hover:border-accent/50">
        <ListChecks aria-hidden className="size-3.5 text-accent" /> Tudo por data
      </button>
      {status?.focus.active && status.focus.until && (
        <button
          type="button"
          title="Terminar o modo foco"
          onClick={() => api.focusOff().then(onFocusOff, (e: Error) => toast(e.message, 'error'))}
          className="num inline-flex h-9 items-center gap-1.5 rounded-full border border-accent/50 px-3.5 text-xs text-accent hover:bg-accent-soft"
        >
          Foco até {time(status.focus.until)} <X aria-hidden className="size-3" />
        </button>
      )}
      <ModelSelector paused={status?.model.paused} />
      <button
        type="button"
        aria-label={theme === 'dark' ? 'Mudar para tema claro' : 'Mudar para tema escuro'}
        onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        className="grid size-9 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-text"
      >
        {theme === 'dark' ? <Moon className="size-4" /> : <Sun className="size-4" />}
      </button>
      <span className="num text-right text-2xl font-semibold">{time(now)}</span>
      <span className="hidden text-xs text-muted sm:inline">{longDay(now)}</span>
    </div>
  )
}

/** Início: the assistant at the centre, the day around it. */
export default function Cockpit() {
  const now = useNow(15_000)
  const toast = useToast()
  const mic = useFeature('mic')
  const navigate = useNavigate()
  const today = useApi(() => api.today(), [])
  const work = useApi(() => api.workload(), [], 120_000)
  const waiting = useApi(() => api.proposals(), [], 120_000)
  const status = useApi(() => api.assistantStatus(), [], 2_000)
  const appearance = useApi(() => api.appearance(), [], 0)
  const [text, setText] = useState('')
  const [all, setAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [reply, setReply] = useState<AssistantReply & { question: string }>()
  const [listening, setListening] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const stopRecording = useRef<(() => Promise<Blob>) | null>(null)
  const limit = useRef(0)
  const t = today.data
  const state: OrbState = listening ? 'listening' : speaking ? 'speaking' : busy ? 'thinking' : status.error ? 'idle' : (status.data?.state ?? 'idle')

  async function speak(reply: string) {
    try {
      const audio = await api.say(reply)
      if (!audio) return // spoken replies are off in Settings
      const url = URL.createObjectURL(audio)
      const player = new Audio(url)
      const done = () => {
        URL.revokeObjectURL(url)
        setSpeaking(false)
      }
      player.onended = done
      player.onerror = done
      setSpeaking(true)
      await player.play()
    } catch (e) {
      setSpeaking(false)
      toast((e as Error).message, 'error') // the answer is on screen anyway
    }
  }

  async function ask(question: string) {
    const q = question.trim()
    if (q && !busy) await send(q, 'text')
  }

  async function send(q: string, source: 'text' | 'voice') {
    setBusy(true)
    setText('')
    try {
      const r = await api.ask(q, source)
      setReply({ ...r, question: q })
      if (['plan_day', 'plan_week', 'skip_session', 'postpone_session', 'focus', 'focus_off', 'model', 'study_plan'].includes(r.intent)) refreshAll()
      if (r.intent === 'open_document' && r.to) navigate(r.to)
      if (source === 'voice') void speak(r.reply)
    } catch (e) {
      setText(q) // keep the question so it can be sent again
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    void ask(text)
  }

  async function toggleMic() {
    if (stopRecording.current) {
      const stop = stopRecording.current
      stopRecording.current = null
      window.clearTimeout(limit.current)
      setListening(false)
      setBusy(true)
      try {
        const heard = (await api.hear(await toWav(await stop()))).text
        if (heard) return await send(heard, 'voice')
        toast('Não ouvi nada. Tenta outra vez.', 'error')
      } catch (e) {
        toast((e as Error).message, 'error')
      }
      setBusy(false)
      return
    }
    try {
      stopRecording.current = await startRecording()
      setListening(true)
      limit.current = window.setTimeout(() => void toggleMic(), 30_000)
    } catch {
      toast('Não consegui usar o microfone. Dá autorização ao browser e tenta outra vez.', 'error')
    }
  }

  const conversation = (
    <div className="grid w-full max-w-3xl gap-3">
      <form onSubmit={submit} className="glass flex items-center gap-3 rounded-full py-2 pr-2 pl-5">
        <Sparkles aria-hidden className="size-4 shrink-0 text-accent" />
        <input
          aria-label="Pergunta à PEACE"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Pergunta-me alguma coisa…"
          maxLength={500}
          className="min-w-0 flex-1 bg-transparent py-2 text-[15px] placeholder:text-faint focus:outline-none"
        />
        {mic && <button
          type="button"
          aria-label={listening ? 'Parar e enviar' : 'Falar'}
          aria-pressed={listening}
          title={micAvailable() ? (listening ? 'Parar e enviar' : 'Falar') : 'O microfone só funciona neste PC ou num endereço https'}
          disabled={!micAvailable() || (busy && !listening)}
          onClick={() => void toggleMic()}
          className={cx('grid size-10 shrink-0 place-items-center rounded-full border transition disabled:opacity-40', listening ? 'border-accent bg-accent-soft text-accent' : 'border-line bg-surface-2 text-muted hover:text-accent')}
        >
          {listening ? <Square className="size-4" /> : <Mic className="size-4" />}
        </button>}
        <button type="submit" aria-label="Enviar" disabled={!text.trim() || busy} className="grid size-10 shrink-0 place-items-center rounded-full border border-line bg-surface-2 text-muted transition hover:text-accent disabled:opacity-40">
          <ArrowUp className="size-4" />
        </button>
      </form>
      <div className="flex flex-wrap justify-center gap-2">
        {CHIPS.map((c) => (
          <button key={c} type="button" disabled={busy} onClick={() => void ask(c)} className="rounded-full border border-line bg-surface/50 px-3.5 py-1.5 text-xs text-muted transition hover:border-accent/50 hover:text-text disabled:opacity-50">
            {c}
          </button>
        ))}
      </div>
    </div>
  )

  return (
    <div className="cockpit relative isolate flex min-h-dvh flex-col gap-3 overflow-x-clip p-4 sm:px-6 sm:py-4 lg:h-dvh lg:overflow-clip lg:pb-24">
      <TopBar status={status.data} now={now} onFocusOff={status.reload} onAll={() => setAll(true)} name={appearance.data?.name} />
      {today.error && <p className="glass px-5 py-3 text-[13px] text-danger">Não consegui carregar o dia: {today.error.message}</p>}
      {work.error && <p className="glass px-5 py-3 text-[13px] text-danger">Não consegui carregar as entregas: {work.error.message}</p>}
      {!!waiting.data?.length && (
        <Link to="/rever" className="glass relative z-20 flex items-center justify-center gap-1 px-5 py-2 text-[13px] text-accent hover:underline">
          A PEACE precisa de uma decisão tua <ArrowRight className="size-3" />
        </Link>
      )}
      <Deck t={t} work={work.data} now={now} state={state} />
      <div className="relative z-20 flex flex-col items-center gap-2">
        <div aria-live="polite" className="w-full max-w-2xl">
          {reply && (
            <div className="glass animate-rise mx-auto max-h-48 overflow-y-auto px-5 py-3 text-center">
              <p className="text-xs text-faint">«{reply.question}»</p>
              <p className="mt-1 text-[15px] leading-relaxed">{reply.reply}</p>
              {!!reply.items.length && (
                <ul className="mt-2 grid gap-0.5 text-left text-[13px]">
                  {reply.items.slice(0, 6).map((i, n) => (
                    <li key={n} className="flex items-baseline gap-2">
                      <span className="size-1 shrink-0 translate-y-[-2px] rounded-full bg-accent" />
                      {(onThisPc && i.vault_path) || safeHttp(i.url) ? (
                        <a href={onThisPc && i.vault_path ? obsidianHref(i.vault_path) : safeHttp(i.url)} {...(!(onThisPc && i.vault_path) && { target: '_blank', rel: 'noreferrer noopener' })} className="truncate hover:text-accent">
                          {i.title}
                        </a>
                      ) : (
                        <span className="truncate">{i.title}</span>
                      )}
                      {i.detail && <span className="truncate text-xs text-muted">{i.detail}</span>}
                    </li>
                  ))}
                </ul>
              )}
              {reply.job && <DesktopJobView id={reply.job.id} />}
              {reply.to && (
                <Link to={reply.to} className="mt-2 inline-flex items-center gap-1 text-xs text-accent hover:underline">
                  Ver mais <ArrowRight className="size-3" />
                </Link>
              )}
            </div>
          )}
        </div>
        {conversation}
      </div>
      <Dialog open={all} onClose={() => setAll(false)} title="Tudo por data">
        {work.data && (
          <div className="grid gap-3">
            <DeliveriesPanel data={work.data} now={now} />
            <ExamsPanel data={work.data} now={now} />
          </div>
        )}
      </Dialog>
    </div>
  )
}
