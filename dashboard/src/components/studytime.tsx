import { Pause, Play, Plus, Square, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, type StudyTimeReport, type TimerState } from '../lib/api'
import { dayMonth, duration, fromLocalInput, time } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'
import { Badge, Button, Card, Checkbox, Dialog, Empty, ErrorNote, Field, Input, Progress, Segmented, Select, Skeleton, useToast } from './ui'

const FOCUS_KEY = 'peace-timer-focus'
const focusWanted = () => {
  try {
    return localStorage.getItem(FOCUS_KEY) === '1'
  } catch {
    return false
  }
}
const rememberFocus = (on: boolean) => {
  try {
    localStorage.setItem(FOCUS_KEY, on ? '1' : '0')
  } catch {
    /* private window: just not remembered */
  }
}

function clock(seconds: number) {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

function elapsed(t: TimerState, now: Date) {
  const banked = t.banked_seconds ?? 0
  return t.state === 'running' && t.running_since ? banked + (now.getTime() - new Date(t.running_since).getTime()) / 1000 : banked
}

async function act(toast: ReturnType<typeof useToast>, run: () => Promise<TimerState>, message: (t: TimerState) => string) {
  try {
    const t = await run()
    toast(message(t), 'ok')
    refreshAll()
  } catch (e) {
    toast((e as Error).message, 'error')
  }
}

/** "Começar" on a planned session: starts the timer for it. */
export function StartButton({ sessionId }: { sessionId: number }) {
  const toast = useToast()
  return (
    <Button size="sm" variant="ghost" onClick={() => act(toast, () => api.startTimer({ session_id: sessionId, focus: focusWanted() }), () => 'Cronómetro a contar')}>
      <Play className="size-3.5" /> Começar
    </Button>
  )
}

function LogDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const tasks = useApi(() => api.tasks({ status: ['TODO', 'IN_PROGRESS', 'BLOCKED'] }), [], 0)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [taskId, setTaskId] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.addStudyLog({ starts_at: fromLocalInput(start)!, ends_at: fromLocalInput(end)!, ...(taskId ? { task_id: Number(taskId) } : {}) })
      toast('Tempo registado', 'ok')
      setStart('')
      setEnd('')
      refreshAll()
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Registar tempo de estudo"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="log-form">
            Registar
          </Button>
        </>
      }
    >
      <form id="log-form" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label="Comecei">
          <Input type="datetime-local" required value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Acabei">
          <Input type="datetime-local" required value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label="Tarefa" hint="Opcional" className="sm:col-span-2">
          <Select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
            <option value="">Sem tarefa</option>
            {tasks.data?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
                {t.course ? ` · ${t.course.name}` : ''}
              </option>
            ))}
          </Select>
        </Field>
      </form>
    </Dialog>
  )
}

/** The study timer: start (with an optional task and focus mode), pause, resume, stop; hand entries. */
export function TimerCard() {
  const toast = useToast()
  const now = useNow(1000)
  const timer = useApi(api.timer, [], 15_000)
  const tasks = useApi(() => api.tasks({ status: ['TODO', 'IN_PROGRESS', 'BLOCKED'] }), [], 0)
  const [taskId, setTaskId] = useState('')
  const [focus, setFocus] = useState(focusWanted)
  const [stoppedAt, setStoppedAt] = useState('')
  const [logging, setLogging] = useState(false)
  const t = timer.data
  const what = t?.task?.title ?? t?.course?.name ?? 'Estudo livre'

  function stopAtTime() {
    // "Parei às 16:40": that time on the day the stretch started, or the next day if it is earlier than the start.
    const since = new Date(t!.running_since!)
    const [h, m] = stoppedAt.split(':').map(Number)
    const at = new Date(since)
    at.setHours(h, m, 0, 0)
    if (at <= since) at.setDate(at.getDate() + 1)
    void act(toast, () => api.stopTimer({ ended_at: at.toISOString() }), (r) => `Registados ${duration(r.logged_minutes ?? 0)}`)
  }

  return (
    <Card className="mb-6" title="Cronómetro" action={<Button size="sm" variant="ghost" onClick={() => setLogging(true)}><Plus className="size-3.5" /> Registar à mão</Button>}>
      {timer.error && <ErrorNote error={timer.error} onRetry={timer.reload} />}
      {!t ? (
        <Skeleton className="h-16" />
      ) : t.state === 'idle' ? (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="O que vais estudar" className="min-w-56 flex-1">
            <Select value={taskId} onChange={(e) => setTaskId(e.target.value)}>
              <option value="">Estudo livre (sem tarefa)</option>
              {tasks.data?.map((task) => (
                <option key={task.id} value={task.id}>
                  {task.title}
                  {task.course ? ` · ${task.course.name}` : ''}
                </option>
              ))}
            </Select>
          </Field>
          <label className="flex h-9 items-center gap-2 text-[13px]">
            <Checkbox checked={focus} label="Modo foco enquanto estudo" onChange={(v) => (setFocus(v), rememberFocus(v))} />
            Modo foco enquanto estudo
          </label>
          <Button variant="primary" onClick={() => act(toast, () => api.startTimer({ ...(taskId ? { task_id: Number(taskId) } : {}), focus }), () => 'Cronómetro a contar')}>
            <Play className="size-4" /> Começar
          </Button>
        </div>
      ) : (
        <div className="grid gap-3">
          <div className="flex flex-wrap items-center gap-4">
            <div className="num text-4xl font-semibold tracking-tight" role="timer" aria-label="Tempo a contar">
              {clock(elapsed(t, now))}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13.5px] font-medium">{what}</p>
              <p className="text-xs text-muted">
                {t.state === 'paused' ? 'Em pausa' : 'A contar'}
                {t.course && t.task ? ` · ${t.course.name}` : ''}
                {t.focus ? ' · modo foco' : ''}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {t.state === 'running' ? (
                <Button onClick={() => act(toast, api.pauseTimer, () => 'Em pausa')}>
                  <Pause className="size-4" /> Pausar
                </Button>
              ) : (
                <Button onClick={() => act(toast, api.resumeTimer, () => 'A contar outra vez')}>
                  <Play className="size-4" /> Continuar
                </Button>
              )}
              <Button variant="primary" onClick={() => act(toast, () => api.stopTimer(), (r) => `Registados ${duration(r.logged_minutes ?? 0)}`)}>
                <Square className="size-4" /> Terminar
              </Button>
              {t.session_id && (
                <Button onClick={() => act(toast, () => api.stopTimer({ complete_session: true }), (r) => `Sessão feita · ${duration(r.logged_minutes ?? 0)}`)}>
                  Terminar e marcar feita
                </Button>
              )}
            </div>
          </div>
          {t.forgotten && (
            <div className="flex flex-wrap items-end gap-3 rounded-xl border border-warn/40 bg-warn-soft p-3">
              <p className="min-w-56 flex-1 text-[13px] text-warn">Está a contar há mais de 3 horas. Se te esqueceste dele, diz a que horas paraste.</p>
              <Field label="Parei às">
                <Input type="time" value={stoppedAt} onChange={(e) => setStoppedAt(e.target.value)} />
              </Field>
              <Button disabled={!stoppedAt} onClick={stopAtTime}>
                Terminar a essa hora
              </Button>
            </div>
          )}
        </div>
      )}
      <LogDialog open={logging} onClose={() => setLogging(false)} />
    </Card>
  )
}

const PERIODS = [
  { value: 'day', label: 'Dia' },
  { value: 'week', label: 'Semana' },
  { value: 'month', label: 'Mês' },
] as const

/** Planned vs actual per course, the entries behind it and the effort calibration suggestions. */
export function StudyTimeCard() {
  const toast = useToast()
  const [period, setPeriod] = useState<StudyTimeReport['period']>('week')
  const report = useApi(() => api.studyTime(period), [period], 60_000)
  const r = report.data
  async function calibrate(courseId: number, factor: number | null) {
    try {
      await api.setCalibration(courseId, factor)
      toast(factor ? 'O plano passa a contar com este ritmo' : 'O plano volta às estimativas', 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  async function remove(id: number) {
    try {
      await api.deleteStudyLog(id)
      toast('Registo apagado', 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  return (
    <Card className="mb-6" title="Tempo de estudo" action={<Segmented label="Período" value={period} onChange={setPeriod} options={[...PERIODS]} />}>
      {report.error && <ErrorNote error={report.error} onRetry={report.reload} />}
      {!r ? (
        <Skeleton className="h-32" />
      ) : (
        <div className="grid gap-5">
          <p className="text-[13px]">
            <span className="num text-2xl font-semibold tracking-tight">{duration(r.actual_minutes)}</span>
            <span className="text-muted"> estudados de {duration(r.planned_minutes)} planeados</span>
          </p>
          {r.courses.length ? (
            <ul className="grid gap-3">
              {r.courses.map((c) => (
                <li key={c.course_id ?? 'none'} className="grid gap-1">
                  <div className="flex justify-between gap-3 text-[13px]">
                    <span className="truncate">{c.name}</span>
                    <span className="num shrink-0 text-muted">
                      {duration(c.actual_minutes)} / {duration(c.planned_minutes)}
                    </span>
                  </div>
                  <Progress value={c.planned_minutes ? Math.min(100, (c.actual_minutes / c.planned_minutes) * 100) : c.actual_minutes ? 100 : 0} tone="ok" label={`${c.name}: tempo estudado face ao planeado`} />
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="Sem estudo registado neste período">Usa o cronómetro ou regista o tempo à mão.</Empty>
          )}
          {r.calibration.map((c) => (
            <div key={c.course_id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line p-3 text-[13px]">
              <p className="min-w-56 flex-1">
                Em <strong>{c.name}</strong> costumas demorar {String(c.factor).replace('.', ',')}× o estimado ({c.tasks} trabalhos acabados).
                {c.current ? ` O plano já conta com ${String(c.current).replace('.', ',')}×.` : ''}
              </p>
              {c.current !== c.factor && (
                <Button size="sm" variant="primary" onClick={() => calibrate(c.course_id, c.factor)}>
                  Usar no plano
                </Button>
              )}
              {c.current && (
                <Button size="sm" variant="ghost" onClick={() => calibrate(c.course_id, null)}>
                  Deixar de usar
                </Button>
              )}
            </div>
          ))}
          {r.logs.length > 0 && (
            <details>
              <summary className="cursor-pointer text-xs text-muted">Registos ({r.logs.length})</summary>
              <ul className="mt-2 grid divide-y divide-line">
                {r.logs.map((l) => (
                  <li key={l.id} className="flex items-center gap-3 py-2 text-[13px]">
                    <span className="num w-36 shrink-0 text-xs text-muted">
                      {dayMonth(l.starts_at)} · {time(l.starts_at)}–{l.ends_at ? time(l.ends_at) : 'agora'}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{l.task?.title ?? l.course?.name ?? 'Estudo livre'}</span>
                    {l.origin === 'manual' && <Badge>à mão</Badge>}
                    <span className="num text-xs text-muted">{duration(l.minutes)}</span>
                    {l.ends_at ? (
                      <Button size="icon" variant="ghost" aria-label="Apagar registo" onClick={() => remove(l.id)}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    ) : (
                      <span className="size-8" />
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Card>
  )
}
