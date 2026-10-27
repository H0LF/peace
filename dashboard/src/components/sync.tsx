import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api, type SyncHealth } from '../lib/api'
import { ago, dayLabel, time } from '../lib/format'
import { refreshAll } from '../lib/hooks'
import { Badge, Button, cx, useToast } from './ui'

export const SOURCE_NAME: Record<string, string> = { inforestudante: 'Inforestudante', moodle: 'Moodle' }
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function SyncButton({ size = 'md' }: { size?: 'sm' | 'md' }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    try {
      // Wait for each source's latest run to change, so server/desktop clock skew does not matter.
      const before = new Map((await api.syncStatus()).map((s) => [s.source, s.last_run_at]))
      const { started } = await api.sync()
      for (let i = 0; i < 60; i++) {
        await sleep(1500)
        const now = await api.syncStatus()
        const finished = now.filter((s) => started.includes(s.source) && s.last_run_at !== before.get(s.source) && s.last_status !== 'running')
        if (finished.length === started.length) {
          const failed = finished.filter((s) => s.last_status !== 'success')
          if (failed.length) toast(`Sincronização falhou: ${failed.map((s) => SOURCE_NAME[s.source]).join(', ')}`, 'error')
          else toast('Sincronização concluída', 'ok')
          return
        }
      }
      toast('A sincronização continua em segundo plano', 'info')
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
      refreshAll()
    }
  }

  return (
    <Button size={size} onClick={run} disabled={busy}>
      <RefreshCw className={cx('size-4', busy && 'animate-spin')} />
      {busy ? 'A sincronizar…' : 'Sincronizar'}
    </Button>
  )
}

export function healthTone(s: SyncHealth) {
  if (s.needs_login || s.last_status === 'failed') return 'danger' as const
  if (s.stale) return 'warn' as const
  return 'ok' as const
}

export function SyncHealthList({ health, now }: { health: SyncHealth[]; now: Date }) {
  return (
    <ul className="grid gap-3">
      {health.map((s) => {
        const tone = healthTone(s)
        return (
          <li key={s.source} className="flex items-start gap-3">
            <span
              aria-hidden
              className={cx('mt-1.5 size-2 shrink-0 rounded-full', tone === 'ok' ? 'bg-ok' : tone === 'warn' ? 'bg-warn' : 'bg-danger')}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[13px] font-medium">{SOURCE_NAME[s.source] ?? s.source}</span>
                {s.needs_login && <Badge tone="danger">requer login</Badge>}
                {!s.needs_login && s.last_status === 'failed' && <Badge tone="danger">falhou</Badge>}
                {s.stale && !s.needs_login && s.last_status !== 'failed' && <Badge tone="warn">desatualizado</Badge>}
              </div>
              <p className="text-xs text-muted">
                {s.last_success_at ? `Última sincronização ${ago(s.last_success_at, now)}` : 'Nunca sincronizado'}
                {' · '}próxima {dayLabel(new Date(s.next_scheduled_at), now).toLowerCase()} às {time(s.next_scheduled_at)}
              </p>
              {s.last_error && s.last_status !== 'success' && <p className="mt-0.5 line-clamp-2 text-xs text-danger/90">{s.last_error}</p>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
