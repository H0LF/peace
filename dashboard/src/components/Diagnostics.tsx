import { api } from '../lib/api'
import type { HealthState } from '../lib/api'
import { useApi } from '../lib/hooks'
import { Badge, ErrorNote, Skeleton } from './ui'

const TONE = { ok: 'ok', warn: 'warn', error: 'danger', off: 'neutral' } as const
const LABEL: Record<HealthState, string> = { ok: 'ok', warn: 'atenção', error: 'problema', off: 'desligado' }

/** Every part of PEACE in one list; the code (e.g. backup_stale) names a problem exactly when reporting it. */
export default function Diagnostics() {
  const report = useApi(() => api.diagnostics(), [], 60_000)
  if (report.error) return <ErrorNote error={report.error} onRetry={report.reload} />
  if (!report.data) return <Skeleton className="h-40" />
  return (
    <div className="grid gap-4 text-[13px]">
      <ul className="divide-y divide-line">
        {report.data.components.map((c) => (
          <li key={c.id} className="flex items-start gap-3 py-2.5">
            <Badge tone={TONE[c.state]} className="mt-0.5 w-20 justify-center">{LABEL[c.state]}</Badge>
            <div className="min-w-0">
              <p className="font-medium">{c.name}</p>
              <p className="text-muted">{c.detail}{c.code && <span className="ml-1 font-mono text-[11px] text-faint">[{c.code}]</span>}</p>
            </div>
          </li>
        ))}
      </ul>
      {report.data.last_job && (
        <p className="text-muted">
          Última ação no PC: {report.data.last_job.title} — {report.data.last_job.summary ?? report.data.last_job.state}
        </p>
      )}
      <details>
        <summary className="cursor-pointer font-medium">Histórico de ações</summary>
        <ul className="mt-2 grid max-h-56 gap-1 overflow-y-auto text-muted">
          {report.data.history.map((h, i) => (
            <li key={i}>
              {new Date(h.at).toLocaleString('pt-PT')} · <span className="font-mono text-[12px]">{h.action}</span>
            </li>
          ))}
        </ul>
      </details>
      <p className="text-xs text-faint">Verificado {new Date(report.data.checked_at).toLocaleTimeString('pt-PT')}.</p>
    </div>
  )
}
