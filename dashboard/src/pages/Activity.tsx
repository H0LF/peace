import { useState } from 'react'
import { SOURCE_NAME } from '../components/sync'
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, Segmented, Skeleton, cx, useToast } from '../components/ui'
import { api, type AuditEntry } from '../lib/api'
import { ago, dayMonth, duration, time } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'

type Tab = 'changes' | 'notifications' | 'syncs' | 'audit'
const STATUS_TONE = { success: 'ok', failed: 'danger', auth_failed: 'danger', running: 'accent' } as const
const STATUS_TEXT: Record<string, string> = { success: 'concluída', failed: 'falhou', auth_failed: 'login recusado', running: 'em curso' }

const ACTIONS: Record<string, (d: AuditEntry['detail']) => string> = {
  'task.status': (d) => `Tarefa ${d.task_id}: ${String(d.status).toLowerCase().replace('_', ' ')}`,
  'task.postpone': (d) => `Tarefa ${d.task_id} adiada`,
  'task.delete': (d) => `Tarefa apagada: ${d.title}`,
  'course.delete': (d) => `Cadeira apagada: ${d.name}`,
  'plan.generate': (d) => `Plano gerado: ${d.sessions} sessões`,
  'preferences.planner': () => 'Preferências do plano alteradas',
  'sync.manual': () => 'Sincronização manual pedida',
}

export default function ActivityPage() {
  const now = useNow(60_000)
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('changes')
  const changes = useApi(() => api.changes(), [])
  const runs = useApi(() => api.syncRuns(), [])
  const audit = useApi(() => api.audit(), [])
  const notes = useApi(() => api.notifications(), [])
  const current = { changes, notifications: notes, syncs: runs, audit }[tab]

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Atividade"
        subtitle="O que mudou nos sites, o histórico de sincronizações e as ações registadas."
        actions={
          <Segmented
            label="Secção"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'changes', label: 'Novidades' },
              { value: 'notifications', label: 'Notificações' },
              { value: 'syncs', label: 'Sincronizações' },
              { value: 'audit', label: 'Registo' },
            ]}
          />
        }
      />
      {current.error && <ErrorNote error={current.error} onRetry={current.reload} />}
      {!current.data ? (
        <Skeleton className="h-72" />
      ) : tab === 'changes' ? (
        <Card
          title="Alterações detetadas"
          action={
            changes.data?.some((c) => !c.seen_at) && (
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  await api.markSeen()
                  toast('Tudo marcado como visto', 'ok')
                  refreshAll()
                }}
              >
                Marcar tudo como visto
              </Button>
            )
          }
        >
          {changes.data!.length ? (
            <ul className="grid divide-y divide-line">
              {changes.data!.map((c) => (
                <li key={c.id} className="flex items-start gap-3 py-3">
                  <span className={cx('mt-1.5 size-2 shrink-0 rounded-full', !c.seen_at ? 'bg-accent' : 'bg-line-strong')} />
                  <div className="min-w-0 flex-1">
                    <p className={cx('text-[13px]', c.seen_at && 'text-muted')}>{c.summary}</p>
                    {c.kind === 'updated' && Object.keys(c.detail).length > 0 && (
                      <p className="mt-0.5 truncate text-xs text-faint">campos: {Object.keys(c.detail).join(', ')}</p>
                    )}
                  </div>
                  <div className="text-right text-[11px] text-faint">
                    <div>{SOURCE_NAME[c.source] ?? c.source}</div>
                    <div>{ago(c.created_at, now)}</div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="Ainda não há alterações registadas" />
          )}
        </Card>
      ) : tab === 'notifications' ? (
        <Card title="Notificações enviadas ao ambiente de trabalho">
          {notes.data!.length ? (
            <ul className="grid divide-y divide-line">
              {notes.data!.map((n) => (
                <li key={n.id} className="flex items-start gap-3 py-3">
                  <span className={cx('mt-1.5 size-2 shrink-0 rounded-full', n.urgent ? 'bg-danger' : 'bg-accent')} />
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] font-medium">{n.title}</p>
                    <p className="text-xs whitespace-pre-line text-muted">{n.body}</p>
                  </div>
                  <div className="text-right text-[11px] text-faint">
                    <div>{n.delivered_at ? `mostrada ${ago(n.delivered_at, now)}` : n.expires_at && new Date(n.expires_at) < now ? 'expirou' : 'por mostrar'}</div>
                    <div className="num">
                      {dayMonth(n.created_at)} · {time(n.created_at)}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="Ainda não houve notificações" />
          )}
        </Card>
      ) : tab === 'syncs' ? (
        <Card title="Sincronizações">
          {runs.data!.length ? (
            <ul className="grid divide-y divide-line">
              {runs.data!.map((r) => {
                const seconds = r.finished_at ? Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000) : null
                const changed = Object.values(r.stats?.changes ?? {}).reduce((a, b) => a + b, 0)
                return (
                  <li key={r.id} className="grid gap-1 py-3 sm:grid-cols-[160px_1fr_auto] sm:items-center sm:gap-4">
                    <div className="num text-[12.5px]">
                      {dayMonth(r.started_at)} · {time(r.started_at)}
                    </div>
                    <div className="flex min-w-0 flex-wrap items-center gap-2 text-[13px]">
                      <span className="font-medium">{SOURCE_NAME[r.source] ?? r.source}</span>
                      <Badge>{r.trigger === 'scheduled' ? 'agendada' : 'manual'}</Badge>
                      <Badge tone={STATUS_TONE[r.status as keyof typeof STATUS_TONE] ?? 'neutral'}>{STATUS_TEXT[r.status] ?? r.status}</Badge>
                      {r.status === 'success' && <span className="text-xs text-muted">{changed ? `${changed} alterações` : 'sem alterações'}</span>}
                      {r.error && <span className="w-full truncate text-xs text-danger sm:w-auto">{r.error}</span>}
                    </div>
                    <div className="num text-xs text-faint">{seconds !== null ? (seconds < 60 ? `${seconds} s` : duration(Math.round(seconds / 60))) : '…'}</div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <Empty title="Ainda não houve sincronizações" />
          )}
        </Card>
      ) : (
        <Card title="Registo de ações">
          {audit.data!.length ? (
            <ul className="grid divide-y divide-line">
              {audit.data!.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-4 py-2.5 text-[13px]">
                  <span>{ACTIONS[a.action]?.(a.detail) ?? a.action}</span>
                  <span className="num shrink-0 text-xs text-faint">
                    {dayMonth(a.created_at)} · {time(a.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="Sem ações registadas" />
          )}
        </Card>
      )}
    </div>
  )
}
