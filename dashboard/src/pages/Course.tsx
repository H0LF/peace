import { ArrowLeft, BookOpen, ExternalLink, GraduationCap, MapPin } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { MaterialList } from '../components/materials'
import { TaskDialog, TaskRow } from '../components/tasks'
import { Badge, Card, Empty, ErrorNote, Progress, Skeleton, courseColor } from '../components/ui'
import { api, type Task } from '../lib/api'
import { addDays, countdown, dayLabel, dayMonth, time } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

export default function Course() {
  const id = Number(useParams().id)
  const now = useNow()
  const [editing, setEditing] = useState<Task | null>(null)
  const data = useApi(
    () =>
      Promise.all([
        api.course(id),
        api.tasks({ course_id: id, limit: 500 }),
        api.events(addDays(now, -60).toISOString(), addDays(now, 150).toISOString(), id),
        api.materials(id),
      ]),
    [id],
  )

  if (data.error) return <ErrorNote error={data.error} onRetry={data.reload} />
  if (!data.data) return <Skeleton className="h-96" />
  const [course, tasks, events, materials] = data.data
  const open = tasks.filter((t) => !['COMPLETED', 'CANCELLED'].includes(t.status))
  const done = tasks.filter((t) => t.status === 'COMPLETED')
  const exams = events.filter((e) => e.kind === 'exam')
  const upcomingExams = exams.filter((e) => new Date(e.ends_at) > now)
  const classes = events.filter((e) => e.kind === 'class' && new Date(e.ends_at) > now).slice(0, 8)
  const progress = tasks.length ? Math.round((done.length / tasks.length) * 100) : 0

  return (
    <div className="grid gap-6">
      <div>
        <Link to="/cadeiras" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-text">
          <ArrowLeft className="size-3.5" /> Cadeiras
        </Link>
        <div className="flex flex-wrap items-start gap-4">
          <span className="mt-1.5 size-3 shrink-0 rounded-full" style={{ background: courseColor(course.id) }} />
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight text-balance">{course.name}</h1>
            <p className="num mt-1 text-[13px] text-muted">{course.code ?? 'sem código'}</p>
          </div>
          {course.url && (
            <a href={course.url} target="_blank" rel="noreferrer noopener" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
              Moodle <ExternalLink className="size-4" />
            </a>
          )}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <div className="text-xs text-muted">Próxima avaliação</div>
          <div className="mt-1 text-lg font-semibold tracking-tight">{upcomingExams[0] ? upcomingExams[0].title : '—'}</div>
          {upcomingExams[0] && <Badge tone="exam">{countdown(upcomingExams[0].starts_at, now)}</Badge>}
        </Card>
        <Card>
          <div className="text-xs text-muted">Tarefas abertas</div>
          <div className="num mt-1 text-2xl font-semibold tracking-tight">{open.length}</div>
        </Card>
        <Card>
          <div className="text-xs text-muted">Progresso nas tarefas</div>
          <div className="num mt-1 text-2xl font-semibold tracking-tight">{progress}%</div>
          <div className="mt-2">
            <Progress value={progress} tone="ok" label="Tarefas concluídas" />
          </div>
        </Card>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="grid gap-6">
          <Card title="Trabalhos e tarefas" bodyClassName="px-1 pb-2">
            {open.length ? (
              <ul className="grid">
                {open.map((t) => (
                  <TaskRow key={t.id} task={t} now={now} onEdit={setEditing} />
                ))}
              </ul>
            ) : (
              <div className="px-4">
                <Empty title="Nada por fazer nesta cadeira" />
              </div>
            )}
            {done.length > 0 && (
              <details className="mt-2 px-4">
                <summary className="cursor-pointer py-1 text-xs font-medium text-muted hover:text-text">Concluídas ({done.length})</summary>
                <ul className="-mx-4 grid">
                  {done.map((t) => (
                    <TaskRow key={t.id} task={t} now={now} onEdit={setEditing} />
                  ))}
                </ul>
              </details>
            )}
          </Card>
          <Card title="Materiais" action={materials.length > 0 && <Badge>{materials.filter((m) => m.kind === 'file').length} ficheiros</Badge>}>
            <MaterialList items={materials} now={now} />
          </Card>
          <Card title="Notas">
            <Empty icon={<BookOpen className="size-5" />} title="As tuas notas do Obsidian aparecem aqui">
              Indexação do cofre prevista na fase 7.
            </Empty>
          </Card>
        </div>
        <div className="grid gap-6">
          <Card title="Avaliações">
            {exams.length ? (
              <ul className="grid gap-2.5">
                {exams.map((e) => (
                  <li key={e.id} className="flex items-center gap-3">
                    <GraduationCap className="size-4 shrink-0 text-exam" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium">{e.title}</div>
                      <div className="num text-xs text-muted">
                        {dayMonth(e.starts_at)} · {time(e.starts_at)}
                      </div>
                    </div>
                    {new Date(e.ends_at) > now ? <Badge tone="exam">{countdown(e.starts_at, now)}</Badge> : <Badge>passou</Badge>}
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="Sem avaliações conhecidas" />
            )}
          </Card>
          <Card title="Próximas aulas">
            {classes.length ? (
              <ul className="grid gap-2">
                {classes.map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-3 text-[13px]">
                    <span className="num">
                      {dayLabel(new Date(e.starts_at), now)} <span className="text-muted">{time(e.starts_at)}</span>
                    </span>
                    <span className="inline-flex min-w-0 items-center gap-1 truncate text-xs text-muted">
                      {e.title}
                      {e.location && (
                        <>
                          <MapPin className="size-3" />
                          {e.location}
                        </>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="Sem aulas agendadas" />
            )}
          </Card>
        </div>
      </div>
      <TaskDialog open={!!editing} task={editing} onClose={() => setEditing(null)} />
    </div>
  )
}
