import { ArrowRightLeft, ListChecks, Plus, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { PRIORITY, STATUS_LABEL, TaskDialog, TaskRow, URGENCY_TONE } from '../components/tasks'
import { Badge, Button, Card, CourseTag, Empty, ErrorNote, Input, Menu, MenuItem, PageHeader, Segmented, Select, Skeleton, cx, useToast } from '../components/ui'
import { api, type Task, type TaskStatus } from '../lib/api'
import { addDays, countdown, startOfDay, urgency } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'

type Filter = 'open' | 'done' | 'all'
const STATUSES: Record<Filter, TaskStatus[] | undefined> = {
  open: ['TODO', 'IN_PROGRESS', 'BLOCKED'],
  done: ['COMPLETED', 'CANCELLED'],
  all: undefined,
}
const COLUMNS: TaskStatus[] = ['TODO', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED']

function groupByDue(tasks: Task[], now: Date) {
  const today = startOfDay(now)
  const groups: { label: string; tasks: Task[] }[] = [
    { label: 'Em atraso', tasks: [] },
    { label: 'Hoje', tasks: [] },
    { label: 'Amanhã', tasks: [] },
    { label: 'Próximos 7 dias', tasks: [] },
    { label: 'Mais tarde', tasks: [] },
    { label: 'Sem prazo', tasks: [] },
    { label: 'Concluídas', tasks: [] },
  ]
  for (const t of tasks) {
    const due = t.due_at ? new Date(t.due_at) : null
    const i =
      t.status === 'COMPLETED' || t.status === 'CANCELLED'
        ? 6
        : !due
          ? 5
          : due < now
            ? 0
            : due < addDays(today, 1)
              ? 1
              : due < addDays(today, 2)
                ? 2
                : due < addDays(today, 8)
                  ? 3
                  : 4
    groups[i].tasks.push(t)
  }
  return groups.filter((g) => g.tasks.length)
}

function Board({ tasks, now, onEdit }: { tasks: Task[]; now: Date; onEdit: (t: Task) => void }) {
  const toast = useToast()
  const [dragging, setDragging] = useState<number | null>(null)
  const [over, setOver] = useState<TaskStatus | null>(null)

  async function move(task: Task, status: TaskStatus) {
    if (task.status === status) return
    try {
      await api.updateTask(task.id, { status })
      toast(`${task.title} → ${STATUS_LABEL[status]}`, 'ok')
      refreshAll()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {COLUMNS.map((status) => {
        const column = tasks.filter((t) => t.status === status)
        return (
          <section
            key={status}
            aria-label={STATUS_LABEL[status]}
            onDragOver={(e) => {
              e.preventDefault()
              setOver(status)
            }}
            onDragLeave={() => setOver(null)}
            onDrop={() => {
              const task = tasks.find((t) => t.id === dragging)
              setOver(null)
              if (task) void move(task, status)
            }}
            className={cx('grid content-start gap-2 rounded-2xl border bg-surface p-3 transition-colors', over === status ? 'border-accent/60 bg-accent-soft' : 'border-line')}
          >
            <header className="flex items-center justify-between px-1 pb-1">
              <h2 className="text-[13px] font-semibold">{STATUS_LABEL[status]}</h2>
              <Badge>{column.length}</Badge>
            </header>
            {column.map((t) => (
              <article
                key={t.id}
                draggable
                onDragStart={() => setDragging(t.id)}
                onDragEnd={() => setDragging(null)}
                className={cx('group cursor-grab rounded-xl border border-line bg-surface-2 p-3 transition-shadow hover:border-line-strong active:cursor-grabbing', dragging === t.id && 'opacity-50')}
              >
                <div className="flex items-start gap-2">
                  <span className={cx('mt-1.5 size-2 shrink-0 rounded-full', PRIORITY[t.priority].bar)} />
                  <button type="button" onClick={() => onEdit(t)} className="min-w-0 flex-1 text-left text-[13px] leading-snug font-medium hover:text-accent">
                    {t.title}
                  </button>
                  <Menu label={`Mover ${t.title}`} trigger={<ArrowRightLeft className="size-3.5" />}>
                    {(close) =>
                      COLUMNS.filter((s) => s !== t.status).map((s) => (
                        <MenuItem key={s} onSelect={() => (close(), void move(t, s))}>
                          {STATUS_LABEL[s]}
                        </MenuItem>
                      ))
                    }
                  </Menu>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 pl-4">
                  <CourseTag course={t.course} compact />
                  {t.due_at && t.status !== 'COMPLETED' && <Badge tone={URGENCY_TONE[urgency(t.due_at, now)]}>{countdown(t.due_at, now)}</Badge>}
                </div>
              </article>
            ))}
            {!column.length && <p className="px-1 py-4 text-center text-xs text-faint">Arrasta tarefas para aqui</p>}
          </section>
        )
      })}
    </div>
  )
}

export default function Tasks() {
  const now = useNow()
  const [filter, setFilter] = useState<Filter>('open')
  const [view, setView] = useState<'list' | 'board'>('list')
  const [courseId, setCourseId] = useState('')
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<Task | null>(null)
  const [creating, setCreating] = useState(false)
  const search = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 250)
    return () => window.clearTimeout(t)
  }, [q])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && !document.querySelector('dialog[open]')) {
        e.preventDefault()
        search.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const boardFilter = view === 'board' ? 'all' : filter
  const tasks = useApi(
    () => api.tasks({ status: STATUSES[boardFilter], course_id: courseId ? Number(courseId) : undefined, q: query || undefined, limit: 500 }),
    [boardFilter, courseId, query],
  )
  const courses = useApi(() => api.courses(), [], 0)
  const groups = useMemo(() => groupByDue(tasks.data ?? [], now), [tasks.data, now])
  const count = tasks.data?.length ?? 0

  return (
    <div>
      <PageHeader
        title="Tarefas"
        subtitle={tasks.data ? `${count} ${count === 1 ? 'tarefa' : 'tarefas'}${filter === 'open' && view === 'list' ? ' por concluir' : ''}` : ' '}
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus className="size-4" /> Nova tarefa
          </Button>
        }
      />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-faint" />
          <Input ref={search} aria-label="Procurar tarefas" placeholder="Procurar…  (/)" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" />
        </div>
        {view === 'list' && (
          <Segmented
            label="Estado"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'open', label: 'Por fazer' },
              { value: 'done', label: 'Concluídas' },
              { value: 'all', label: 'Todas' },
            ]}
          />
        )}
        <Select aria-label="Filtrar por cadeira" className="w-56" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
          <option value="">Todas as cadeiras</option>
          {(courses.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
        <div className="sm:ml-auto">
          <Segmented
            label="Vista"
            value={view}
            onChange={setView}
            options={[
              { value: 'list', label: 'Lista' },
              { value: 'board', label: 'Quadro' },
            ]}
          />
        </div>
      </div>

      {tasks.error && <ErrorNote error={tasks.error} onRetry={tasks.reload} />}
      {tasks.loading && !tasks.data ? (
        <div className="grid gap-3">
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
          <Skeleton className="h-14" />
        </div>
      ) : view === 'board' ? (
        <Board tasks={tasks.data ?? []} now={now} onEdit={setEditing} />
      ) : groups.length ? (
        <div className="grid gap-5">
          {groups.map((g) => (
            <Card key={g.label} title={<span className={g.label === 'Em atraso' ? 'text-danger' : ''}>{g.label}</span>} action={<Badge>{g.tasks.length}</Badge>} bodyClassName="px-1 pb-2">
              <ul className="grid">
                {g.tasks.map((t) => (
                  <TaskRow key={t.id} task={t} now={now} onEdit={setEditing} />
                ))}
              </ul>
            </Card>
          ))}
        </div>
      ) : (
        <Empty icon={<ListChecks className="size-6" />} title={query ? `Nada encontrado para «${query}»` : 'Sem tarefas'}>
          {query ? 'Tenta outras palavras.' : 'Cria uma com «Nova tarefa» ou carrega n.'}
        </Empty>
      )}

      <TaskDialog open={creating || !!editing} task={editing} onClose={() => (setEditing(null), setCreating(false))} />
    </div>
  )
}
