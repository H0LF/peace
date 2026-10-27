import {
  ArrowRight,
  ArrowUp,
  AudioLines,
  Check,
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  FileText,
  GraduationCap,
  Hourglass,
  Moon,
  Sparkles,
  Sun,
  Target,
  TriangleAlert,
  X,
} from 'lucide-react'
import { lazy, Suspense, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import type { OrbState } from '../components/Orb'
import { obsidianHref, safeHttp } from '../components/materials'
import { ChunkBoundary, courseColor, cx, useToast } from '../components/ui'
import { api, type AssistantReply, type AssistantStatus, type CalEvent, type Deadline, type DesktopStepResult, type StudySession, type Today } from '../lib/api'
import { addDays, countdown, dayKey, greeting, longDay, shortDay, startOfDay, time, urgency } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'
import { useTheme } from '../lib/theme'

const Orb = lazy(() => import('../components/Orb'))

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
const CHIPS = ['Plano de hoje', 'O que tenho amanhã?', 'Prazos da semana', 'Planear a semana', 'Abre os materiais']
const STATES: { state: OrbState; label: string }[] = [
  { state: 'listening', label: 'A ouvir' },
  { state: 'thinking', label: 'A pensar' },
  { state: 'speaking', label: 'A responder' },
  { state: 'idle', label: 'Em espera' },
]

function readOpen(id: string): boolean {
  try {
    return localStorage.getItem(`peace-panel-${id}`) !== 'closed'
  } catch {
    return true
  }
}

/** A glass panel with an uppercase title; collapsible, the choice remembered per panel. */
function Panel({ id, icon, title, aside, children, className }: { id: string; icon: ReactNode; title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(() => readOpen(id))
  const toggle = () =>
    setOpen((o) => {
      try {
        localStorage.setItem(`peace-panel-${id}`, o ? 'closed' : 'open')
      } catch {
        /* remembered until reload only */
      }
      return !o
    })
  return (
    <section aria-label={title} className={cx('glass shrink-0 px-5 py-4', className)}>
      <header className="flex items-center gap-2.5">
        <h2 className="min-w-0 flex-1">
          <button type="button" onClick={toggle} aria-expanded={open} className="group flex w-full min-w-0 items-center gap-2.5 text-left">
            <span className="text-accent">{icon}</span>
            <span className="font-display truncate text-[13px] font-semibold tracking-[0.12em] uppercase">{title}</span>
            <ChevronDown className={cx('size-3.5 text-faint opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100', !open && '-rotate-90 opacity-100')} />
          </button>
        </h2>
        {aside}
      </header>
      {open && <div className="mt-3">{children}</div>}
    </section>
  )
}

function TodayPanel({ t, now }: { t: Today; now: Date }) {
  const items = [
    ...t.events.map((e) => ({ key: `e${e.id}`, start: e.starts_at, title: e.title, detail: [e.course?.name, e.location].filter(Boolean).join(' · '), color: e.kind === 'exam' ? 'var(--exam)' : e.course ? courseColor(e.course.id) : 'var(--muted)' })),
    ...t.sessions.filter((s) => s.status === 'planned').map((s) => ({ key: `s${s.id}`, start: s.starts_at, title: s.task?.title ?? s.event?.title ?? 'Sessão de estudo', detail: 'estudo', color: 'var(--accent)' })),
  ].sort((a, b) => a.start.localeCompare(b.start))
  const classes = t.events.filter((e) => e.kind === 'class').length
  return (
    <Panel id="hoje" icon={<CalendarDays className="size-4" />} title="Hoje" aside={<span className="truncate text-xs text-muted">{longDay(now)}</span>}>
      <div className="flex items-center justify-between border-b border-line/70 pb-2.5 text-[13px]">
        <span className="font-medium">{classes ? `${classes} ${classes === 1 ? 'aula' : 'aulas'}` : 'Sem aulas'}</span>
        <span className={cx('inline-flex items-center gap-1.5 text-xs', t.overdue.length ? 'text-danger' : 'text-muted')}>
          <span className={cx('size-1.5 rounded-full', t.overdue.length ? 'bg-danger' : 'border border-faint')} />
          {t.overdue.length ? `${t.overdue.length} em atraso` : 'Nada em atraso'}
        </span>
      </div>
      {items.length ? (
        <ol className="mt-2 grid">
          {items.slice(0, 5).map((i) => {
            const past = new Date(i.start) < now && !i.key.startsWith('s')
            return (
              <li key={i.key} className={cx('flex items-stretch gap-3 py-1.5', past && 'opacity-50')}>
                <span className="num w-11 shrink-0 pt-0.5 text-right text-[13px] text-muted">{time(i.start)}</span>
                <span aria-hidden className="relative w-2 shrink-0">
                  <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2" style={{ background: i.color, opacity: 0.6 }} />
                  <span className="absolute top-1.5 left-1/2 size-2 -translate-x-1/2 rounded-full" style={{ background: i.color, boxShadow: `0 0 8px ${i.color}` }} />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-[14px]">{i.title}</span>
                  {i.detail && <span className="block truncate text-xs text-muted">{i.detail}</span>}
                </span>
              </li>
            )
          })}
        </ol>
      ) : (
        <p className="mt-3 text-[13px] text-muted">Dia livre.</p>
      )}
    </Panel>
  )
}

function DeadlinesPanel({ items, now }: { items: Deadline[]; now: Date }) {
  return (
    <Panel
      id="prazos"
      icon={<Hourglass className="size-4" />}
      title="Próximos prazos"
      aside={
        <Link to="/prazos" className="inline-flex shrink-0 items-center gap-1 text-xs text-muted hover:text-accent">
          Ver todos <ArrowRight className="size-3" />
        </Link>
      }
    >
      {items.length ? (
        <ul className="grid gap-1">
          {items.slice(0, 3).map((d) => {
            const u = urgency(d.due_at, now)
            const Icon = d.type === 'exam' ? GraduationCap : d.type === 'assignment' ? FileText : Clock
            return (
              <li key={`${d.type}${d.id}`} className="flex items-center gap-3 py-1">
                <span className={cx('grid size-9 shrink-0 place-items-center rounded-xl', d.type === 'exam' ? 'bg-exam-soft text-exam' : 'bg-surface-3 text-muted')}>
                  <Icon className="size-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14px]">{d.title}</span>
                  <span className="num block truncate text-xs text-muted">
                    {shortDay(d.due_at)} · {time(d.due_at)}
                  </span>
                </span>
                <span
                  className={cx(
                    'num shrink-0 rounded-lg px-2 py-1 text-[11px] font-medium',
                    d.type === 'exam' ? 'bg-exam-soft text-exam' : u === 'overdue' || u === 'critical' ? 'bg-danger-soft text-danger' : 'bg-warn-soft text-warn',
                  )}
                >
                  {countdown(d.due_at, now)}
                </span>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="text-[13px] text-muted">Sem prazos nos próximos 7 dias.</p>
      )}
    </Panel>
  )
}

function RecommendationPanel({ t }: { t: Today }) {
  const rec = t.recommendation
  const course = rec?.course ?? (rec && [...t.overdue, ...t.due_today, ...t.priorities.map((p) => p.task)].find((x) => x.id === rec.task_id)?.course)
  return (
    <Panel
      id="recomendado"
      icon={<Sparkles className="size-4" />}
      title="Recomendado agora"
      aside={
        <Link to="/estudo" aria-label="Abrir o plano de estudo" className="shrink-0 text-muted hover:text-accent">
          <ArrowRight className="size-4" />
        </Link>
      }
    >
      {rec ? (
        <>
          <p className="text-[15px] leading-snug font-medium">{rec.title}</p>
          <p className="mt-0.5 text-xs text-muted">
            ~ {rec.minutes} min{course && ` · ${course.name}`}
          </p>
          <p className="mt-2 text-xs text-muted">{rec.reasons.join(' · ')}</p>
          {rec.objective && <p className="mt-1 text-xs text-muted">{rec.objective}</p>}
          {rec.starts_at && <p className="mt-1 text-xs text-muted">Próximo bloco: {shortDay(rec.starts_at)} · {time(rec.starts_at)}</p>}
          {rec.warnings?.map((warning) => <p key={warning} className="mt-1 text-xs text-warn">{warning}</p>)}
          {!!rec.materials?.length && (
            <ul aria-label="Material para estudar" className="mt-3 grid">
              {rec.materials.map((m) => (
                <li key={m.id} className="flex items-center gap-3 border-t border-line/60 py-2">
                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
                    <FileText className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px]">{m.title}</span>
                    <span className="block truncate text-[11px] text-muted">{m.why}</span>
                  </span>
                  {(m.vault_path || safeHttp(m.url)) && (
                    <a
                      href={m.vault_path ? obsidianHref(m.vault_path) : safeHttp(m.url)}
                      {...(!m.vault_path && { target: '_blank', rel: 'noreferrer noopener' })}
                      aria-label={`Abrir ${m.title}${m.vault_path ? ' no Obsidian' : ''}`}
                      className="grid size-7 shrink-0 place-items-center rounded-md text-muted hover:bg-surface-3 hover:text-accent"
                    >
                      <ExternalLink className="size-3.5" />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="text-[13px] text-muted">Nada urgente por agora.</p>
      )}
    </Panel>
  )
}

/** A day as a vertical timeline: classes, exams and study sessions as blocks, with the current time. */
function CalendarPanel({ now }: { now: Date }) {
  const [offset, setOffset] = useState(0)
  const day = addDays(startOfDay(now), offset)
  const end = addDays(day, 1)
  const events = useApi(() => api.events(day.toISOString(), end.toISOString()), [offset], 300_000)
  const sessions = useApi(() => api.sessions(day.toISOString(), end.toISOString()), [offset], 120_000)
  const ready = !events.loading && !sessions.loading // never show the previous day's blocks under the new label
  const blocks = [
    ...(ready ? (events.data ?? []) : []).map((e: CalEvent) => ({ key: `e${e.id}`, start: new Date(e.starts_at), end: new Date(e.ends_at), title: e.title, detail: e.location, color: e.kind === 'exam' ? 'var(--exam)' : e.course ? courseColor(e.course.id) : 'var(--muted)' })),
    ...(ready ? (sessions.data ?? []) : []).filter((s: StudySession) => s.status !== 'skipped').map((s) => ({ key: `s${s.id}`, start: new Date(s.starts_at), end: new Date(s.ends_at), title: s.task?.title ?? s.event?.title ?? 'Estudo', detail: 'estudo', color: 'var(--accent)' })),
  ].sort((a, b) => a.start.getTime() - b.start.getTime())
  const hours = (d: Date) => (d.getTime() - day.getTime()) / 3_600_000
  const from = Math.max(0, Math.min(8, ...blocks.map((b) => Math.floor(hours(b.start)))))
  const to = Math.min(24, Math.max(20, ...blocks.map((b) => Math.ceil(hours(b.end)))))
  const pct = (h: number) => `${((h - from) / (to - from)) * 100}%`
  // Overlapping blocks share the width side by side.
  const lanes: number[] = []
  const placed = blocks.map((b) => {
    let lane = lanes.findIndex((endsAt) => endsAt <= b.start.getTime())
    if (lane === -1) lane = lanes.push(0) - 1
    lanes[lane] = b.end.getTime()
    return { ...b, lane }
  })
  const width = 100 / Math.max(1, lanes.length)
  const isToday = offset === 0
  const label = isToday ? 'Hoje' : offset === 1 ? 'Amanhã' : offset === -1 ? 'Ontem' : shortDay(day)

  return (
    <Panel
      id="calendario"
      icon={<CalendarDays className="size-4" />}
      title="Calendário"
      aside={
        <div className="flex shrink-0 items-center gap-1">
          <button type="button" onClick={() => setOffset(0)} className={cx('px-2 text-xs', isToday ? 'text-accent underline decoration-accent/60 underline-offset-[6px]' : 'text-muted hover:text-text')}>
            {label}
          </button>
          <button type="button" aria-label="Dia anterior" onClick={() => setOffset((o) => o - 1)} className="grid size-7 place-items-center rounded-lg border border-line text-muted hover:text-text">
            <ChevronLeft className="size-3.5" />
          </button>
          <button type="button" aria-label="Dia seguinte" onClick={() => setOffset((o) => o + 1)} className="grid size-7 place-items-center rounded-lg border border-line text-muted hover:text-text">
            <ChevronRight className="size-3.5" />
          </button>
        </div>
      }
    >
      <div className="relative ml-12 h-52 border-l border-line/80">
        {Array.from({ length: Math.floor((to - from) / 2) + 1 }, (_, i) => from + i * 2).map((h) => (
          <div key={h} aria-hidden className="absolute right-0 -left-12 flex items-center" style={{ top: pct(h) }}>
            <span className="num w-10 -translate-y-1/2 text-right text-[11px] text-faint">{String(h).padStart(2, '0')}:00</span>
            <span className="ml-2 h-px flex-1 -translate-y-1/2 bg-line/40" />
          </div>
        ))}
        <ul aria-label={`Agenda de ${label.toLowerCase()}`}>
        {placed.map((b) => (
          <li key={b.key}>
          <Link
            to={`/calendario?date=${dayKey(day)}`}
            className="absolute overflow-hidden rounded-lg border-l-2 px-2 py-1 text-[11px] leading-tight hover:brightness-125"
            style={{
              top: pct(Math.max(from, hours(b.start))),
              height: `max(34px, calc(${pct(Math.min(to, hours(b.end)))} - ${pct(Math.max(from, hours(b.start)))}))`,
              left: `calc(${b.lane * width}% + 6px)`,
              width: `calc(${width}% - 10px)`,
              borderColor: b.color,
              background: `color-mix(in srgb, ${b.color} 16%, var(--surface))`,
            }}
          >
            <span className="block truncate font-medium">{b.title}</span>
            <span className="num block truncate text-muted">
              {time(b.start)}
              {b.detail ? ` · ${b.detail}` : ''}
            </span>
          </Link>
          </li>
        ))}
        </ul>
        {isToday && hours(now) >= from && hours(now) <= to && (
          <div role="img" aria-label={`Agora, ${time(now)}`} className="absolute right-0 -left-1 flex items-center" style={{ top: pct(hours(now)) }}>
            <span className="size-2 -translate-y-1/2 rounded-full bg-accent shadow-[0_0_8px_var(--glow)]" />
            <span className="h-px flex-1 -translate-y-1/2 bg-accent/60" />
          </div>
        )}
      </div>
      {ready && !blocks.length && <p className="mt-2 text-xs text-muted">Nada marcado.</p>}
    </Panel>
  )
}

function StatusPanel({ status, online, state }: { status?: AssistantStatus; online: boolean; state: OrbState }) {
  const tile = (label: string, value: string, ok: boolean) => (
    <div className="min-w-0 rounded-xl border border-line/70 bg-surface-2/50 px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div title={value} className={cx('truncate text-[13px] font-medium', !ok && 'text-faint')}>
        {value}
      </div>
    </div>
  )
  return (
    <Panel
      id="estado"
      icon={<Target className="size-4" />}
      title="Estado da PEACE"
      aside={
        <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted">
          <span className={cx('size-2 rounded-full', online ? 'bg-ok shadow-[0_0_6px_var(--ok)]' : 'bg-danger')} />
          {online ? 'Online' : 'Offline'}
        </span>
      }
    >
      <div className="flex items-center gap-4">
        <div className={cx('state-dial relative grid size-28 shrink-0 place-items-center rounded-full', state !== 'idle' && 'is-active')}>
          <svg viewBox="0 0 100 100" aria-hidden className="absolute inset-0">
            <circle cx="50" cy="50" r="44" fill="none" stroke="var(--line)" strokeWidth="2" />
            <circle cx="50" cy="50" r="44" fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="70 207" className="state-arc" />
          </svg>
          <AudioLines className="size-7 text-accent" />
        </div>
        <ul aria-label="Estado do assistente" className="grid flex-1 gap-0.5">
          {STATES.map((s) => (
            <li key={s.state} aria-current={s.state === state} className={cx('flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px]', s.state === state ? 'bg-surface-2 text-text' : 'text-faint')}>
              <span className={cx('size-2 rounded-full', s.state === state ? 'bg-accent shadow-[0_0_6px_var(--glow)]' : 'bg-line-strong')} />
              {s.label}
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {tile('Modelo', status ? (status.model.reachable ? status.model.name : 'offline') : '…', !!status?.model.reachable)}
        {tile('Agente do PC', status ? (status.desktop.connected ? 'ligado' : 'desligado') : '…', !!status?.desktop.connected)}
        {tile('Voz', status ? (status.voice.available ? (status.voice.name ?? 'disponível') : 'indisponível') : '…', !!status?.voice.available)}
      </div>
    </Panel>
  )
}

function FocusPanel({ status, now, onChange }: { status?: AssistantStatus; now: Date; onChange: () => void }) {
  const toast = useToast()
  const focus = status?.focus
  async function run(p: Promise<unknown>, message: string) {
    try {
      await p
      toast(message, 'ok')
      onChange()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  return (
    <Panel
      id="foco"
      icon={<Target className="size-4" />}
      title="Modo foco"
      aside={
        <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted">
          <span className={cx('size-2 rounded-full', focus?.active ? 'bg-accent shadow-[0_0_6px_var(--glow)]' : 'bg-line-strong')} />
          {focus?.active && focus.until ? `Ativo até ${time(focus.until)}` : 'Desativado'}
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        {focus?.active ? (
          <>
            <span className="num text-xs text-muted">Faltam {countdown(focus.until!, now).replace(/^em /, '')}; só passam avisos urgentes.</span>
            <button type="button" onClick={() => run(api.focusOff(), 'Modo foco desligado')} className="ml-auto rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent/50 hover:text-accent">
              Terminar
            </button>
          </>
        ) : (
          [25, 50, 90].map((m) => (
            <button key={m} type="button" onClick={() => run(api.focusOn(m), `Modo foco: ${m} min`)} className="rounded-lg border border-line px-3 py-1.5 text-xs hover:border-accent/50 hover:text-accent">
              {m} min
            </button>
          ))
        )}
      </div>
    </Panel>
  )
}

function TopBar({ status, now }: { status?: AssistantStatus; now: Date }) {
  const [theme, setTheme] = useTheme()
  return (
    <div className="flex items-center justify-end gap-2">
      {status && (
        <span
          title={status.model.reachable ? 'Modelo local ligado (gpt-oss no teu PC)' : 'Modelo offline: a PEACE usa os comandos internos'}
          className="inline-flex h-9 items-center gap-2 rounded-full border border-line bg-surface/60 px-3.5 text-xs"
        >
          <span className={cx('size-2 rounded-full', status.model.reachable ? 'bg-accent shadow-[0_0_6px_var(--glow)]' : 'bg-line-strong')} />
          {status.model.name}
          {!status.model.reachable && <span className="text-faint">offline</span>}
        </span>
      )}
      <button
        type="button"
        aria-label={theme === 'dark' ? 'Mudar para tema claro' : 'Mudar para tema escuro'}
        onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
        className="grid size-9 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-text"
      >
        {theme === 'dark' ? <Moon className="size-4" /> : <Sun className="size-4" />}
      </button>
      <span className="num w-12 text-right text-[15px] text-muted">{time(now)}</span>
    </div>
  )
}

/** Início: the assistant at the centre, the day around it. */
export default function Cockpit() {
  const now = useNow(15_000)
  const toast = useToast()
  const today = useApi(() => api.today(), [])
  const deadlines = useApi(() => api.deadlines(7), [], 120_000)
  const status = useApi(() => api.assistantStatus(), [], 2_000)
  const appearance = useApi(() => api.appearance(), [], 0)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [reply, setReply] = useState<AssistantReply & { question: string }>()
  const t = today.data
  const state: OrbState = busy ? 'thinking' : status.error ? 'idle' : (status.data?.state ?? 'idle')

  async function ask(question: string) {
    const q = question.trim()
    if (!q || busy) return
    setBusy(true)
    setText('')
    try {
      const r = await api.ask(q)
      setReply({ ...r, question: q })
      if (['plan_day', 'plan_week', 'skip_session', 'postpone_session', 'focus', 'focus_off'].includes(r.intent)) refreshAll()
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
    <div className="cockpit relative grid min-h-dvh gap-5 p-4 sm:p-6 xl:h-dvh xl:grid-cols-[360px_minmax(0,1fr)_380px] xl:grid-rows-[minmax(0,1fr)_auto] xl:overflow-hidden">
      {/* Centre: identity, orb, last reply */}
      <section aria-label="Assistente" className="relative flex min-h-[460px] flex-col items-center xl:col-start-2 xl:row-start-1 xl:min-h-0">
        <div className="z-10 pt-2 text-center xl:pt-8">
          <p className="font-display pl-[0.6em] text-3xl font-semibold tracking-[0.6em] text-accent sm:text-[34px]">PEACE</p>
          <p className="mt-1 text-lg text-text/90">
            {greeting(now)}
            {appearance.data?.name && `, ${appearance.data.name}`}
          </p>
        </div>
        <div className="relative w-full min-h-0 flex-1">
          <ChunkBoundary>
            <Suspense fallback={null}>
              <Orb hero state={state} className="size-full" />
            </Suspense>
          </ChunkBoundary>
        </div>
        <div aria-live="polite" className="z-10 w-full max-w-2xl">
          {reply && (
            <div className="glass animate-rise mx-auto max-h-48 overflow-y-auto px-5 py-3 text-center">
              <p className="text-xs text-faint">«{reply.question}»</p>
              <p className="mt-1 text-[15px] leading-relaxed">{reply.reply}</p>
              {!!reply.items.length && (
                <ul className="mt-2 grid gap-0.5 text-left text-[13px]">
                  {reply.items.slice(0, 6).map((i, n) => (
                    <li key={n} className="flex items-baseline gap-2">
                      <span className="size-1 shrink-0 translate-y-[-2px] rounded-full bg-accent" />
                      {i.vault_path || safeHttp(i.url) ? (
                        <a href={i.vault_path ? obsidianHref(i.vault_path) : safeHttp(i.url)} {...(!i.vault_path && { target: '_blank', rel: 'noreferrer noopener' })} className="truncate hover:text-accent">
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
      </section>

      {/* Conversation: below the orb, spanning centre and right like the reference */}
      <div className="flex justify-center xl:col-span-2 xl:col-start-2 xl:row-start-2">{conversation}</div>

      {/* Left: today, deadlines, recommendation */}
      <div className="grid content-start gap-4 xl:col-start-1 xl:row-span-2 xl:row-start-1 xl:min-h-0 xl:overflow-y-auto xl:pr-1">
        {t ? (
          <>
            <TodayPanel t={t} now={now} />
            <DeadlinesPanel items={deadlines.data ?? t.upcoming} now={now} />
            <RecommendationPanel t={t} />
          </>
        ) : (
          today.error ? <p className="glass px-5 py-4 text-[13px] text-danger">Não consegui carregar o dia: {today.error.message}</p> : <div className="glass h-64 animate-pulse" />
        )}
      </div>

      {/* Right: top bar, calendar, status, focus */}
      <div className="grid content-start gap-4 xl:col-start-3 xl:row-start-1 xl:min-h-0 xl:overflow-y-auto xl:pl-1">
        <TopBar status={status.data} now={now} />
        <CalendarPanel now={now} />
        <StatusPanel status={status.data} online={!status.error} state={state} />
        <FocusPanel status={status.data} now={now} onChange={status.reload} />
      </div>
    </div>
  )
}
