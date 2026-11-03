import { ChevronLeft, ChevronRight, ExternalLink, MapPin, Plus } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { useSearchParams } from 'react-router'
import { Badge, Button, CourseTag, Dialog, ErrorNote, Field, Input, PageHeader, Segmented, Select, courseColor, cx, useToast } from '../components/ui'
import { api, type CalEvent, type Course, type EventKind, type StudySession, type Task } from '../lib/api'
import { addDays, dayKey, dayMonth, fromLocalInput, longDay, monthTitle, sameDay, startOfDay, startOfWeek, time, toLocalInput, weekday } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'

type View = 'day' | 'week' | 'month'
type Kind = 'class' | 'exam' | 'appointment' | 'session' | 'deadline'
const FIRST_HOUR = 8
const LAST_HOUR = 23
const HOUR_PX = 52
const SAME_BLOCK_MS = 30 * 60_000 // the planner's breaks: two sessions of one subject this close are one stretch of study

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
  parts?: { start: Date; end: Date; topic?: string | null; done: boolean }[]
  event?: CalEvent
}

const KIND_LABEL: Record<Kind, string> = { class: 'Aulas', exam: 'Avaliações', appointment: 'Compromissos', session: 'Estudo', deadline: 'Prazos' }
const KIND_ONE: Record<Kind, string> = { class: 'Aula', exam: 'Avaliação', appointment: 'Compromisso', session: 'Sessão de estudo', deadline: 'Prazo' }

/** The planner cuts study into short sessions with breaks; on the calendar a stretch of one subject is one block. */
function studyBlocks(sessions: StudySession[]): Item[] {
  const out: Item[] = []
  const last = new Map<string, Item>()
  for (const s of [...sessions].filter((x) => x.status !== 'skipped').sort((a, b) => a.starts_at.localeCompare(b.starts_at))) {
    const start = new Date(s.starts_at)
    const end = new Date(s.ends_at)
    const part = { start, end, topic: s.topic, done: s.status === 'done' }
    const title = s.task?.title ?? s.event?.title ?? 'Sessão de estudo'
    const key = `${s.event?.id}|${s.task?.id}|${s.course_id}|${title}`
    const open = last.get(key)
    if (open && sameDay(open.start, start) && start.getTime() - open.end.getTime() <= SAME_BLOCK_MS) {
      if (end > open.end) open.end = end
      open.parts!.push(part)
      continue
    }
    const item: Item = {
      id: `s${s.id}`,
      kind: 'session',
      title,
      start,
      end,
      color: 'var(--accent)',
      course: null,
      note: [s.status === 'done' ? 'Concluída' : 'Planeada', s.reason].filter(Boolean).join(' · '),
      parts: [part],
    }
    out.push(item)
    last.set(key, item)
  }
  return out
}

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
      event: e,
    })),
    ...studyBlocks(sessions),
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

function Block({ item, onOpen, onGrab, onHover, ghost }: {
  item: Item & { lane: number; lanes: number }
  onOpen: (i: Item) => void
  onGrab?: (e: ReactPointerEvent, i: Item) => void
  onHover?: (i: Item | null) => void
  ghost?: boolean
}) {
  const startH = item.start.getHours() + item.start.getMinutes() / 60
  const endH = Math.min(LAST_HOUR + 1, item.end.getHours() + item.end.getMinutes() / 60 || 24)
  const top = (Math.max(startH, FIRST_HOUR) - FIRST_HOUR) * HOUR_PX
  const height = Math.max(22, (endH - Math.max(startH, FIRST_HOUR)) * HOUR_PX - 3)
  const width = 100 / item.lanes
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      onPointerDown={onGrab && ((e) => onGrab(e, item))}
      onPointerEnter={onHover && (() => onHover(item))}
      onPointerLeave={onHover && (() => onHover(null))}
      onFocus={onHover && (() => onHover(item))}
      onBlur={onHover && (() => onHover(null))}
      title={`${item.title} · ${time(item.start)}–${time(item.end)}${item.location ? ` · ${item.location}` : ''}`}
      className={cx(
        'absolute overflow-hidden rounded-lg border-l-[3px] px-2 py-1 text-left text-[11.5px] leading-tight transition-[filter] hover:brightness-125',
        onGrab && 'cursor-grab touch-none active:cursor-grabbing',
        ghost && 'pointer-events-none z-20 opacity-80 shadow-lg ring-1 ring-accent',
      )}
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

const SNAP_MIN = 15
const DRAG_PX = 4 // less than this is a click, not a drag

/** The day column under the pointer and the time at its height, snapped to 15 minutes. */
function slotAt(x: number, y: number, days: Date[]): Date | null {
  const col = document.elementsFromPoint(x, y).find((el) => (el as HTMLElement).dataset.day) as HTMLElement | undefined
  if (!col) return null
  const day = days[Number(col.dataset.day)]
  const minutes = Math.round((((y - col.getBoundingClientRect().top) / HOUR_PX) * 60) / SNAP_MIN) * SNAP_MIN
  const clamped = Math.min(Math.max(minutes, 0), (LAST_HOUR + 1 - FIRST_HOUR) * 60 - SNAP_MIN)
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), FIRST_HOUR, clamped)
}

/** Pointer capture, when the pointer really exists (Holo Hands sends mouse-shaped events that may not). */
function capture(el: Element, pointerId: number) {
  try {
    el.setPointerCapture(pointerId)
  } catch {
    /* synthetic pointer: events are sent to this element anyway */
  }
}

type Drag =
  | { mode: 'create'; from: Date; to: Date; x: number; y: number; moved: boolean }
  | { mode: 'move'; item: Item; grab: number; start: Date; x: number; y: number; moved: boolean }

/** Week/day grid. Double-click or drag on free space creates an event; dragging an event moves it (snaps to 15 min). */
function TimeGrid({ days, items, now, onOpen, onCreate, onMove, onHover }: {
  days: Date[]
  items: Item[]
  now: Date
  onOpen: (i: Item) => void
  onHover: (i: Item | null) => void
  onCreate: (start: Date, end: Date) => void
  onMove: (i: Item, start: Date, end: Date) => void
}) {
  const hours = Array.from({ length: LAST_HOUR - FIRST_HOUR + 1 }, (_, i) => FIRST_HOUR + i)
  const cols = `56px repeat(${days.length}, minmax(0, 1fr))`
  const [drag, setDrag] = useState<Drag | null>(null)
  const dragged = useRef(false) // swallows the click that ends a drag

  function startCreate(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0 || e.target !== e.currentTarget && !(e.target as HTMLElement).dataset.slot) return
    const at = slotAt(e.clientX, e.clientY, days)
    if (!at) return
    capture(e.currentTarget, e.pointerId)
    setDrag({ mode: 'create', from: at, to: at, x: e.clientX, y: e.clientY, moved: false })
  }
  function grab(e: ReactPointerEvent, item: Item) {
    if (e.button !== 0) return
    const at = slotAt(e.clientX, e.clientY, days)
    if (!at) return
    capture(e.currentTarget as HTMLElement, e.pointerId)
    setDrag({ mode: 'move', item, grab: at.getTime() - item.start.getTime(), start: item.start, x: e.clientX, y: e.clientY, moved: false })
  }
  function move(e: ReactPointerEvent) {
    if (!drag) return
    const moved = drag.moved || Math.hypot(e.clientX - drag.x, e.clientY - drag.y) >= DRAG_PX
    const at = slotAt(e.clientX, e.clientY, days)
    if (!at) return setDrag({ ...drag, moved })
    if (drag.mode === 'create') setDrag({ ...drag, to: at, moved })
    else setDrag({ ...drag, start: new Date(at.getTime() - drag.grab), moved })
  }
  function end() {
    if (!drag) return
    setDrag(null)
    if (!drag.moved) return
    dragged.current = true
    setTimeout(() => (dragged.current = false))
    if (drag.mode === 'create') {
      const [a, b] = drag.from <= drag.to ? [drag.from, drag.to] : [drag.to, drag.from]
      onCreate(a, new Date(b.getTime() + SNAP_MIN * 60_000))
    } else if (drag.start.getTime() !== drag.item.start.getTime()) {
      onMove(drag.item, drag.start, new Date(drag.start.getTime() + drag.item.end.getTime() - drag.item.start.getTime()))
    }
  }
  const open = (i: Item) => !dragged.current && onOpen(i)
  const movable = (i: Item) => !!i.event && i.event.source !== 'google'

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
        <div className="relative grid select-none" style={{ gridTemplateColumns: cols }} onPointerMove={move} onPointerUp={end} onPointerCancel={() => setDrag(null)}>
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
              <div
                key={d.toISOString()}
                data-day={days.indexOf(d)}
                onPointerDown={startCreate}
                onDoubleClick={(e) => {
                  if (e.target !== e.currentTarget && !(e.target as HTMLElement).dataset.slot) return
                  const at = slotAt(e.clientX, e.clientY, days)
                  if (at) onCreate(at, new Date(at.getTime() + 3_600_000))
                }}
                className={cx('relative cursor-cell border-l border-line', isToday && 'bg-accent/[0.03]')}
              >
                {hours.map((h) => (
                  <div key={h} data-slot className="border-t border-line/60" style={{ height: HOUR_PX }} />
                ))}
                {lanes(items.filter((i) => i.kind !== 'deadline' && sameDay(i.start, d))).map((it) => (
                  <Block
                    key={it.id}
                    item={it}
                    onOpen={open}
                    onGrab={movable(it) ? grab : undefined}
                    onHover={onHover}
                  />
                ))}
                {drag?.mode === 'move' && drag.moved && sameDay(drag.start, d) && (
                  <Block
                    ghost
                    item={{ ...drag.item, start: drag.start, end: new Date(drag.start.getTime() + drag.item.end.getTime() - drag.item.start.getTime()), lane: 0, lanes: 1 }}
                    onOpen={() => undefined}
                  />
                )}
                {drag?.mode === 'create' && drag.moved && sameDay(drag.from, d) && (() => {
                  const [a, b] = drag.from <= drag.to ? [drag.from, drag.to] : [drag.to, drag.from]
                  const top = ((a.getHours() - FIRST_HOUR) * 60 + a.getMinutes()) * (HOUR_PX / 60)
                  const height = ((b.getTime() - a.getTime()) / 60_000 + SNAP_MIN) * (HOUR_PX / 60)
                  return (
                    <div aria-hidden className="pointer-events-none absolute inset-x-1 z-20 rounded-lg border border-dashed border-accent bg-accent/15 px-2 py-1 text-[11px] text-accent" style={{ top, height }}>
                      {time(a)}–{time(new Date(b.getTime() + SNAP_MIN * 60_000))}
                    </div>
                  )
                })()}
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

const KIND_CHOICE: Record<EventKind, string> = { appointment: 'Compromisso', class: 'Aula', exam: 'Avaliação (exame, teste, frequência)' }

/** Create an event, or edit one (`event`): changes reach Google Calendar too when it is connected. */
function EventDialog({ open, onClose, courses, day, event, range }: { open: boolean; onClose: () => void; courses: Course[]; day: Date; event?: CalEvent; range?: [Date, Date] }) {
  const toast = useToast()
  const base = range?.[0] ?? new Date(day.getFullYear(), day.getMonth(), day.getDate(), 14)
  const until = range?.[1] ?? new Date(base.getTime() + 3_600_000)
  const [form, setForm] = useState(() =>
    event
      ? { title: event.title, kind: event.kind, course_id: event.course ? String(event.course.id) : '', starts_at: toLocalInput(event.starts_at), ends_at: toLocalInput(event.ends_at), location: event.location ?? '' }
      : { title: '', kind: 'appointment' as EventKind, course_id: '', starts_at: toLocalInput(base.toISOString()), ends_at: toLocalInput(until.toISOString()), location: '' },
  )
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))
  async function submit(e: FormEvent) {
    e.preventDefault()
    const data = {
      title: form.title.trim(),
      kind: form.kind,
      course_id: form.course_id ? Number(form.course_id) : null,
      starts_at: fromLocalInput(form.starts_at)!,
      ends_at: fromLocalInput(form.ends_at)!,
      location: form.location || null,
    }
    try {
      if (event) await api.updateEvent(event.id, data)
      else await api.createEvent(data)
      toast(event ? 'Evento guardado' : 'Evento criado', 'ok')
      refreshAll()
      onClose()
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={event ? 'Editar evento' : 'Novo evento'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="event-form" disabled={!form.title.trim()}>
            {event ? 'Guardar' : 'Criar'}
          </Button>
        </>
      }
    >
      <form id="event-form" onSubmit={submit} className="grid gap-4">
        <Field label="Título">
          <Input autoFocus required maxLength={300} value={form.title} onChange={set('title')} placeholder="Ex.: Reunião de grupo" />
        </Field>
        <Field label="Tipo">
          <Select value={form.kind} onChange={set('kind')}>
            {(Object.keys(KIND_CHOICE) as EventKind[]).map((k) => (
              <option key={k} value={k}>{KIND_CHOICE[k]}</option>
            ))}
          </Select>
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

/** Delete (or, for a university class, hide) an event; shared by the details dialog and the Delete key. */
async function deleteEvent(event: CalEvent, toast: ReturnType<typeof useToast>): Promise<boolean> {
  try {
    await api.deleteEvent(event.id)
    toast(event.source === 'manual' ? 'Evento apagado' : 'Escondido: não volta na próxima sincronização', 'ok')
    refreshAll()
    return true
  } catch (err) {
    toast(`Não foi possível apagar: ${(err as Error).message}`, 'error')
    return false
  }
}

/** Asks before deleting the event under the mouse when Delete is pressed. */
function ConfirmDelete({ event, onClose }: { event: CalEvent; onClose: () => void }) {
  const toast = useToast()
  const manual = event.source === 'manual'
  return (
    <Dialog
      open
      onClose={onClose}
      title={manual ? 'Apagar evento?' : 'Esconder aula?'}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="danger" autoFocus onClick={async () => (await deleteEvent(event, toast)) && onClose()}>
            {manual ? 'Apagar' : 'Esconder'}
          </Button>
        </>
      }
    >
      <p className="text-[13px] text-muted">
        «{event.title}», {longDay(new Date(event.starts_at))} às {time(new Date(event.starts_at))}.{' '}
        {manual
          ? 'Também sai do Google Calendar, se estiver ligado.'
          : 'Vem da universidade: fica escondida aqui e no Google, e não volta na próxima sincronização.'}
      </p>
    </Dialog>
  )
}

/** Edit or delete an event. University classes are hidden rather than deleted (the next sync would bring them back);
 * events from the user's other Google calendars are read-only here, except for their type. */
function EventActions({ event, onEdit, onDone }: { event: CalEvent; onEdit: () => void; onDone: () => void }) {
  const toast = useToast()
  const [confirming, setConfirming] = useState(false)
  if (event.source === 'google')
    return (
      <div className="flex w-full flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-faint">Do teu Google Calendar (só leitura)</span>
        <Select
          aria-label="Tipo do evento"
          className="h-8 w-44 text-xs"
          value={event.kind}
          onChange={async (e) => {
            try {
              await api.updateEvent(event.id, { kind: e.target.value as EventKind })
              toast('Tipo guardado', 'ok')
              refreshAll()
              onDone()
            } catch (err) {
              toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
            }
          }}
        >
          {(Object.keys(KIND_CHOICE) as EventKind[]).map((k) => (
            <option key={k} value={k}>{KIND_CHOICE[k]}</option>
          ))}
        </Select>
      </div>
    )
  async function remove() {
    if (await deleteEvent(event, toast)) onDone()
  }
  return (
    <>
      {confirming ? (
        <Button variant="danger" onClick={() => void remove()}>
          {event.source === 'manual' ? 'Confirmar: apagar' : 'Confirmar: esconder'}
        </Button>
      ) : (
        <Button variant="ghost" onClick={() => setConfirming(true)}>
          Apagar
        </Button>
      )}
      <Button variant="primary" onClick={onEdit}>
        Editar
      </Button>
    </>
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
  const [creating, setCreating] = useState<false | true | [Date, Date]>(false)
  const toast = useToast()
  async function moveEvent(item: Item, start: Date, end: Date) {
    try {
      await api.updateEvent(item.event!.id, { starts_at: start.toISOString(), ends_at: end.toISOString() })
      toast(`${item.title}: ${longDay(start)}, ${time(start)}`, 'ok')
      refreshAll()
    } catch (err) {
      toast(`Não foi possível mover: ${(err as Error).message}`, 'error')
    }
  }
  const [editing, setEditing] = useState<CalEvent | null>(null)
  const [hovered, setHovered] = useState<Item | null>(null)
  // Holo Hands peace sign: back to today in the week view.
  useEffect(() => {
    const reset = (e: Event) => {
      e.preventDefault()
      setParams({ date: dayKey(new Date()), view: 'week' })
    }
    window.addEventListener('peace:reset', reset)
    return () => window.removeEventListener('peace:reset', reset)
  }, [setParams])
  const [confirming, setConfirming] = useState<CalEvent | null>(null)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Delete' || !hovered || document.querySelector('dialog[open]')) return
      if ((e.target as HTMLElement).closest('input, textarea, select, [contenteditable]')) return
      if (!hovered.event) return toast('Só os eventos se apagam aqui (prazos e sessões não).', 'error')
      if (hovered.event.source === 'google') return toast('Vem de um calendário Google só de leitura: apaga-o no Google.', 'error')
      e.preventDefault()
      setConfirming(hovered.event)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [hovered]) // eslint-disable-line react-hooks/exhaustive-deps

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
            <Plus className="size-4" /> Evento
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
        <TimeGrid days={days} items={items} now={now} onOpen={setOpen} onCreate={(a, b) => setCreating([a, b])} onMove={(i, a, b) => void moveEvent(i, a, b)} onHover={setHovered} />
      )}

      <Dialog
        open={!!open}
        onClose={() => setOpen(null)}
        title={open?.title ?? ''}
        footer={
          open?.event && (
            <EventActions
              event={open.event}
              onEdit={() => {
                setEditing(open.event!)
                setOpen(null)
              }}
              onDone={() => setOpen(null)}
            />
          )
        }
      >
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
            {open.parts && open.parts.length > 1 && (
              <div>
                <dt className="text-xs text-faint">Partes</dt>
                <dd>
                  <ul className="grid gap-1">
                    {open.parts.map((p) => (
                      <li key={p.start.toISOString()}>
                        <span className="num">{time(p.start)}–{time(p.end)}</span>
                        {p.topic && ` · ${p.topic}`}
                        {p.done && ' · Concluída'}
                      </li>
                    ))}
                  </ul>
                </dd>
              </div>
            )}
            {open.note && (!open.parts || open.parts.length === 1) && (
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
      {confirming && <ConfirmDelete event={confirming} onClose={() => setConfirming(null)} />}
      {creating && <EventDialog open onClose={() => setCreating(false)} courses={courses.data ?? []} day={anchor} range={creating === true ? undefined : creating} />}
      {editing && <EventDialog open onClose={() => setEditing(null)} courses={courses.data ?? []} day={anchor} event={editing} />}
    </div>
  )
}
