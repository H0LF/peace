import { CalendarClock, Clock, ExternalLink, MoreHorizontal, Pencil, Timer, Trash2 } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { api, type Course, type Task, type TaskInput, type TaskStatus } from '../lib/api'
import { addDays, countdown, duration, fromLocalInput, plural, startOfDay, toLocalInput, urgency, type Urgency } from '../lib/format'
import { refreshAll } from '../lib/hooks'
import { Badge, Button, Checkbox, CourseTag, Dialog, Field, Input, Menu, MenuItem, Progress, Select, Textarea, cx, useToast, type Tone } from './ui'

export const PRIORITY: Record<number, { label: string; bar: string; tone: Tone }> = {
  1: { label: 'Baixa', bar: 'bg-faint/40', tone: 'neutral' },
  2: { label: 'Normal', bar: 'bg-accent/60', tone: 'accent' },
  3: { label: 'Alta', bar: 'bg-warn', tone: 'warn' },
  4: { label: 'Crítica', bar: 'bg-danger', tone: 'danger' },
}
export const URGENCY_TONE: Record<Urgency, Tone> = { overdue: 'danger', critical: 'danger', soon: 'warn', week: 'accent', later: 'neutral' }
export const STATUS_LABEL: Record<TaskStatus, string> = {
  TODO: 'Por fazer',
  IN_PROGRESS: 'Em curso',
  BLOCKED: 'Bloqueada',
  COMPLETED: 'Concluída',
  CANCELLED: 'Cancelada',
}
export const SOURCE_LABEL: Record<string, string> = { moodle: 'Moodle', inforestudante: 'Inforestudante', manual: 'Manual' }

function postponeOptions(now: Date) {
  const at9 = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), 9)
  const toMonday = (8 - now.getDay()) % 7 || 7
  return [
    { label: 'Daqui a 3 horas', until: new Date(now.getTime() + 3 * 3_600_000) },
    { label: 'Amanhã às 9h', until: at9(addDays(now, 1)) },
    { label: 'Daqui a 3 dias', until: at9(addDays(now, 3)) },
    { label: 'Próxima segunda', until: at9(addDays(startOfDay(now), toMonday)) },
  ]
}

export function useTaskActions() {
  const toast = useToast()
  const fail = (e: unknown) => {
    toast(`Não foi possível guardar: ${(e as Error).message}`, 'error')
    refreshAll()
  }
  return {
    async setDone(task: Task, done: boolean) {
      try {
        await api.updateTask(task.id, { status: done ? 'COMPLETED' : 'TODO' })
        toast(done ? `Concluída: ${task.title}` : `Reaberta: ${task.title}`, 'ok')
        refreshAll()
      } catch (e) {
        fail(e)
      }
    },
    async postpone(task: Task, until: Date, label: string) {
      try {
        await api.postponeTask(task.id, until.toISOString())
        toast(`Adiada (${label.toLowerCase()}): ${task.title}`, 'ok')
        refreshAll()
      } catch (e) {
        fail(e)
      }
    },
    async startSession(task: Pick<Task, 'id' | 'title' | 'status'>, minutes: number) {
      const start = new Date()
      try {
        await api.createSession({ task_id: task.id, starts_at: start.toISOString(), ends_at: new Date(start.getTime() + minutes * 60_000).toISOString() })
        if (task.status === 'TODO') await api.updateTask(task.id, { status: 'IN_PROGRESS' })
        toast(`Sessão de ${duration(minutes)} iniciada: ${task.title}`, 'ok')
        refreshAll()
      } catch (e) {
        fail(e)
      }
    },
  }
}

export function TaskRow({ task, now, onEdit, compact = false }: { task: Task; now: Date; onEdit?: (t: Task) => void; compact?: boolean }) {
  const actions = useTaskActions()
  const [done, setDone] = useState(task.status === 'COMPLETED')
  useEffect(() => setDone(task.status === 'COMPLETED'), [task.status])
  const tone = URGENCY_TONE[urgency(task.due_at, now)]
  return (
    <li className="group relative flex items-start gap-3 rounded-xl py-2.5 pr-2 pl-4 transition-colors hover:bg-surface-2 focus-within:bg-surface-2">
      <span aria-hidden className={cx('absolute top-3 bottom-3 left-1 w-[3px] rounded-full', PRIORITY[task.priority].bar)} />
      <div className="pt-px">
        <Checkbox
          checked={done}
          label={`${done ? 'Reabrir' : 'Concluir'}: ${task.title}`}
          onChange={(v) => {
            setDone(v)
            void actions.setDone(task, v)
          }}
        />
      </div>
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => onEdit?.(task)}
          className={cx('block max-w-full truncate text-left text-[13.5px] font-medium transition-colors', done ? 'text-faint line-through' : 'text-text hover:text-accent')}
        >
          {task.title}
        </button>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          <CourseTag course={task.course} compact={compact} />
          {task.due_at && !done && (
            <Badge tone={tone}>
              <Clock className="size-3" />
              {countdown(task.due_at, now)}
            </Badge>
          )}
          {task.estimated_minutes ? <span className="num">{duration(task.estimated_minutes)}</span> : null}
          {task.status === 'IN_PROGRESS' && !done && <span className="text-accent">em curso</span>}
          {task.status === 'BLOCKED' && <span className="text-warn">bloqueada</span>}
          {task.postponed_count > 0 && <span className="text-faint">adiada {plural(task.postponed_count, 'vez', 'vezes')}</span>}
          {task.source !== 'manual' && <span className="text-faint">{SOURCE_LABEL[task.source] ?? task.source}</span>}
        </div>
        {task.progress > 0 && !done && (
          <div className="mt-2 flex max-w-56 items-center gap-2">
            <Progress value={task.progress} label={`Progresso de ${task.title}`} />
            <span className="num text-[11px] text-faint">{task.progress}%</span>
          </div>
        )}
      </div>
      <div className="flex items-center gap-0.5 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 sm:opacity-0">
        {task.url && (
          <a
            href={task.url}
            target="_blank"
            rel="noreferrer noopener"
            aria-label={`Abrir ${task.title} no site`}
            className="grid size-8 place-items-center rounded-lg text-muted hover:bg-surface-3 hover:text-text"
          >
            <ExternalLink className="size-4" />
          </a>
        )}
        <Menu label={`Ações: ${task.title}`} trigger={<MoreHorizontal className="size-4" />}>
          {(close) => (
            <>
              <MenuItem onSelect={() => (close(), void actions.startSession(task, 50))}>
                <Timer className="size-4 text-muted" /> Começar sessão de 50 min
              </MenuItem>
              {postponeOptions(now).map((o) => (
                <MenuItem key={o.label} onSelect={() => (close(), void actions.postpone(task, o.until, o.label))}>
                  <CalendarClock className="size-4 text-muted" /> Adiar: {o.label}
                </MenuItem>
              ))}
              {onEdit && (
                <MenuItem onSelect={() => (close(), onEdit(task))}>
                  <Pencil className="size-4 text-muted" /> Editar
                </MenuItem>
              )}
            </>
          )}
        </Menu>
      </div>
    </li>
  )
}

type Form = {
  title: string
  description: string
  course_id: string
  priority: string
  due_at: string
  estimated_minutes: string
  status: TaskStatus
  progress: string
}

const formFor = (task?: Task | null): Form => ({
  title: task?.title ?? '',
  description: task?.description ?? '',
  course_id: task?.course ? String(task.course.id) : '',
  priority: String(task?.priority ?? 2),
  due_at: toLocalInput(task?.due_at ?? null),
  estimated_minutes: task?.estimated_minutes ? String(task.estimated_minutes) : '',
  status: task?.status ?? 'TODO',
  progress: String(task?.progress ?? 0),
})

export function TaskDialog({ open, onClose, task }: { open: boolean; onClose: () => void; task?: Task | null }) {
  const toast = useToast()
  const [courses, setCourses] = useState<Course[]>([])
  const [form, setForm] = useState<Form>(formFor(task))
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    setForm(formFor(task))
    api.courses().then(setCourses, () => setCourses([]))
  }, [open, task])

  const set = (key: keyof Form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [key]: e.target.value }))

  async function submit(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    const data: TaskInput = {
      title: form.title.trim(),
      description: form.description,
      course_id: form.course_id ? Number(form.course_id) : null,
      priority: Number(form.priority),
      due_at: fromLocalInput(form.due_at),
      estimated_minutes: form.estimated_minutes ? Number(form.estimated_minutes) : null,
    }
    try {
      if (task) await api.updateTask(task.id, { ...data, status: form.status, progress: Number(form.progress) })
      else await api.createTask(data)
      toast(task ? 'Tarefa guardada' : 'Tarefa criada', 'ok')
      refreshAll()
      onClose()
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!task || !window.confirm(`Apagar «${task.title}»?`)) return
    try {
      await api.deleteTask(task.id)
      toast('Tarefa apagada', 'ok')
      refreshAll()
      onClose()
    } catch (err) {
      toast(`Não foi possível apagar: ${(err as Error).message}`, 'error')
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={task ? 'Editar tarefa' : 'Nova tarefa'}
      footer={
        <>
          {task?.source === 'manual' && (
            <Button variant="danger" className="mr-auto" onClick={remove}>
              <Trash2 className="size-4" /> Apagar
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="task-form" disabled={saving || !form.title.trim()}>
            {saving ? 'A guardar…' : 'Guardar'}
          </Button>
        </>
      }
    >
      <form id="task-form" onSubmit={submit} className="grid gap-4">
        {task && task.source !== 'manual' && (
          <p className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted">
            Importada de {SOURCE_LABEL[task.source]}. As tuas alterações mantêm-se até a origem alterar o mesmo campo.
          </p>
        )}
        <Field label="Título">
          <Input autoFocus required maxLength={300} value={form.title} onChange={set('title')} placeholder="Ex.: Rever capítulo 3 de Redes" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Cadeira">
            <Select value={form.course_id} onChange={set('course_id')}>
              <option value="">Sem cadeira</option>
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Prioridade">
            <Select value={form.priority} onChange={set('priority')}>
              {[4, 3, 2, 1].map((p) => (
                <option key={p} value={p}>
                  {PRIORITY[p].label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Prazo">
            <Input type="datetime-local" value={form.due_at} onChange={set('due_at')} />
          </Field>
          <Field label="Duração estimada (min)">
            <Input type="number" min={1} max={10000} step={5} value={form.estimated_minutes} onChange={set('estimated_minutes')} placeholder="60" />
          </Field>
          {task && (
            <>
              <Field label="Estado">
                <Select value={form.status} onChange={set('status')}>
                  {(Object.keys(STATUS_LABEL) as TaskStatus[]).map((s) => (
                    <option key={s} value={s}>
                      {STATUS_LABEL[s]}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label={`Progresso: ${form.progress}%`}>
                <input type="range" min={0} max={100} step={5} value={form.progress} onChange={set('progress')} className="h-9 accent-[var(--accent)]" />
              </Field>
            </>
          )}
        </div>
        <Field label="Notas">
          <Textarea maxLength={20000} value={form.description} onChange={set('description')} />
        </Field>
      </form>
    </Dialog>
  )
}
