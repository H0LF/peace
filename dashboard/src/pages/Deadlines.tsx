import { Clock, ExternalLink, GraduationCap, Hourglass } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { URGENCY_TONE } from '../components/tasks'
import { Badge, CourseTag, Empty, ErrorNote, PageHeader, Segmented, Skeleton, cx } from '../components/ui'
import { api, type Deadline } from '../lib/api'
import { countdown, dayHeading, dayKey, time, urgency, type Urgency } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

const DOT: Record<Urgency, string> = { overdue: 'bg-danger', critical: 'bg-danger', soon: 'bg-warn', week: 'bg-accent', later: 'bg-faint' }

export function Heading({ date, now }: { date: Date; now: Date }) {
  const [main, extra] = dayHeading(date, now)
  return (
    <>
      {main}
      {extra && <span className="font-normal text-faint"> · {extra}</span>}
    </>
  )
}

export default function Deadlines() {
  const now = useNow()
  const [days, setDays] = useState('30')
  const data = useApi(() => api.deadlines(Number(days)), [days])

  const groups = new Map<string, Deadline[]>()
  for (const d of data.data ?? []) {
    const key = d.overdue ? 'overdue' : dayKey(new Date(d.due_at))
    groups.set(key, [...(groups.get(key) ?? []), d])
  }
  const exams = (data.data ?? []).filter((d) => d.type === 'exam').length
  const tasks = (data.data ?? []).length - exams

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Prazos"
        subtitle={data.data ? `${tasks} ${tasks === 1 ? 'entrega' : 'entregas'} e ${exams} ${exams === 1 ? 'avaliação' : 'avaliações'} nos próximos ${days} dias` : ' '}
        actions={
          <Segmented
            label="Horizonte"
            value={days}
            onChange={setDays}
            options={['7', '14', '30', '60', '120'].map((d) => ({ value: d, label: `${d} dias` }))}
          />
        }
      />
      {data.error && <ErrorNote error={data.error} onRetry={data.reload} />}
      {!data.data ? (
        <div className="grid gap-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      ) : groups.size === 0 ? (
        <Empty icon={<Hourglass className="size-6" />} title="Sem prazos neste horizonte" />
      ) : (
        <ol className="relative grid gap-6 before:absolute before:top-2 before:bottom-2 before:left-[7px] before:w-px before:bg-line">
          {[...groups.entries()].map(([key, items]) => {
            const date = key === 'overdue' ? null : new Date(`${key}T12:00`)
            return (
              <li key={key} className="relative pl-8">
                <span className={cx('absolute top-1.5 left-0 size-[15px] rounded-full border-4 border-bg', key === 'overdue' ? 'bg-danger' : DOT[urgency(items[0].due_at, now)])} />
                <h2 className={cx('mb-2 text-[13px] font-semibold', key === 'overdue' && 'text-danger')}>
                  {date ? <Heading date={date} now={now} /> : 'Em atraso'}
                </h2>
                <ul className="grid gap-2">
                  {items.map((d) => {
                    const u = urgency(d.due_at, now)
                    return (
                      <li key={`${d.type}${d.id}`} className="animate-rise flex items-center gap-4 rounded-xl border border-line bg-surface px-4 py-3">
                        <div className={cx('grid size-9 shrink-0 place-items-center rounded-lg', d.type === 'exam' ? 'bg-exam-soft text-exam' : 'bg-surface-3 text-muted')}>
                          {d.type === 'exam' ? <GraduationCap className="size-4" /> : <Clock className="size-4" />}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[13.5px] font-medium">{d.title}</div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-muted">
                            {d.course && (
                              <Link to={`/cadeiras/${d.course.id}`} className="inline-flex max-w-full min-w-0 hover:text-text">
                                <CourseTag course={d.course} />
                              </Link>
                            )}
                            <span className="num">{time(d.due_at)}</span>
                            <span>{d.type === 'exam' ? 'avaliação' : d.type === 'assignment' ? 'trabalho' : 'tarefa'}</span>
                          </div>
                        </div>
                        <div className="text-right">
                          <Badge tone={d.type === 'exam' ? 'exam' : URGENCY_TONE[u]} className="num">
                            {countdown(d.due_at, now)}
                          </Badge>
                        </div>
                        {d.type !== 'exam' && (
                          <Link to="/tarefas" aria-label="Ver em Tarefas" className="text-faint hover:text-text">
                            <ExternalLink className="size-4" />
                          </Link>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
