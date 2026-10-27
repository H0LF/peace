import { CalendarCheck2, CalendarRange, Check, ChevronLeft, ChevronRight, MoreHorizontal, Pencil, SkipForward, Timer, Trash2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, Input, Menu, MenuItem, PageHeader, Progress, Skeleton, cx, useToast } from '../components/ui'
import { StudyMaterials } from '../components/materials'
import { api, type StudySession } from '../lib/api'
import { addDays, dayKey, dayMonth, duration, fromLocalInput, plural, sameDay, startOfDay, startOfWeek, time, toLocalInput } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'
import { Heading } from './Deadlines'

const minutes = (s: StudySession) => Math.round((new Date(s.ends_at).getTime() - new Date(s.starts_at).getTime()) / 60_000)

function EditSession({ session, onClose }: { session: StudySession | null; onClose: () => void }) {
  const toast = useToast()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [loaded, setLoaded] = useState<number | null>(null)
  if (session && loaded !== session.id) {
    setLoaded(session.id)
    setStart(toLocalInput(session.starts_at))
    setEnd(toLocalInput(session.ends_at))
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!session) return
    try {
      await api.updateSession(session.id, { starts_at: fromLocalInput(start)!, ends_at: fromLocalInput(end)! })
      toast('Sessão reagendada', 'ok')
      refreshAll()
      onClose()
    } catch (err) {
      toast((err as Error).message, 'error')
    }
  }
  return (
    <Dialog
      open={!!session}
      onClose={onClose}
      title="Reagendar sessão"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="session-form">
            Guardar
          </Button>
        </>
      }
    >
      <form id="session-form" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <p className="text-[13px] text-muted sm:col-span-2">{session?.task?.title ?? session?.event?.title ?? 'Sessão de estudo'}</p>
        <Field label="Início">
          <Input type="datetime-local" required value={start} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="Fim">
          <Input type="datetime-local" required value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
      </form>
    </Dialog>
  )
}

function SessionRow({ s, now, onEdit }: { s: StudySession; now: Date; onEdit: (s: StudySession) => void }) {
  const toast = useToast()
  const past = new Date(s.ends_at) <= now
  const missed = past && s.status === 'planned'
  async function patch(data: Parameters<typeof api.updateSession>[1], message: string) {
    try {
      await api.updateSession(s.id, data)
      toast(message, 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const tomorrow = (iso: string) => addDays(new Date(iso), 1).toISOString()
  return (
    <li className={cx('group flex items-center gap-3 rounded-xl px-3 py-2.5 hover:bg-surface-2', s.status === 'skipped' && 'opacity-50')}>
      <div className="num w-24 shrink-0 text-[12.5px]">
        <span className="font-medium">{time(s.starts_at)}</span>
        <span className="text-faint">–{time(s.ends_at)}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className={cx('truncate text-[13.5px] font-medium', s.status === 'done' && 'text-muted line-through')}>{s.task?.title ?? s.event?.title ?? 'Sessão de estudo'}</div>
        {s.reason && <div className="truncate text-xs text-faint">{s.reason}</div>}
        {s.progress_note && <p className="text-xs text-warn">{s.progress_note}</p>}
        {s.status !== 'skipped' && <StudyMaterials items={s.materials ?? []} compact className="mt-1.5" />}
      </div>
      <span className="num hidden text-xs text-muted sm:inline">{duration(minutes(s))}</span>
      {s.status === 'done' && <Badge tone="ok">feita</Badge>}
      {s.status === 'skipped' && <Badge>saltada</Badge>}
      {missed && <Badge tone="warn">por fazer</Badge>}
      {s.status === 'planned' && (
        <Button size="sm" variant="ghost" onClick={() => patch({ status: 'done' }, 'Sessão concluída')}>
          <Check className="size-3.5" /> Feita
        </Button>
      )}
      <Menu label="Mais ações" trigger={<MoreHorizontal className="size-4" />}>
        {(close) => (
          <>
            {s.status === 'planned' && (
              <MenuItem onSelect={() => (close(), void patch({ status: 'skipped' }, 'Sessão saltada'))}>
                <SkipForward className="size-4 text-muted" /> Saltar
              </MenuItem>
            )}
            {s.status !== 'done' && (
              <MenuItem onSelect={() => (close(), void patch({ starts_at: tomorrow(s.starts_at), ends_at: tomorrow(s.ends_at), status: 'planned' }, 'Adiada para amanhã'))}>
                <CalendarRange className="size-4 text-muted" /> Adiar para amanhã
              </MenuItem>
            )}
            <MenuItem onSelect={() => (close(), onEdit(s))}>
              <Pencil className="size-4 text-muted" /> Mudar hora
            </MenuItem>
            <MenuItem
              onSelect={async () => {
                close()
                await api.deleteSession(s.id)
                toast('Sessão removida', 'ok')
                refreshAll()
              }}
            >
              <Trash2 className="size-4 text-muted" /> Remover
            </MenuItem>
          </>
        )}
      </Menu>
    </li>
  )
}

export default function Planner() {
  const now = useNow()
  const toast = useToast()
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()))
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<StudySession | null>(null)
  const end = addDays(weekStart, 7)
  const data = useApi(() => api.sessions(weekStart.toISOString(), end.toISOString()), [weekStart.getTime()])
  const recommendation = useApi(api.recommendation, [])
  const sessions = data.data ?? []
  const counted = sessions.filter((s) => s.status !== 'skipped')
  const planned = counted.reduce((a, s) => a + minutes(s), 0)
  const done = sessions.filter((s) => s.status === 'done').reduce((a, s) => a + minutes(s), 0)
  const missed = sessions.filter((s) => s.status === 'planned' && new Date(s.ends_at) <= now).length

  async function plan(kind: 'day' | 'week') {
    setBusy(true)
    try {
      const result = kind === 'day' ? await api.planDay() : await api.planWeek(dayKey(weekStart < startOfDay(now) ? startOfDay(now) : weekStart))
      const fresh = result.filter((s) => s.status === 'planned' && new Date(s.starts_at) >= now)
      toast(fresh.length ? `${plural(fresh.length, 'sessão planeada', 'sessões planeadas')}` : 'Sem tempo livre ou tarefas por planear', 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const days = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i))

  return (
    <div>
      <PageHeader
        title="Plano de estudo"
        subtitle={
          <>
            Sessões propostas a partir dos prazos, prioridades e tempo livre.{' '}
            <Link to="/definicoes" className="text-accent hover:underline">
              Ajustar preferências
            </Link>
          </>
        }
        actions={
          <>
            <Button onClick={() => plan('day')} disabled={busy}>
              <CalendarCheck2 className="size-4" /> Planear hoje
            </Button>
            <Button variant="primary" onClick={() => plan('week')} disabled={busy}>
              <CalendarRange className="size-4" /> Planear semana
            </Button>
          </>
        }
      />

      {recommendation.data && (
        <Card className="mb-6" title="Recomendado agora">
          <p className="font-medium">{recommendation.data.title} · {recommendation.data.minutes} min</p>
          <p className="mt-1 text-sm text-muted">{recommendation.data.reasons.join(' · ')}</p>
          <p className="mt-1 text-sm text-muted">{recommendation.data.objective}</p>
          {recommendation.data.starts_at && <p className="mt-1 text-xs text-muted">Próximo bloco: {dayMonth(recommendation.data.starts_at)} · {time(recommendation.data.starts_at)}</p>}
          {recommendation.data.warnings?.map((warning) => <p key={warning} className="mt-1 text-xs text-warn">{warning}</p>)}
          <StudyMaterials items={recommendation.data.materials ?? []} className="mt-3" />
        </Card>
      )}

      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <Card>
          <div className="text-xs text-muted">Planeado esta semana</div>
          <div className="num mt-1 text-2xl font-semibold tracking-tight">{duration(planned)}</div>
        </Card>
        <Card>
          <div className="text-xs text-muted">Feito</div>
          <div className="num mt-1 text-2xl font-semibold tracking-tight text-ok">{duration(done)}</div>
          <div className="mt-2">
            <Progress value={planned ? (done / planned) * 100 : 0} tone="ok" label="Estudo concluído esta semana" />
          </div>
        </Card>
        <Card>
          <div className="text-xs text-muted">Sessões por fazer no passado</div>
          <div className={cx('num mt-1 text-2xl font-semibold tracking-tight', missed > 0 && 'text-warn')}>{missed}</div>
          {missed > 0 && <div className="mt-1 text-xs text-faint">«Planear hoje» volta a distribuir o trabalho.</div>}
        </Card>
      </div>

      <div className="mb-4 flex items-center gap-2">
        <Button variant="secondary" size="icon" aria-label="Semana anterior" onClick={() => setWeekStart(addDays(weekStart, -7))}>
          <ChevronLeft className="size-4" />
        </Button>
        <Button variant="secondary" size="sm" className="h-8" onClick={() => setWeekStart(startOfWeek(new Date()))}>
          Esta semana
        </Button>
        <Button variant="secondary" size="icon" aria-label="Semana seguinte" onClick={() => setWeekStart(addDays(weekStart, 7))}>
          <ChevronRight className="size-4" />
        </Button>
        <h2 className="ml-2 text-[15px] font-semibold">
          {dayMonth(weekStart)} – {dayMonth(addDays(end, -1))}
        </h2>
      </div>

      {data.error && <ErrorNote error={data.error} onRetry={data.reload} />}
      {!data.data ? (
        <Skeleton className="h-64" />
      ) : !sessions.length ? (
        <Empty icon={<Timer className="size-6" />} title="Ainda não há sessões nesta semana">
          Carrega em «Planear semana» para o PEACE propor um plano realista.
        </Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {days.map((day) => {
            const list = sessions.filter((s) => sameDay(new Date(s.starts_at), day))
            if (!list.length) return null
            const total = list.filter((s) => s.status !== 'skipped').reduce((a, s) => a + minutes(s), 0)
            return (
              <Card
                key={day.toISOString()}
                title={
                  <span className={cx(sameDay(day, now) && 'text-accent')}>
                    <Heading date={day} now={now} />
                  </span>
                }
                action={<span className="num text-xs text-muted">{duration(total)}</span>}
                bodyClassName="px-1 pb-2"
              >
                <ul className="grid">
                  {list.map((s) => (
                    <SessionRow key={s.id} s={s} now={now} onEdit={setEditing} />
                  ))}
                </ul>
              </Card>
            )
          })}
        </div>
      )}
      <EditSession session={editing} onClose={() => setEditing(null)} />
    </div>
  )
}
