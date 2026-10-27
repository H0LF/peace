import { ExternalLink, GraduationCap } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router'
import { Badge, Empty, ErrorNote, PageHeader, Skeleton, courseColor } from '../components/ui'
import { api, type CalEvent, type Course, type Task } from '../lib/api'
import { addDays, countdown, dayLabel, time } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

type Summary = { course: Course; nextClass?: CalEvent; nextExam?: CalEvent; open: Task[]; nextDue?: Task }

function CourseCard({ s, now }: { s: Summary; now: Date }) {
  return (
    <Link
      to={`/cadeiras/${s.course.id}`}
      className="animate-rise group relative grid gap-4 overflow-hidden rounded-2xl border border-line bg-surface p-5 transition-colors hover:border-line-strong hover:bg-surface-2"
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: courseColor(s.course.id) }} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[15px] leading-snug font-semibold tracking-tight text-balance">{s.course.name}</h2>
          <p className="num mt-0.5 text-xs text-faint">{s.course.code ?? 'sem código'}</p>
        </div>
        {s.course.url && (
          <span className="text-faint group-hover:text-muted" title="Tem página no Moodle">
            <ExternalLink className="size-4" />
          </span>
        )}
      </div>
      <dl className="grid gap-2 text-[12.5px]">
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Próxima aula</dt>
          <dd className="num text-right">{s.nextClass ? `${dayLabel(new Date(s.nextClass.starts_at), now)} ${time(s.nextClass.starts_at)}` : '—'}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Próxima avaliação</dt>
          <dd className="text-right">{s.nextExam ? <Badge tone="exam">{countdown(s.nextExam.starts_at, now)}</Badge> : '—'}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="text-muted">Tarefas abertas</dt>
          <dd className="num text-right">{s.open.length || '—'}</dd>
        </div>
      </dl>
    </Link>
  )
}

export default function Courses() {
  const now = useNow(60_000)
  const data = useApi(
    () =>
      Promise.all([
        api.courses(),
        api.tasks({ status: ['TODO', 'IN_PROGRESS', 'BLOCKED'], limit: 1000 }),
        api.events(now.toISOString(), addDays(now, 120).toISOString()),
      ]),
    [],
  )

  const [active, quiet] = useMemo(() => {
    if (!data.data) return [[], []]
    const [courses, tasks, events] = data.data
    const summaries: Summary[] = courses.map((course) => {
      const mine = events.filter((e) => e.course?.id === course.id)
      const open = tasks.filter((t) => t.course?.id === course.id)
      return {
        course,
        nextClass: mine.find((e) => e.kind === 'class'),
        nextExam: mine.find((e) => e.kind === 'exam'),
        open,
        nextDue: open.find((t) => t.due_at),
      }
    })
    const soonest = (s: Summary) =>
      Math.min(...[s.nextClass?.starts_at, s.nextExam?.starts_at, s.nextDue?.due_at].filter(Boolean).map((d) => new Date(d!).getTime()))
    const busy = summaries.filter((s) => s.nextClass || s.nextExam || s.open.length).sort((a, b) => soonest(a) - soonest(b))
    return [busy, summaries.filter((s) => !busy.includes(s))]
  }, [data.data])

  return (
    <div>
      <PageHeader title="Cadeiras" subtitle="As unidades curriculares deste ano, ordenadas pela próxima atividade." />
      {data.error && <ErrorNote error={data.error} onRetry={data.reload} />}
      {!data.data ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-44" />
          ))}
        </div>
      ) : !active.length && !quiet.length ? (
        <Empty icon={<GraduationCap className="size-6" />} title="Ainda não há cadeiras">
          Sincroniza o Inforestudante e o Moodle para as importar.
        </Empty>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {active.map((s) => (
              <CourseCard key={s.course.id} s={s} now={now} />
            ))}
          </div>
          {quiet.length > 0 && (
            <details className="mt-8">
              <summary className="cursor-pointer text-[13px] font-medium text-muted hover:text-text">
                Sem atividade agendada ({quiet.length})
              </summary>
              <ul className="mt-3 grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
                {quiet.map((s) => (
                  <li key={s.course.id}>
                    <Link to={`/cadeiras/${s.course.id}`} className="flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] text-muted hover:bg-surface-2 hover:text-text">
                      <span className="size-2 rounded-full" style={{ background: courseColor(s.course.id) }} />
                      {s.course.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  )
}
