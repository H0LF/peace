import { BookOpenCheck, Check, ChevronLeft, ChevronRight, FileText, ListChecks, X } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { ChunkBoundary, cx, useToast } from './ui'
import type { OrbState } from './Orb'
import { api, type DeliveryItem, type ExamItem, type Today, type Workload } from '../lib/api'
import { addDays, countdown, dayKey, duration, longDay, shortDay, startOfDay, time } from '../lib/format'
import { refreshAll } from '../lib/hooks'
import { VoiceCue } from './voice'
import { Wallpaper } from './wallpapers'

const Orb = lazy(() => import('./Orb'))
const hours = (minutes: number) => (minutes < 60 ? duration(minutes) : `${Math.round(minutes / 60)} h`)
const WEEKDAYS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']

type Card =
  | { key: string; kind: 'today'; day: string }
  | { key: string; kind: 'late'; day: string; items: DeliveryItem[] }
  | { key: string; kind: 'delivery'; day: string; item: DeliveryItem }
  | { key: string; kind: 'exam'; day: string; item: ExamItem }

/** Today first, then what is late, then everything ahead by date. */
function buildCards(work: Workload | undefined, now: Date): Card[] {
  const today = dayKey(now)
  const cards: Card[] = [{ key: 'today', kind: 'today', day: today }]
  const late = work?.deliveries.filter((d) => d.overdue) ?? []
  if (late.length) cards.push({ key: 'late', kind: 'late', day: today, items: late })
  const ahead: { at: string; card: Card }[] = [
    ...(work?.deliveries.filter((d) => !d.overdue) ?? []).map((item) => ({
      at: item.due_at,
      card: { key: `d${item.id}`, kind: 'delivery' as const, day: dayKey(new Date(item.due_at)), item },
    })),
    ...(work?.exams ?? []).map((item) => ({
      at: item.starts_at,
      card: { key: `e${item.id}`, kind: 'exam' as const, day: dayKey(new Date(item.starts_at)), item },
    })),
  ]
  return [...cards, ...ahead.sort((a, b) => a.at.localeCompare(b.at)).map((a) => a.card)]
}

function Action({ children, onClick, solid }: { children: ReactNode; onClick: () => void; solid?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx('inline-flex h-9 items-center gap-2 rounded-lg px-3.5 text-[13px] font-medium transition-colors', solid ? 'bg-accent text-bg hover:opacity-90' : 'border border-line-strong text-text hover:bg-surface-2')}
    >
      {children}
    </button>
  )
}

function useDone() {
  const toast = useToast()
  return async (d: DeliveryItem) => {
    try {
      await api.updateTask(d.id, { status: 'COMPLETED' })
      toast(`«${d.title}» feita`, 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
}

function Detail({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="animate-rise absolute inset-y-0 right-0 z-10 flex w-full flex-col bg-surface/90 p-6 text-text backdrop-blur-md sm:w-[56%]">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-[13px] font-semibold tracking-wide uppercase">{title}</h3>
        <button type="button" aria-label="Fechar" onClick={onClose} className="grid size-8 place-items-center rounded-lg hover:bg-surface-2"><X className="size-4" /></button>
      </div>
      <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1">{children}</div>
    </div>
  )
}

function DeliveryFace({ d, now }: { d: DeliveryItem; now: Date }) {
  const [open, setOpen] = useState(false)
  const done = useDone()
  return (
    <Face
      kicker={`Entrega${d.course ? ` · ${d.course.name}` : ''}`}
      title={d.title}
      meta={`${shortDay(d.due_at)}, ${time(d.due_at)} · ${countdown(d.due_at, now)} · ~${hours(d.minutes)}${d.estimated ? '' : ' (palpite)'}`}
      actions={
        <>
          <Action onClick={() => void done(d)}><Check className="size-4" /> Feita</Action>
          <Action solid onClick={() => setOpen(true)}><ListChecks className="size-4" /> O que fazer</Action>
        </>
      }
    >
      {open && (
        <Detail title="O que fazer" onClose={() => setOpen(false)}>
          {d.what.length ? (
            <ul className="grid list-disc gap-1.5 pl-4 text-[14px]">{d.what.map((w) => <li key={w}>{w}</li>)}</ul>
          ) : (
            <p className="text-[13px] text-muted">O enunciado ainda não foi lido.</p>
          )}
          {d.brief && (
            <a href={`/api/v1/materials/${d.brief.id}/file`} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-[13px] underline">
              <FileText aria-hidden className="size-4" /> Abrir enunciado
            </a>
          )}
        </Detail>
      )}
    </Face>
  )
}

function ExamFace({ e, now }: { e: ExamItem; now: Date }) {
  const [open, setOpen] = useState(false)
  return (
    <Face
      kicker={`Avaliação${e.course ? ` · ${e.course.name}` : ''}`}
      title={e.title}
      meta={`${shortDay(e.starts_at)}, ${time(e.starts_at)} · ${countdown(e.starts_at, now)}${e.minutes ? ` · ~${hours(e.minutes)} de estudo` : ''}`}
      actions={<Action solid onClick={() => setOpen(true)}><BookOpenCheck className="size-4" /> O que estudar</Action>}
    >
      {open && (
        <Detail title="O que estudar" onClose={() => setOpen(false)}>
          {e.topics.length ? (
            <ol className="grid gap-2.5">
              {e.topics.map((t) => (
                <li key={t.topic} className="text-[14px]">
                  <span className="flex items-baseline justify-between gap-3"><span>{t.topic}</span><span className="num shrink-0 text-xs text-muted">{hours(t.minutes)}</span></span>
                  {t.files.map((f) => (
                    <a key={f.id} href={`/api/v1/materials/${f.id}/file`} target="_blank" rel="noreferrer" className="block truncate text-xs text-muted underline">{f.name}</a>
                  ))}
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-[13px] text-muted">A PEACE ainda está a ler os ficheiros desta cadeira.</p>
          )}
        </Detail>
      )}
    </Face>
  )
}

function LateFace({ items, now }: { items: DeliveryItem[]; now: Date }) {
  const done = useDone()
  return (
    <Face kicker="Em atraso" title={items.length === 1 ? '1 entrega em atraso' : `${items.length} entregas em atraso`}>
      <ul className="face-in absolute inset-x-6 top-6 grid max-h-[55%] gap-1 overflow-y-auto text-text sm:inset-x-8 sm:top-8">
        {items.map((d) => (
          <li key={d.id} className="flex items-center gap-3 text-[14px]">
            <button type="button" aria-label={`Marcar «${d.title}» como feita`} onClick={() => void done(d)} className="grid size-5 shrink-0 place-items-center rounded-full border border-line-strong hover:bg-surface-2/20"><Check className="size-3" /></button>
            <span className="min-w-0 flex-1 truncate">{d.title}</span>
            <span className="num shrink-0 text-xs text-muted">{countdown(d.due_at, now)}</span>
          </li>
        ))}
      </ul>
    </Face>
  )
}

function Face({ kicker, title, meta, actions, children }: { kicker: string; title: string; meta?: string; actions?: ReactNode; children?: ReactNode }) {
  return (
    <>
      <div aria-hidden className="absolute inset-0 bg-gradient-to-t from-[var(--scrim)] via-[var(--scrim-mid)] to-transparent" />
      <div className="face-in absolute inset-x-6 bottom-6 flex flex-wrap items-end justify-between gap-4 text-text sm:inset-x-8 sm:bottom-8">
        <div className="min-w-0 max-w-2xl">
          <p className="text-xs font-medium text-muted">{kicker}</p>
          <h2 className="font-display mt-1 text-3xl leading-tight font-semibold sm:text-[40px]">{title}</h2>
          {meta && <p className="num mt-2 text-[13px] text-muted">{meta}</p>}
        </div>
        {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
      </div>
      {children}
    </>
  )
}

function HomeFace({ t, now, state, ahead }: { t?: Today; now: Date; state: OrbState; ahead?: Card }) {
  const items = t ? [
    ...t.events.map((e) => ({ key: `e${e.id}`, start: e.starts_at, title: e.title })),
    ...t.sessions.filter((s) => s.status === 'planned').map((s) => ({ key: `s${s.id}`, start: s.starts_at, title: s.task?.title ?? s.topic ?? s.event?.title ?? 'Estudo' })),
  ].sort((a, b) => a.start.localeCompare(b.start)) : []
  const left = items.filter((i) => new Date(i.start) >= now)
  const coming = ahead?.kind === 'delivery' ? ahead.item.title : ahead?.kind === 'exam' ? ahead.item.title : null
  const when = ahead?.kind === 'delivery' ? ahead.item.due_at : ahead?.kind === 'exam' ? ahead.item.starts_at : null
  return (
    <section aria-label="Hoje" className="absolute inset-0">
      <div className="absolute inset-x-0 top-0 bottom-24">
        <ChunkBoundary><Suspense fallback={null}><Orb hero bleed={0} state={state} className="size-full" /></Suspense></ChunkBoundary>
      </div>
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-1 text-center">
        <VoiceCue state={state} />
        <p className="text-xs text-muted">{longDay(now)}</p>
        <h2 className="font-display text-3xl leading-tight font-semibold">{left.length ? left[0].title : items.length ? 'Por hoje, acabou' : 'Dia livre'}</h2>
        <p className="num px-4 text-[13px] text-muted">
          {left.length ? `${time(left[0].start)} · ${countdown(left[0].start, now)}` : coming && when ? `A seguir: ${coming} · ${countdown(when, now)}` : 'Nada marcado'}
          {left.length > 1 && ` · depois ${left.slice(1, 3).map((i) => `${i.title} ${time(i.start)}`).join(', ')}`}
        </p>
      </div>
    </section>
  )
}

/** Seven days from today, each with how much falls on it. */
function WeekStrip({ cards, now, selected, onPick }: { cards: Card[]; now: Date; selected?: string; onPick: (day: string) => void }) {
  const days = [0, 1, 2, 3, 4, 5, 6].map((n) => addDays(startOfDay(now), n))
  return (
    <ol aria-label="Esta semana" className="glass grid grid-cols-7 gap-1 rounded-2xl p-1.5">
      {days.map((d, n) => {
        const key = dayKey(d)
        const count = cards.filter((c) => c.day === key && c.kind !== 'today' && c.kind !== 'late').length
        return (
          <li key={key}>
            <button
              type="button"
              aria-current={n === 0 ? 'date' : undefined}
              onClick={() => onPick(key)}
              className={cx(
                'num flex h-11 w-full flex-col items-center justify-center rounded-xl text-[11px] leading-tight transition-colors sm:h-9 sm:flex-row sm:gap-1.5 sm:text-xs',
                n === 0 ? 'bg-accent font-semibold text-bg' : selected === key ? 'bg-surface-2 text-text' : 'text-muted hover:bg-surface-2/60 hover:text-text',
              )}
            >
              <span>{WEEKDAYS[d.getDay()]}</span>
              <span>{d.getDate()}</span>
              {count > 0 && <span aria-label={`${count} ${count === 1 ? 'prazo' : 'prazos'}`} className={cx('size-1.5 rounded-full', n === 0 ? 'bg-bg' : 'bg-accent')} />}
            </button>
          </li>
        )
      })}
    </ol>
  )
}

const hueSeed = (c: Card) => (c.kind === 'delivery' || c.kind === 'exam' ? c.item.course?.id ?? 0 : 0)

/** The orb grows past the viewer when leaving, and comes back from there. */
const homeStyle = (offset: number): CSSProperties => (offset === 0
  ? { transform: 'scale(1)', opacity: 1, zIndex: 20 }
  : { transform: 'scale(1.35)', opacity: 0, filter: 'blur(14px)', zIndex: 0, pointerEvents: 'none' })

/** Cards wait in a pile behind the current one; a finished one swings away to the left. On the home screen, none show. */
function cardStyle(offset: number, atHome: boolean): CSSProperties {
  if (offset < 0) return { transform: 'translate3d(-70%, 0, 120px) rotateY(32deg)', opacity: 0, filter: 'blur(10px)', zIndex: 15, pointerEvents: 'none' }
  if (atHome) return { transform: 'translate3d(0, 60px, -220px) scale(0.8)', opacity: 0, filter: 'blur(10px)', zIndex: 1, pointerEvents: 'none' }
  return {
    transform: `translate3d(0, ${-offset * 40}px, ${-offset * 70}px)`, // the perspective pulls a far card's top down: this lifts it clear
    opacity: offset === 0 ? 1 : offset === 3 ? 0 : 1 - offset * 0.25,
    filter: offset === 0 ? 'none' : `blur(${offset}px)`,
    zIndex: 10 - offset,
    pointerEvents: offset === 0 ? 'auto' : 'none',
  }
}

/** The main page: the orb alone, then one card at a time with the others piled behind it. */
export function Deck({ t, work, now, state }: { t?: Today; work?: Workload; now: Date; state: OrbState }) {
  const navigate = useNavigate()
  const cards = useMemo(() => buildCards(work, now), [work, now])
  const [activeKey, setActiveKey] = useState('today')
  const found = cards.findIndex((c) => c.key === activeKey)
  const active = found < 0 ? 0 : found // the shown card may have just been ticked off
  const go = (i: number) => setActiveKey(cards[Math.max(0, Math.min(cards.length - 1, i))].key)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable || document.querySelector('dialog[open]')) return
      if (e.key === 'ArrowRight') setActiveKey((k) => cards[Math.min(cards.findIndex((c) => c.key === k) + 1, cards.length - 1)].key)
      if (e.key === 'ArrowLeft') setActiveKey((k) => cards[Math.max(cards.findIndex((c) => c.key === k) - 1, 0)].key)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [cards])

  function pick(day: string) {
    const hit = cards.find((c) => c.day === day && c.kind !== 'late' && (c.kind !== 'today' || day === dayKey(now)))
    if (hit) setActiveKey(hit.key)
    else navigate(`/calendario?view=day&date=${day}`)
  }

  // Talking to PEACE brings the orb back, whatever card is open.
  useEffect(() => {
    if (state === 'listening' || state === 'speaking') setActiveKey('today')
  }, [state])

  return (
    <>
      <WeekStrip cards={cards} now={now} selected={cards[active].day} onPick={pick} />
      <section aria-label="Cartões" aria-roledescription="carrossel" className="deck relative min-h-[440px] flex-1 lg:min-h-[240px]">
        {cards.map((c, i) => {
          const offset = i - active
          if (offset < -1 || offset > 3) return null
          const isActive = offset === 0
          const home = c.kind === 'today'
          return (
            <article
              key={c.key}
              aria-hidden={!isActive}
              inert={!isActive}
              data-active={isActive}
              style={home ? homeStyle(offset) : cardStyle(offset, active === 0)}
              className={cx('deck-card absolute inset-x-0 bottom-0 mx-auto', home ? 'top-0 w-full' : 'top-14 w-[min(92%,1040px)] overflow-hidden rounded-2xl border border-accent/25 shadow-2xl')}
            >
              {!home && <Wallpaper seed={c.kind === 'late' ? 2 : hueSeed(c)} />}
              {!home && isActive && <span aria-hidden key={c.key} className="card-sweep" />}
              {home && <HomeFace t={t} now={now} state={state} ahead={cards.find((x) => x.kind === 'delivery' || x.kind === 'exam')} />}
              {c.kind === 'late' && <LateFace items={c.items} now={now} />}
              {c.kind === 'delivery' && <DeliveryFace d={c.item} now={now} />}
              {c.kind === 'exam' && <ExamFace e={c.item} now={now} />}
            </article>
          )
        })}
        <button type="button" aria-label="Cartão anterior" disabled={active === 0} onClick={() => go(active - 1)} className="absolute top-1/2 left-2 z-20 grid size-10 -translate-y-1/2 place-items-center rounded-full border border-line bg-surface/70 text-text backdrop-blur transition hover:bg-surface-2 disabled:invisible"><ChevronLeft className="size-5" /></button>
        <button type="button" aria-label="Cartão seguinte" disabled={active === cards.length - 1} onClick={() => go(active + 1)} className="absolute top-1/2 right-2 z-20 grid size-10 -translate-y-1/2 place-items-center rounded-full border border-line bg-surface/70 text-text backdrop-blur transition hover:bg-surface-2 disabled:invisible"><ChevronRight className="size-5" /></button>
        <p aria-live="polite" className="num absolute top-2 right-4 z-20 text-[11px] text-muted">{active + 1} / {cards.length}</p>
      </section>
    </>
  )
}
