import { ArrowRight, CalendarCheck2, Check, Clock, GraduationCap, MapPin, Sparkles, SkipForward, Timer } from 'lucide-react'
import { lazy, Suspense, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { StudyMaterials } from '../components/materials'
import { SyncButton, SyncHealthList, SOURCE_NAME } from '../components/sync'
import { TaskDialog, TaskRow, URGENCY_TONE, useTaskActions } from '../components/tasks'
import { Badge, ChunkBoundary, Button, Card, CourseTag, Empty, ErrorNote, Progress, Segmented, Skeleton, courseColor, cx, useToast } from '../components/ui'
import { api, type CalEvent, type Deadline, type StudySession, type Task, type Today } from '../lib/api'
import { addDays, ago, countdown, dayKey, dayLabel, duration, greeting, longDay, plural, sameDay, shortDay, startOfDay, startOfWeek, time, urgency, weekday } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'

const Orb = lazy(() => import('../components/Orb'))

const list = (parts: string[]) => (parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} e ${parts.at(-1)}`)

function summary(t: Today, now: Date): string {
  const classes = t.events.filter((e) => e.kind === 'class').length
  const exams = t.events.filter((e) => e.kind === 'exam').length
  const sessions = t.sessions.filter((s) => s.status === 'planned' && new Date(s.ends_at) > now).length
  const parts = [
    classes && plural(classes, 'aula', 'aulas'),
    exams && plural(exams, 'avaliação', 'avaliações'),
    sessions && plural(sessions, 'sessão de estudo', 'sessões de estudo'),
  ].filter(Boolean) as string[]
  const first = parts.length ? `Hoje tens ${list(parts)}.` : 'Hoje não tens aulas nem avaliações.'
  const pending = [
    t.due_today.length && `${plural(t.due_today.length, 'entrega', 'entregas')} para hoje`,
    t.overdue.length && `${plural(t.overdue.length, 'tarefa', 'tarefas')} em atraso`,
  ].filter(Boolean) as string[]
  const exam = t.upcoming.find((d) => d.type === 'exam')
  const next = exam ? ` Próxima avaliação: ${exam.title}, ${dayLabel(new Date(exam.due_at), now).toLowerCase()} às ${time(exam.due_at)}.` : ''
  return `${first} ${pending.length ? `Tens ${list(pending)}.` : 'Nada em atraso.'}${next}`
}

function Stat({ label, value, tone, to }: { label: string; value: number; tone?: 'danger' | 'warn' | 'exam'; to: string }) {
  return (
    <Link to={to} className="animate-rise rounded-2xl border border-line bg-surface px-4 py-3.5 transition-colors hover:border-line-strong hover:bg-surface-2">
      <div className={cx('num text-2xl font-semibold tracking-tight', value > 0 && tone === 'danger' && 'text-danger', value > 0 && tone === 'warn' && 'text-warn', value > 0 && tone === 'exam' && 'text-exam')}>
        {value}
      </div>
      <div className="text-xs text-muted">{label}</div>
    </Link>
  )
}

function Recommendation({ t, now }: { t: Today; now: Date }) {
  const actions = useTaskActions()
  const rec = t.recommendation
  const next = [...t.events, ...t.sessions.filter((s) => s.status === 'planned')]
    .map((x) => ({ x, start: new Date(x.starts_at) }))
    .filter(({ start }) => start > now)
    .sort((a, b) => a.start.getTime() - b.start.getTime())[0]
  const task = rec && [...t.overdue, ...t.due_today, ...t.priorities.map((p) => p.task)].find((x) => x.id === rec.task_id)
  return (
    <section className="animate-rise relative overflow-hidden rounded-2xl border border-accent/25 bg-surface p-5 sm:p-6">
      <div aria-hidden className="pointer-events-none absolute -top-24 -right-16 size-64 rounded-full bg-accent/10 blur-3xl" />
      <div className="relative flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-5">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-accent uppercase">
            <Sparkles className="size-3.5" /> Recomendado agora
          </p>
          {rec ? (
            <>
              <h2 className="mt-2 text-xl font-semibold tracking-tight text-balance">
                {duration(rec.minutes)} em «{rec.title}»
              </h2>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {(rec.course || task) && <CourseTag course={rec.course ?? task!.course} />}
                {rec.reasons.map((r) => (
                  <Badge key={r} tone={r === 'em atraso' ? 'danger' : 'neutral'}>
                    {r}
                  </Badge>
                ))}
              </div>
              {rec.objective && <p className="mt-2 text-[13px] text-muted">{rec.objective}</p>}
              {rec.starts_at && <p className="mt-1 text-xs text-muted">Próximo bloco: {shortDay(rec.starts_at)} · {time(rec.starts_at)}</p>}
              {rec.warnings?.map((warning) => <p key={warning} className="mt-1 text-xs text-warn">{warning}</p>)}
              {!!rec.materials?.length && (
                <div className="mt-4">
                  <h3 className="mb-1.5 text-[11px] font-semibold tracking-wide text-faint uppercase">Para estudar</h3>
                  <StudyMaterials items={rec.materials} />
                </div>
              )}
            </>
          ) : (
            <>
              <h2 className="mt-2 text-xl font-semibold tracking-tight">Nada urgente por agora</h2>
              <p className="mt-1 text-[13px] text-muted">Bom momento para rever matéria ou descansar.</p>
            </>
          )}
        </div>
        {rec && task && !rec.scheduled_minutes && rec.starts_at && new Date(rec.starts_at).getTime() <= now.getTime() + 300_000 && (
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => actions.startSession(task, rec.minutes)}>
              <Timer className="size-4" /> Começar agora
            </Button>
          </div>
        )}
      </div>
      {next && (
        <div className="relative mt-5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-4 text-[13px]">
          <span className="text-faint">A seguir</span>
          <span className="font-medium">{'title' in next.x ? next.x.title : (next.x.task?.title ?? next.x.event?.title ?? 'Sessão de estudo')}</span>
          <span className="num text-muted">{time(next.start)}</span>
          {'location' in next.x && next.x.location && (
            <span className="inline-flex items-center gap-1 text-muted">
              <MapPin className="size-3.5" />
              {next.x.location}
            </span>
          )}
          <Badge tone="accent">{countdown(next.x.starts_at, now)}</Badge>
        </div>
      )}
    </section>
  )
}

type AgendaItem = { key: string; start: Date; end: Date; event?: CalEvent; session?: StudySession }

function Agenda({ t, now }: { t: Today; now: Date }) {
  const toast = useToast()
  const items: AgendaItem[] = [
    ...t.events.map((e) => ({ key: `e${e.id}`, start: new Date(e.starts_at), end: new Date(e.ends_at), event: e })),
    ...t.sessions.filter((s) => s.status !== 'skipped').map((s) => ({ key: `s${s.id}`, start: new Date(s.starts_at), end: new Date(s.ends_at), session: s })),
  ].sort((a, b) => a.start.getTime() - b.start.getTime())
  const nowIndex = items.findIndex((i) => i.end > now)

  async function mark(s: StudySession, status: 'done' | 'skipped') {
    try {
      await api.updateSession(s.id, { status })
      toast(status === 'done' ? 'Sessão concluída' : 'Sessão saltada', 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const nowLine = (
    <li key="now" aria-label={`Agora, ${time(now)}`} className="flex items-center gap-3 py-1">
      <span className="num w-[88px] text-right text-[11px] font-semibold text-accent">{time(now)}</span>
      <span className="h-px flex-1 bg-accent/50" />
    </li>
  )

  if (!items.length)
    return <Empty icon={<CalendarCheck2 className="size-5" />} title="Sem aulas nem sessões hoje">Usa «Planear o dia» para reservar tempo de estudo.</Empty>

  return (
    <ol className="grid">
      {items.map((item, i) => {
        const past = item.end <= now
        const e = item.event
        const s = item.session
        const color = e?.kind === 'exam' ? 'var(--exam)' : e ? (e.course ? courseColor(e.course.id) : 'var(--muted)') : 'var(--accent)'
        return [
          i === nowIndex && nowLine,
          <li key={item.key} className={cx('group flex items-stretch gap-3 rounded-xl py-2 transition-opacity', past && 'opacity-45')}>
            <div className="num w-[88px] shrink-0 pt-0.5 text-right text-[12px] leading-tight text-muted">
              <div className="font-medium text-text">{time(item.start)}</div>
              <div className="text-faint">{time(item.end)}</div>
            </div>
            <span aria-hidden className="w-[3px] shrink-0 rounded-full" style={{ background: color, opacity: s ? 0.6 : 1 }} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-[13.5px] font-medium">{e ? e.title : (s?.task?.title ?? s?.event?.title ?? 'Sessão de estudo')}</span>
                {e?.kind === 'exam' && <Badge tone="exam">avaliação</Badge>}
                {s && <Badge tone={s.status === 'done' ? 'ok' : 'accent'}>{s.status === 'done' ? 'feito' : 'estudo'}</Badge>}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                {e && <CourseTag course={e.course} />}
                {e?.location && (
                  <span className="inline-flex items-center gap-1">
                    <MapPin className="size-3" />
                    {e.location}
                  </span>
                )}
                {s?.reason && <span className="truncate">{s.reason}</span>}
              </div>
              {s && s.status === 'planned' && !past && <StudyMaterials items={s.materials ?? []} compact className="mt-1.5" />}
            </div>
            {s && s.status === 'planned' && (
              <div className="flex items-start gap-1 opacity-100 transition-opacity group-hover:opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100">
                <Button size="sm" variant="ghost" aria-label="Marcar sessão como feita" onClick={() => mark(s, 'done')}>
                  <Check className="size-3.5" /> Feito
                </Button>
                <Button size="sm" variant="ghost" aria-label="Saltar sessão" onClick={() => mark(s, 'skipped')}>
                  <SkipForward className="size-3.5" />
                </Button>
              </div>
            )}
          </li>,
        ]
      })}
      {nowIndex === -1 && nowLine}
    </ol>
  )
}

function Checklist({ t, now, onEdit }: { t: Today; now: Date; onEdit: (task: Task) => void }) {
  const groups = useMemo(() => {
    const seen = new Set<number>()
    const take = (tasks: Task[]) => tasks.filter((x) => !seen.has(x.id) && seen.add(x.id))
    return [
      { label: 'Em atraso', tasks: take(t.overdue) },
      { label: 'Para hoje', tasks: take(t.due_today) },
      { label: 'Prioridades', tasks: take(t.priorities.map((p) => p.task)) },
      { label: 'Concluídas hoje', tasks: take(t.completed_today) },
    ].filter((g) => g.tasks.length)
  }, [t])
  if (!groups.length) return <Empty icon={<Check className="size-5" />} title="Tudo em dia">Sem tarefas pendentes para hoje.</Empty>
  return (
    <div className="grid gap-3">
      {groups.map((g) => (
        <div key={g.label}>
          <h3 className={cx('mb-1 px-4 text-[11px] font-semibold tracking-wide uppercase', g.label === 'Em atraso' ? 'text-danger' : 'text-faint')}>{g.label}</h3>
          <ul className="grid">
            {g.tasks.map((task) => (
              <TaskRow key={task.id} task={task} now={now} onEdit={onEdit} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

type Range = 'today' | 'tomorrow' | '3' | '7' | '30'
const RANGES: { value: Range; label: string }[] = [
  { value: 'today', label: 'Hoje' },
  { value: 'tomorrow', label: 'Amanhã' },
  { value: '3', label: '3 dias' },
  { value: '7', label: '7 dias' },
  { value: '30', label: '30 dias' },
]

function inRange(d: Deadline, range: Range, now: Date): boolean {
  const due = new Date(d.due_at)
  const today = startOfDay(now)
  if (range === 'today') return due < addDays(today, 1)
  if (range === 'tomorrow') return due >= addDays(today, 1) && due < addDays(today, 2)
  return due < addDays(today, Number(range) + 1)
}

function Deadlines({ items, now }: { items: Deadline[]; now: Date }) {
  const [range, setRange] = useState<Range>('7')
  const shown = items.filter((d) => inRange(d, range, now))
  return (
    <div className="grid gap-3">
      <Segmented label="Intervalo de prazos" value={range} onChange={setRange} options={RANGES} />
      {shown.length ? (
        <ul className="grid gap-1">
          {shown.map((d) => {
            const u = urgency(d.due_at, now)
            return (
              <li key={`${d.type}${d.id}`} className="flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-surface-2">
                <div
                  className={cx(
                    'grid size-8 shrink-0 place-items-center rounded-lg',
                    d.type === 'exam' ? 'bg-exam-soft text-exam' : u === 'overdue' || u === 'critical' ? 'bg-danger-soft text-danger' : u === 'soon' ? 'bg-warn-soft text-warn' : 'bg-surface-3 text-muted',
                  )}
                >
                  {d.type === 'exam' ? <GraduationCap className="size-4" /> : <Clock className="size-4" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium">{d.title}</div>
                  <div className="flex items-center gap-2 text-xs text-muted">
                    <CourseTag course={d.course} compact />
                    <span className="num text-faint">
                      {shortDay(d.due_at)} · {time(d.due_at)}
                    </span>
                  </div>
                </div>
                <Badge tone={d.type === 'exam' ? 'exam' : URGENCY_TONE[u]} className="num">
                  {countdown(d.due_at, now)}
                </Badge>
              </li>
            )
          })}
        </ul>
      ) : (
        <Empty title="Sem prazos neste intervalo" />
      )}
      <Link to="/prazos" className="inline-flex items-center gap-1 justify-self-start text-xs font-medium text-accent hover:underline">
        Ver todos os prazos <ArrowRight className="size-3" />
      </Link>
    </div>
  )
}

function WeekStrip({ now, deadlines }: { now: Date; deadlines: Deadline[] }) {
  const start = startOfWeek(now)
  const key = dayKey(start)
  const week = useApi(() => api.events(start.toISOString(), addDays(start, 7).toISOString()), [key], 300_000)
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i))
  return (
    <div className="grid grid-cols-7 gap-1.5">
      {days.map((day) => {
        const events = (week.data ?? []).filter((e) => sameDay(new Date(e.starts_at), day))
        const classes = events.filter((e) => e.kind === 'class').length
        const exams = events.filter((e) => e.kind === 'exam').length
        const due = deadlines.filter((d) => d.type !== 'exam' && sameDay(new Date(d.due_at), day)).length
        const isToday = sameDay(day, now)
        return (
          <Link
            key={day.toISOString()}
            to={`/calendario?date=${dayKey(day)}`}
            aria-label={`${weekday(day)} ${day.getDate()}: ${plural(classes, 'aula', 'aulas')}, ${plural(exams, 'avaliação', 'avaliações')}, ${plural(due, 'prazo', 'prazos')}`}
            className={cx(
              'grid gap-1.5 rounded-xl border px-1 py-2 text-center transition-colors hover:bg-surface-2',
              isToday ? 'border-accent/50 bg-accent-soft' : 'border-transparent',
              day < startOfDay(now) && 'opacity-50',
            )}
          >
            <span className="text-[10px] font-medium tracking-wide text-faint uppercase">{weekday(day)}</span>
            <span className={cx('num text-[15px] font-semibold', isToday && 'text-accent')}>{day.getDate()}</span>
            <span className="flex h-4 flex-wrap items-center justify-center gap-0.5">
              {Array.from({ length: Math.min(classes, 4) }, (_, i) => (
                <span key={i} className="size-1 rounded-full bg-muted/70" />
              ))}
            </span>
            <span className="flex flex-col items-center gap-0.5">
              {exams > 0 && <span className="rounded bg-exam-soft px-1 text-[10px] font-semibold text-exam">{exams}A</span>}
              {due > 0 && <span className="rounded bg-warn-soft px-1 text-[10px] font-semibold text-warn">{due}P</span>}
            </span>
          </Link>
        )
      })}
      <p className="col-span-7 mt-1 flex justify-center gap-4 text-[11px] text-faint">
        <span>• aulas</span>
        <span className="text-exam">A avaliações</span>
        <span className="text-warn">P prazos</span>
      </p>
    </div>
  )
}

function Updates({ t, now }: { t: Today; now: Date }) {
  const toast = useToast()
  if (!t.changes.length) return <Empty title="Sem novidades">Nada mudou nos sites desde a última vez.</Empty>
  return (
    <div className="grid gap-3">
      <ul className="grid gap-2.5">
        {t.changes.map((c) => (
          <li key={c.id} className="flex items-start gap-3">
            <span className={cx('mt-1.5 size-1.5 shrink-0 rounded-full', c.kind === 'removed' ? 'bg-danger' : c.kind === 'created' ? 'bg-ok' : 'bg-warn')} />
            <div className="min-w-0">
              <p className="text-[13px] leading-snug">{c.summary}</p>
              <p className="text-[11px] text-faint">
                {SOURCE_NAME[c.source] ?? c.source} · {ago(c.created_at, now)}
              </p>
            </div>
          </li>
        ))}
      </ul>
      <Button
        size="sm"
        variant="ghost"
        className="justify-self-start"
        onClick={async () => {
          await api.markSeen(t.changes.map((c) => c.id))
          toast('Novidades marcadas como vistas', 'ok')
          refreshAll()
        }}
      >
        <Check className="size-3.5" /> Marcar como vistas
      </Button>
    </div>
  )
}

export default function Home() {
  const now = useNow()
  const toast = useToast()
  const today = useApi(() => api.today(), [])
  const month = useApi(() => api.deadlines(30), [], 120_000)
  const [editing, setEditing] = useState<Task | null>(null)
  const [planning, setPlanning] = useState(false)
  const t = today.data

  async function planDay() {
    setPlanning(true)
    try {
      const sessions = await api.planDay()
      const planned = sessions.filter((s) => s.status === 'planned' && new Date(s.ends_at) > new Date())
      toast(planned.length ? `${plural(planned.length, 'sessão planeada', 'sessões planeadas')} para hoje` : 'Sem tempo livre ou tarefas para planear hoje', 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setPlanning(false)
    }
  }

  const weekStart = startOfWeek(now)
  const examsThisWeek = (month.data ?? []).filter((d) => d.type === 'exam' && new Date(d.due_at) < addDays(weekStart, 7)).length
  const done = t ? t.completed_today.length : 0
  const total = t ? done + new Set([...t.overdue, ...t.due_today].map((x) => x.id)).size : 0

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex min-w-0 max-w-3xl items-center gap-4 sm:gap-6">
          <ChunkBoundary>
            <Suspense fallback={<div className="size-24 shrink-0 sm:size-36" />}>
              <Orb className="size-24 shrink-0 sm:size-36" />
            </Suspense>
          </ChunkBoundary>
          <div className="min-w-0">
            <p className="text-[13px] text-muted">{greeting(now)}</p>
            <h1 className="text-[28px] font-semibold tracking-tight sm:text-3xl">{longDay(now)}</h1>
            {t ? <p className="mt-2 text-[14px] leading-relaxed text-muted text-pretty">{summary(t, now)}</p> : <Skeleton className="mt-3 h-5 w-96 max-w-full" />}
          </div>
        </div>
        <div className="flex gap-2">
          <Button onClick={planDay} disabled={planning}>
            <CalendarCheck2 className="size-4" /> {planning ? 'A planear…' : 'Planear o dia'}
          </Button>
          <SyncButton />
        </div>
      </div>

      {today.error && <ErrorNote error={today.error} onRetry={today.reload} />}

      {!t ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <div className="grid gap-6">
            <Skeleton className="h-40" />
            <Skeleton className="h-72" />
          </div>
          <Skeleton className="h-96" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="aulas hoje" value={t.events.filter((e) => e.kind === 'class').length} to="/calendario" />
            <Stat label="entregas hoje" value={t.due_today.length} tone="warn" to="/prazos" />
            <Stat label="em atraso" value={t.overdue.length} tone="danger" to="/tarefas" />
            <Stat label="avaliações esta semana" value={examsThisWeek} tone="exam" to="/prazos" />
          </div>

          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
            <div className="grid gap-6">
              <Recommendation t={t} now={now} />
              <Card title="Agenda de hoje" action={<Link to="/calendario" className="text-xs font-medium text-accent hover:underline">Calendário</Link>}>
                <Agenda t={t} now={now} />
              </Card>
              <Card
                title="Checklist"
                action={
                  total > 0 && (
                    <div className="flex w-40 items-center gap-2">
                      <Progress value={(done / total) * 100} tone="ok" label="Tarefas de hoje concluídas" />
                      <span className="num text-[11px] text-muted">
                        {done}/{total}
                      </span>
                    </div>
                  )
                }
                bodyClassName="px-1 pb-3"
              >
                <Checklist t={t} now={now} onEdit={setEditing} />
              </Card>
            </div>

            <div className="grid gap-6">
              <Card title="Próximos prazos">
                <Deadlines items={month.data ?? t.upcoming} now={now} />
              </Card>
              <Card title="Esta semana">
                <WeekStrip now={now} deadlines={month.data ?? t.upcoming} />
              </Card>
              <Card title="Novidades" action={t.changes.length > 0 && <Badge tone="accent">{t.changes.length}</Badge>}>
                <Updates t={t} now={now} />
              </Card>
              <Card title="Integrações" action={<SyncButton size="sm" />}>
                <SyncHealthList health={t.sync} now={now} />
              </Card>
            </div>
          </div>
        </>
      )}
      <TaskDialog open={!!editing} task={editing} onClose={() => setEditing(null)} />
    </div>
  )
}
