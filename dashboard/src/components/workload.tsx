import { BookOpenCheck, Check, ChevronDown, FileText, Hourglass } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { courseColor, cx, useToast } from './ui'
import { api, type DeliveryItem, type ExamItem, type Workload } from '../lib/api'
import { countdown, dayHeading, dayKey, duration, time, urgency } from '../lib/format'
import { refreshAll } from '../lib/hooks'

function readOpen(id: string): boolean {
  try {
    return localStorage.getItem(`peace-panel-${id}`) !== 'closed'
  } catch {
    return true
  }
}

/** A glass panel with an uppercase title; collapsible, the choice remembered per panel. */
export function Panel({ id, icon, title, aside, children, className }: { id: string; icon: ReactNode; title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
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

const hours = (minutes: number) => (minutes < 60 ? duration(minutes) : `${Math.round(minutes / 60)} h`)

function DeliveryRow({ d, now }: { d: DeliveryItem; now: Date }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const late = urgency(d.due_at, now) === 'overdue'
  async function done() {
    setBusy(true)
    try {
      await api.updateTask(d.id, { status: 'COMPLETED' })
      toast(`«${d.title}» feita`, 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
      setBusy(false)
    }
  }
  const detail = d.what.length > 0 || d.brief
  const head = (
    <span className="min-w-0 flex-1">
      <span className="block text-[14px] leading-snug">{d.title}</span>
      <span className="num mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
        {d.course && (
          <>
            <span aria-hidden className="size-1.5 rounded-full" style={{ background: courseColor(d.course.id) }} />
            <span className="truncate">{d.course.name}</span>
            <span aria-hidden>·</span>
          </>
        )}
        <span>{time(d.due_at)}</span>
        <span aria-hidden>·</span>
        <span className={late ? 'text-danger' : undefined}>{countdown(d.due_at, now)}</span>
        <span aria-hidden>·</span>
        <span title={d.estimated ? 'Estimativa lida do enunciado' : 'Sem enunciado: estimativa genérica'}>
          ~{hours(d.minutes)}
          {!d.estimated && ' (palpite)'}
        </span>
      </span>
    </span>
  )
  return (
    <li className="flex items-start gap-3 py-1.5">
      <button
        type="button"
        disabled={busy}
        onClick={() => void done()}
        aria-label={`Marcar «${d.title}» como feita`}
        className="group mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border border-faint hover:border-accent hover:bg-accent-soft disabled:opacity-50"
      >
        <Check className="size-3 text-accent opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100" />
      </button>
      {detail ? (
        <details className="group/d min-w-0 flex-1">
          <summary className="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden">
            {head}
            <ChevronDown aria-hidden className="mt-1 size-3.5 shrink-0 text-faint transition group-open/d:rotate-180" />
          </summary>
          {d.what.length > 0 && (
            <ul className="mt-2 grid list-disc gap-1 pl-4 text-[13px] text-text/90">
              {d.what.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          {d.brief && (
            <a href={`/api/v1/materials/${d.brief.id}/file`} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1.5 text-xs text-accent hover:underline">
              <FileText aria-hidden className="size-3.5" /> Abrir enunciado
            </a>
          )}
        </details>
      ) : (
        head
      )}
    </li>
  )
}

/** What to deliver, one heading per date. */
export function DeliveriesPanel({ data, now }: { data: Workload; now: Date }) {
  const groups = new Map<string, DeliveryItem[]>()
  for (const d of data.deliveries) {
    const key = d.overdue ? 'overdue' : dayKey(new Date(d.due_at))
    groups.set(key, [...(groups.get(key) ?? []), d])
  }
  const total = data.deliveries.reduce((sum, d) => sum + d.minutes, 0)
  return (
    <Panel
      id="entregas"
      icon={<Hourglass className="size-4" />}
      title="Para entregar"
      aside={
        <Link to="/prazos" className="num shrink-0 text-xs text-muted hover:text-accent">
          {total ? `~${hours(total)} de trabalho` : 'Prazos'}
        </Link>
      }
    >
      {groups.size ? (
        <div className="grid gap-3">
          {[...groups.entries()].map(([key, items]) => {
            const [main, extra] = key === 'overdue' ? ['Em atraso', null] : dayHeading(new Date(`${key}T12:00`), now)
            return (
              <div key={key}>
                <h3 className={cx('text-xs font-semibold', key === 'overdue' ? 'text-danger' : 'text-muted')}>
                  {main}
                  {extra && <span className="font-normal text-faint"> · {extra}</span>}
                </h3>
                <ul className="mt-1 grid">
                  {items.map((d) => <DeliveryRow key={d.id} d={d} now={now} />)}
                </ul>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="text-[13px] text-muted">Nada para entregar.</p>
      )}
    </Panel>
  )
}

function ExamRow({ e, now }: { e: ExamItem; now: Date }) {
  const head = (
    <span className="min-w-0 flex-1">
      <span className="block text-[14px] leading-snug">{e.title}</span>
      <span className="num mt-0.5 block text-xs text-muted">
        {dayHeading(new Date(e.starts_at), now)[0]} · {countdown(e.starts_at, now)}
        {e.minutes ? ` · ~${hours(e.minutes)} de estudo` : ' · a preparar o plano'}
      </span>
    </span>
  )
  if (!e.topics.length) return <li className="py-1.5">{head}</li>
  return (
    <li className="py-1.5">
      <details className="group/d">
        <summary className="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden">
          {head}
          <ChevronDown aria-hidden className="mt-1 size-3.5 shrink-0 text-faint transition group-open/d:rotate-180" />
        </summary>
        <ol className="mt-2 grid gap-1.5">
          {e.topics.map((t) => (
            <li key={t.topic} className="text-[13px]">
              <span className="flex items-baseline justify-between gap-3">
                <span>{t.topic}</span>
                <span className="num shrink-0 text-xs text-muted">{hours(t.minutes)}</span>
              </span>
              {t.files.length > 0 && (
                <span className="mt-0.5 flex flex-wrap gap-x-2 text-xs">
                  {t.files.map((f) => (
                    <a key={f.id} href={`/api/v1/materials/${f.id}/file`} target="_blank" rel="noreferrer" className="max-w-full truncate text-accent hover:underline">
                      {f.name}
                    </a>
                  ))}
                </span>
              )}
            </li>
          ))}
        </ol>
      </details>
    </li>
  )
}

/** What to study for each exam, and for how long. */
export function ExamsPanel({ data, now }: { data: Workload; now: Date }) {
  return (
    <Panel id="estudar" icon={<BookOpenCheck className="size-4" />} title="Para estudar">
      {data.exams.length ? (
        <ul className="grid">
          {data.exams.map((e) => <ExamRow key={e.id} e={e} now={now} />)}
        </ul>
      ) : (
        <p className="text-[13px] text-muted">Sem avaliações marcadas.</p>
      )}
    </Panel>
  )
}
