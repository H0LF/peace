import { CalendarClock, Check, Info, RefreshCw, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { api, type StudyPlan, type StudySession } from '../lib/api'
import { countdown, dayMonth, duration, time } from '../lib/format'
import { refreshAll, useApi } from '../lib/hooks'
import { StudyMaterials } from './materials'
import { Badge, Button, Card, Progress, Select, useToast } from './ui'

const ACTIVITY: Record<string, string> = { learn: 'Estudar', recall: 'Testar de memória', practice: 'Resolver exercícios', review: 'Revisão final' }
const BASIS: Record<string, string> = {
  guide: 'Baseado no guia lido dos ficheiros da cadeira',
  concepts: 'Baseado nos conceitos da cadeira',
  files: 'Baseado nos ficheiros da cadeira',
  none: 'Sem materiais: plano genérico',
}

function SessionRow({ s, reload }: { s: StudySession; reload: () => void }) {
  const toast = useToast()
  const name = ACTIVITY[s.activity ?? 'learn'] + (s.topic ? `: ${s.topic}` : '')
  const finish = () => api.updateSession(s.id, { status: 'done' }).then(() => (reload(), refreshAll()), (e: Error) => toast(e.message, 'error'))
  return (
    <li className="grid gap-1 py-2">
      <div className="flex items-start gap-3">
        <button type="button" aria-label={`Marcar como feita: ${name}`} title="Marcar como feita" onClick={() => void finish()} className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border border-line-strong text-faint hover:border-ok hover:text-ok">
          <Check className="size-3.5" />
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium">{name}</p>
          <p className="num text-xs text-muted">{dayMonth(s.starts_at)} · {time(s.starts_at)}–{time(s.ends_at)}</p>
        </div>
      </div>
      {s.materials.length > 0 && <StudyMaterials items={s.materials} compact className="ml-8" />}
    </li>
  )
}

function PlanCard({ plan, now, reload }: { plan: StudyPlan; now: Date; reload: () => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [all, setAll] = useState(false)
  const shown = all ? plan.sessions : plan.sessions.slice(0, 3)

  async function again() {
    setBusy(true)
    try {
      await api.replanStudyPlan(plan.id)
      toast('Plano reorganizado com o tempo que resta', 'ok')
      reload()
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  async function remove() {
    if (!window.confirm(`Apagar o plano de ${plan.exam.title}? As sessões já feitas ficam registadas.`)) return
    try {
      await api.deleteStudyPlan(plan.id)
      reload()
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <Card title={plan.exam.title} action={<Badge tone="exam">{countdown(plan.exam.starts_at, now)}</Badge>}>
      <div className="grid gap-3">
        <div className="flex items-center gap-3">
          <Progress value={plan.progress} tone="ok" label="Progresso do plano" />
          <span className="num shrink-0 text-xs text-muted">{plan.progress}%</span>
        </div>
        <p className="text-[13px]">
          {plan.enough_time ? (
            <span className="text-ok">Tens tempo suficiente.</span>
          ) : (
            <span className="text-warn">Não há tempo para tudo: faltam cerca de {duration(plan.short_minutes)}. Ficou de fora o menos importante.</span>
          )}{' '}
          <span className="text-muted">Estudado: {duration(plan.done_minutes)}. Por fazer: {duration(plan.planned_minutes)}.</span>
        </p>
        {plan.sessions.length > 0 ? (
          <div>
            <h3 className="text-[11px] font-semibold tracking-wide text-faint uppercase">A seguir</h3>
            <ul className="divide-y divide-line">
              {shown.map((s) => (
                <SessionRow key={s.id} s={s} reload={reload} />
              ))}
            </ul>
            {plan.sessions.length > 3 && (
              <button type="button" onClick={() => setAll(!all)} className="mt-1 text-xs text-accent hover:underline">
                {all ? 'Mostrar menos' : `Ver todas (${plan.sessions.length})`}
              </button>
            )}
          </div>
        ) : (
          <p className="text-[13px] text-muted">Sem sessões por fazer.</p>
        )}
        <details className="text-xs text-muted">
          <summary className="flex cursor-pointer items-center gap-1.5 hover:text-text">
            <Info aria-hidden className="size-3.5" /> Como foi feito este plano
          </summary>
          <p className="mt-2 font-medium">{BASIS[plan.basis]}</p>
          <ul className="mt-1 grid list-disc gap-1 pl-4">
            {plan.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          <ul className="mt-2 grid gap-0.5">
            {plan.topics.map((t) => (
              <li key={t.topic} className="flex justify-between gap-3">
                <span className="truncate">{t.topic}</span>
                <span className="num shrink-0">{duration(t.done)} / {duration(t.minutes)}</span>
              </li>
            ))}
          </ul>
        </details>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={() => void again()}>
            <RefreshCw className="size-3.5" /> Reorganizar
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void remove()}>
            <Trash2 className="size-3.5" /> Apagar plano
          </Button>
          <Link to="/definicoes?separador=integracoes" className="inline-flex h-8 items-center gap-1 px-2 text-xs text-muted hover:text-text">
            <CalendarClock aria-hidden className="size-3.5" /> Ver no Google Calendar
          </Link>
        </div>
      </div>
    </Card>
  )
}

/** Preparing for exams: a plan per exam, what to do next, and what the plan rests on. */
export function StudyPlans({ now }: { now: Date }) {
  const toast = useToast()
  const data = useApi(() => api.studyPlans(), [], 120_000)
  const [busy, setBusy] = useState(false)
  if (!data.data || (!data.data.plans.length && !data.data.exams.length)) return null

  async function create(id: number) {
    setBusy(true)
    try {
      await api.createStudyPlan(id)
      toast('Plano de estudo criado', 'ok')
      data.reload()
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-6 grid gap-4">
      {data.data.plans.map((p) => (
        <PlanCard key={p.id} plan={p} now={now} reload={data.reload} />
      ))}
      {data.data.exams.length > 0 && (
        <label className="flex flex-wrap items-center gap-3 text-[13px] text-muted">
          Criar um plano de estudo para
          <Select aria-label="Criar um plano de estudo para" disabled={busy} value="" onChange={(e) => e.target.value && void create(Number(e.target.value))} className="w-auto">
            <option value="">escolhe uma avaliação…</option>
            {data.data.exams.map((e) => (
              <option key={e.id} value={e.id}>{e.title} ({dayMonth(e.starts_at)})</option>
            ))}
          </Select>
        </label>
      )}
    </div>
  )
}

export function PrepareButton({ eventId }: { eventId: number }) {
  const toast = useToast()
  return (
    <Link
      to="/estudo"
      onClick={() => {
        api.createStudyPlan(eventId).then(refreshAll, (e: Error) => toast(e.message, 'error'))
      }}
      className="shrink-0 text-xs text-accent hover:underline"
    >
      Preparar
    </Link>
  )
}
