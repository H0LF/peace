import { ChevronLeft, ChevronRight, ExternalLink, MapPin, Plus } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { Badge, Button, CourseTag, Dialog, ErrorNote, Field, Input, PageHeader, Segmented, Select, courseColor, cx, useToast } from '../components/ui'
import { api, type CalEvent, type Course, type StudySession, type Task } from '../lib/api'
import { addDays, dayKey, dayMonth, fromLocalInput, longDay, monthTitle, sameDay, startOfDay, startOfWeek, time, toLocalInput, weekday } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'

type View = 'day' | 'week' | 'month'
type Kind = 'class' | 'exam' | 'appointment' | 'session' | 'deadline'
const FIRST_HOUR = 8
const LAST_HOUR = 23
const HOUR_PX = 52

type Item = {
  id: string
  kind: Kind
  title: string
  start: Date
  end: Date
  color: string
  course: Course | { id: number; name: string; code: string | null } | null
  location?: string | null
  url?: string | null
  note?: string
  source?: string
}

const KIND_LABEL: Record<Kind, string> = { class: 'Aulas', exam: 'Avaliações', appointment: 'Compromissos', session: 'Estudo', deadline: 'Prazos' }
const KIND_ONE: Record<Kind, string> = { class: 'Aula', exam: 'Avaliação', appointment: 'Compromisso', session: 'Sessão de estudo', deadline: 'Prazo' }

function toItems(events: CalEvent[], sessions: StudySession[], tasks: Task[]): Item[] {
  return [
    ...events.map((e) => ({
      id: `e${e.id}`,
      kind: e.kind,
      title: e.title,
      start: new Date(e.starts_at),
      end: new Date(e.ends_at),
      color: e.kind === 'exam' ? 'var(--exam)' : e.kind === 'appointment' ? 'var(--muted)' : e.course ? courseColor(e.course.id) : 'var(--muted)',
      course: e.course,
      location: e.location,
      url: e.url,
      source: e.source,
    })),
    ...sessions
      .filter((s) => s.status !== 'skipped')
      .map((s) => ({
        id: `s${s.id}`,
        kind: 'session' as const,
        title: s.task?.title ?? s.event?.title ?? 'Sessão de estudo',
        start: new Date(s.starts_at),
        end: new Date(s.ends_at),
        color: 'var(--accent)',
        course: null,
        note: [s.status === 'done' ? 'Concluída' : 'Planeada', s.reason].filter(Boolean).join(' · '),
      })),
    ...tasks
      .filter((t) => t.due_at)
      .map((t) => ({
        id: `t${t.id}`,
        kind: 'deadline' as const,
        title: t.title,
        start: new Date(t.due_at!),
        end: new Date(t.due_at!),
        color: 'var(--warn)',
        course: t.course,
        url: t.url,
        source: t.source,
      })),
  ]
}

/** Side-by-side lanes for overlapping items of one day. */
function lanes(items: Item[]) {
  const sorted = [...items].sort((a, b) => a.start.getTime() - b.start.getTime() || b.end.getTime() - a.end.getTime())
  const out: (Item & { lane: number; lanes: number })[] = []
  let cluster: (Item & { lane: number })[] = []
  let clusterEnd = 0
  let ends: number[] = []
  const flush = () => {
    const count = Math.max(...cluster.map((c) => c.lane)) + 1
    cluster.forEach((c) => out.push({ ...c, lanes: count }))
    cluster = []
    ends = []
    clusterEnd = 0
  }
  for (const it of sorted) {
    if (cluster.length && it.start.getTime() >= clusterEnd) flush()
    let lane = ends.findIndex((e) => e <= it.start.getTime())
    if (lane === -1) {
      lane = ends.length
      ends.push(0)
    }
    ends[lane] = it.end.getTime()
    cluster.push({ ...it, lane })
    clusterEnd = Math.max(clusterEnd, it.end.getTime())
  }
  if (cluster.length) flush()
  return out
}

function Block({ item, onOpen }: { item: Item & { lane: number; lanes: number }; onOpen: (i: Item) => void }) {
  const startH = item.start.getHours() + item.start.getMinutes() / 60
  const endH = Math.min(LAST_HOUR + 1, item.end.getHours() + item.end.getMinutes() / 60 || 24)
  const top = (Math.max(startH, FIRST_HOUR) - FIRST_HOUR) * HOUR_PX
  const height = Math.max(22, (endH - Math.max(startH, FIRST_HOUR)) * HOUR_PX - 3)
  const width = 100 / item.lanes
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      title={`${item.title} · ${time(item.start)}–${time(item.end)}${item.location ? ` · ${item.location}` : ''}`}
      className="absolute overflow-hidden rounded-lg border-l-[3px] px-2 py-1 text-left text-[11.5px] leading-tight transition-[filter] hover:brightness-125"
      style={{
        top,
        height,
        left: `calc(${item.lane * width}% + 2px)`,
        width: `calc(${width}% - 4px)`,
        borderColor: item.color,
        background: `color-mix(in oklab, ${item.color} ${item.kind === 'session' ? 10 : 16}%, var(--surface))`,
      }}
    >
      <div className="truncate font-semibold text-text">{item.title}</div>
      {height > 34 && (
        <div className="num truncate text-muted">
          {time(item.start)}–{time(item.end)}
          {item.location ? ` · ${item.location}` : ''}
        </div>
      )}
    </button>
  )
}

function TimeGrid({ days, items, now, onOpen }: { days: Date[]; items: Item[]; now: Date; onOpen: (i: Item) => void }) {
  const hours = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i)
  const cols = `56px repeat(${days.length}, minmax(0, 1fr))`
  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-surface">
      <div className="grid border-b border-line" style={{ gridTemplateColumns: cols }}>
        <div />
        {days.map((d) => {
          const deadlines = items.filter((i) => i.kind === 'deadline' && sameDay(i.start, d))
          return (
            <div key={d.toISOString()} className="min-w-0 border-l border-line px-2 py-2">
              <div className={cx('flex items-baseline gap-1.5', sameDay(d, now) ? 'text-accent' : 'text-muted')}>
                <span className="text-[11px] font-medium tracking-wide uppercase">{weekday(d)}</span>
                <span className="num text-lg font-semibold">{d.getDate()}</span>
              </div>
              <div className="mt-1 grid gap-1">
                {deadlines.map((dl) => (
                  <button
                    key={dl.id}
                    type="button"
                    onClick={() => onOpen(dl)}
                    className="truncate rounded-md bg-warn-soft px-1.5 py-0.5 text-left text-[11px] font-medium text-warn hover:brightness-125"
                  >
                    {time(dl.start)} · {dl.title}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <div className="max-h-[calc(100dvh-260px)] min-h-[420px] overflow-y-auto pt-2">
        <div className="relative grid" style={{ gridTemplateColumns: cols }}>
          <div>
            {hours.map((h) => (
              <div key={h} className="num relative text-right text-[10.5px] text-faint" style={{ height: HOUR_PX }}>
                <span className="absolute -top-2 right-2">{String(h).padStart(2, '0')}:00</span>
              </div>
            ))}
          </div>
          {days.map((d) => {
            const isToday = sameDay(d, now)
            const nowTop = (now.getHours() + now.getMinutes() / 60 - FIRST_HOUR) * HOUR_PX
            return (
              <div key={d.toISOString()} className={cx('relative border-l border-line', isToday && 'bg-accent/[0.03]')}>
                {hours.map((h) => (
                  <div key={h} className="border-t border-line/60" style={{ height: HOUR_PX }} />
                ))}
                {lanes(items.filter((i) => i.kind !== 'deadline' && sameDay(i.start, d))).map((it) => (
                  <Block key={it.id} item={it} onOpen={onOpen} />
                ))}
                {isToday && nowTop > 0 && nowTop < hours.length * HOUR_PX && (
                  <div aria-hidden className="pointer-events-none absolute inset-x-0 z-10 flex items-center" style={{ top: nowTop }}>
                    <span className="-ml-1 size-2 rounded-full bg-accent" />
                    <span className="h-px flex-1 bg-accent" />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function MonthGrid({ anchor, items, now, onPick }: { anchor: Date; items: Item[]; now: Date; onPick: (d: Date) => void }) {
  const start = startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1))
  const days = Array.from({ length: 42 }, (_, i) => addDays(start, i))
  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-surface">
      <div className="grid grid-cols-7 border-b border-line">
        {days.slice(0, 7).map((d) => (
          <div key={d.toISOString()} className="px-3 py-2 text-[11px] font-medium tracking-wide text-faint uppercase">
            {weekday(d)}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((d) => {
          const day = items.filter((i) => sameDay(i.start, d))
          const exams = day.filter((i) => i.kind === 'exam')
          const deadlines = day.filter((i) => i.kind === 'deadline')
          const classes = day.filter((i) => i.kind === 'class').length
          const sessions = day.filter((i) => i.kind === 'session').length
          const other = d.getMonth() !== anchor.getMonth()
          return (
            <button
              key={d.toISOString()}
              type="button"
              onClick={() => onPick(d)}
              className={cx('grid min-h-28 content-start gap-1 border-t border-l border-line p-2 text-left transition-colors hover:bg-surface-2', other && 'opacity-40')}
            >
              <span className={cx('num grid size-6 place-items-center rounded-full text-[12px] font-semibold', sameDay(d, now) ? 'bg-accent text-accent-ink' : 'text-muted')}>
                {d.getDate()}
              </span>
              {exams.map((e) => (
                <span key={e.id} className="truncate rounded bg-exam-soft px-1.5 py-0.5 text-[11px] font-medium text-exam">
                  {e.title}
                </span>
              ))}
              {deadlines.slice(0, 2).map((e) => (
                <span key={e.id} className="truncate rounded bg-warn-soft px-1.5 py-0.5 text-[11px] font-medium text-warn">
                  {e.title}
                </span>
              ))}
              {(classes > 0 || sessions > 0 || deadlines.length > 2) && (
                <span className="text-[10.5px] text-faint">
                  {[classes && `${classes} aulas`, sessions && `${sessions} estudo`, deadlines.length > 2 && `+${deadlines.length - 2} prazos`].filter(Boolean).join(' · ')}
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function NewEventDialog({ open, onClose, courses, day }: { open: boolean; onClose: () => void; courses: Course[]; day: Date }) {
  const toast = useToast()
  const base = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 14)
  const [form, setForm] = useState({ title: '', course_id: '', starts_at: toLocalInput(base.toISOString()), ends_at: toLocalInput(new Date(base.getTime() + 3_600_000).toISOString()), location: '' })
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.createEvent({
        title: form.title.trim(),
        kind: 'appointment',
        course_id: form.course_id ? Number(form.course_id) : null,
        starts_at: fromLocalInput(form.starts_at)!,
        ends_at: fromLocalInput(form.ends_at)!,
        location: form.location || null,
      })
      toast('Compromisso criado', 'ok')
      refreshAll()
      onClose()
    } catch (err) {
      toast(`Não foi possível criar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Novo compromisso"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="event-form" disabled={!form.title.trim()}>
            Criar
          </Button>
        </>
      }
    >
      <form id="event-form" onSubmit={submit} className="grid gap-4">
        <Field label="Título">
          <Input autoFocus required maxLength={300} value={form.title} onChange={set('title')} placeholder="Ex.: Reunião de grupo" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Início">
            <Input type="datetime-local" required value={form.starts_at} onChange={set('starts_at')} />
          </Field>
          <Field label="Fim">
            <Input type="datetime-local" required value={form.ends_at} onChange={set('ends_at')} />
          </Field>
          <Field label="Cadeira">
            <Select value={form.course_id} onChange={set('course_id')}>
              <option value="">Nenhuma</option>
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Local">
            <Input maxLength={200} value={form.location} onChange={set('location')} />
          </Field>
        </div>
      </form>
    </Dialog>
  )
}

export default function Calendar() {
  const now = useNow(60_000)
  const [params, setParams] = useSearchParams()
  const anchor = params.get('date') ? new Date(`${params.get('date')}T12:00`) : startOfDay(now)
  const view = (params.get('view') as View) || (params.get('date') ? 'day' : 'week')
  const [hidden, setHidden] = useState<Set<Kind>>(new Set())
  const [courseId, setCourseId] = useState('')
  const [open, setOpen] = useState<Item | null>(null)
  const [creating, setCreating] = useState(false)

  const go = (date: Date, v: View = view) => setParams({ date: dayKey(date), view: v })
  const [start, end] = useMemo(() => {
    if (view === 'day') return [startOfDay(anchor), addDays(startOfDay(anchor), 1)]
    if (view === 'week') return [startOfWeek(anchor), addDays(startOfWeek(anchor), 7)]
    const first = startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1))
    return [first, addDays(first, 42)]
  }, [view, anchor.getTime()]) // eslint-disable-line react-hooks/exhaustive-deps

  const data = useApi(
    () =>
      Promise.all([
        api.events(start.toISOString(), end.toISOString()),
        api.sessions(start.toISOString(), end.toISOString()),
        api.tasks({ status: ['TODO', 'IN_PROGRESS', 'BLOCKED'], due_after: start.toISOString(), due_before: end.toISOString(), limit: 500 }),
      ]),
    [start.getTime(), end.getTime()],
  )
  const courses = useApi(() => api.courses(), [], 0)

  const items = data.data
    ? toItems(...data.data).filter((i) => !hidden.has(i.kind) && (!courseId || i.course?.id === Number(courseId)))
    : []
  const step = view === 'day' ? 1 : view === 'week' ? 7 : 0
  const move = (dir: number) =>
    go(step ? addDays(anchor, dir * step) : new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1))
  const title = view === 'day' ? longDay(anchor) : view === 'week' ? `${dayMonth(start)} – ${dayMonth(addDays(end, -1))}` : monthTitle(anchor)
  const days = view === 'day' ? [startOfDay(anchor)] : Array.from({ length: 7 }, (_, i) => addDays(start, i))

  return (
    <div>
      <PageHeader
        title="Calendário"
        subtitle="Aulas, avaliações e sessões de estudo, com os prazos de cada dia."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="size-4" /> Compromisso
          </Button>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1">
          <Button variant="secondary" size="icon" aria-label="Anterior" onClick={() => move(-1)}>
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="secondary" size="sm" className="h-8" onClick={() => go(startOfDay(now))}>
            Hoje
          </Button>
          <Button variant="secondary" size="icon" aria-label="Seguinte" onClick={() => move(1)}>
            <ChevronRight className="size-4" />
          </Button>
        </div>
        <h2 className="min-w-48 text-[15px] font-semibold tracking-tight">{title}</h2>
        <Segmented
          label="Vista"
          value={view}
          onChange={(v) => go(anchor, v)}
          options={[
            { value: 'day', label: 'Dia' },
            { value: 'week', label: 'Semana' },
            { value: 'month', label: 'Mês' },
          ]}
        />
        <div className="flex flex-wrap items-center gap-1.5 lg:ml-auto">
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={!hidden.has(k)}
              onClick={() => setHidden((h) => (h.has(k) ? new Set([...h].filter((x) => x !== k)) : new Set([...h, k])))}
              className={cx('h-7 rounded-full border px-2.5 text-xs font-medium transition-colors', hidden.has(k) ? 'border-line text-faint' : 'border-line-strong bg-surface-2 text-text')}
            >
              {KIND_LABEL[k]}
            </button>
          ))}
          <Select aria-label="Filtrar por cadeira" className="h-8 w-48 text-xs" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
            <option value="">Todas as cadeiras</option>
            {(courses.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {data.error && <ErrorNote error={data.error} onRetry={data.reload} />}
      {view === 'month' ? (
        <MonthGrid anchor={anchor} items={items} now={now} onPick={(d) => go(d, 'day')} />
      ) : (
        <TimeGrid days={days} items={items} now={now} onOpen={setOpen} />
      )}

      <Dialog open={!!open} onClose={() => setOpen(null)} title={open?.title ?? ''}>
        {open && (
          <dl className="grid gap-3 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={open.kind === 'exam' ? 'exam' : open.kind === 'deadline' ? 'warn' : open.kind === 'session' ? 'accent' : 'neutral'}>
                {KIND_ONE[open.kind]}
              </Badge>
              <CourseTag course={open.course} />
            </div>
            <div>
              <dt className="text-xs text-faint">Quando</dt>
              <dd className="num">
                {longDay(open.start)} · {time(open.start)}
                {open.end > open.start && `–${time(open.end)}`}
              </dd>
            </div>
            {open.location && (
              <div>
                <dt className="text-xs text-faint">Local</dt>
                <dd className="inline-flex items-center gap-1">
                  <MapPin className="size-3.5 text-muted" />
                  {open.location}
                </dd>
              </div>
            )}
            {open.note && (
              <div>
                <dt className="text-xs text-faint">Notas</dt>
                <dd>{open.note}</dd>
              </div>
            )}
            {open.url && (
              <a href={open.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
                Abrir na origem <ExternalLink className="size-3.5" />
              </a>
            )}
          </dl>
        )}
      </Dialog>
      <NewEventDialog open={creating} onClose={() => setCreating(false)} courses={courses.data ?? []} day={anchor} />
    </div>
  )
}
