import { useState } from 'react'
import { api } from '../lib/api'
import { useApi, useNow } from '../lib/hooks'
import { Button, ErrorNote, Skeleton } from './ui'

export default function NotificationDiagnostics() {
  const now = useNow(15_000)
  const diagnostics = useApi(() => api.notificationDiagnostics(), [], 15_000)
  const history = useApi(() => api.notifications(), [], 15_000)
  const [testing, setTesting] = useState(false)
  const [message, setMessage] = useState('')
  const status = diagnostics.data

  async function test() {
    setTesting(true)
    try {
      const notification = await api.testNotification()
      setMessage(`Teste #${notification.id} em fila. O agente do PC apresenta uma notificação nativa; confirma se a viste. Este teste explícito ignora o silêncio e a desativação automática.`)
      await Promise.all([diagnostics.reload(), history.reload()])
    } catch (error) {
      setMessage(`Teste não enviado: ${(error as Error).message}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <section aria-label="Diagnóstico de notificações" className="mt-5 grid gap-3 border-t border-line pt-5 text-[13px]">
      <h3 className="font-medium">Entrega no Linux</h3>
      {diagnostics.error ? <ErrorNote error={diagnostics.error} onRetry={diagnostics.reload} /> : !status ? <Skeleton className="h-12" /> : (
        <div className="grid gap-1 text-muted">
          <p>Agente de notificações: {status.delivery_connected ? 'ligado' : 'sem diagnóstico recente'}. Serviço nativo: {status.delivery_connected && status.delivery?.daemon_available ? 'disponível' : 'não confirmado'}.</p>
          <p>{status.pending} aviso(s) para entrega. Automáticas: {status.enabled ? 'ativas' : 'desativadas'}{status.quiet_hours ? ' · horário de silêncio' : ''}{status.focus_active ? ' · modo foco' : ''}.</p>
          {status.delivery?.result && <p>Último envio: {status.delivery.result === 'accepted' ? `aceite pelo serviço (ID ${status.delivery.native_id ?? '—'})` : 'falhou; será repetido enquanto válido'}.</p>}
          <p>A aceitação pelo serviço não confirma que o popup ficou visível. A confirmação visual é tua.</p>
        </div>
      )}
      <div><Button type="button" disabled={testing} onClick={() => void test()}>{testing ? 'A enviar…' : 'Testar notificação nativa'}</Button></div>
      {message && <p role="status" className="text-muted">{message}</p>}
      <details>
        <summary className="cursor-pointer font-medium">Histórico de notificações</summary>
        {history.error ? <ErrorNote error={history.error} onRetry={history.reload} /> : !history.data ? <Skeleton className="mt-3 h-12" /> : history.data.length === 0 ? <p className="mt-3 text-muted">Ainda sem notificações.</p> : (
          <ul className="mt-3 grid max-h-64 gap-3 overflow-y-auto">
            {history.data.map((n) => (
              <li key={n.id}>
                <span className="font-medium">{n.title}</span>
                <span className="block text-xs text-muted">
                  {new Date(n.created_at).toLocaleString('pt-PT')} · {n.delivered_at ? 'aceite pelo serviço' : n.expires_at && new Date(n.expires_at) <= now ? 'expirada' : 'por entregar'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </section>
  )
}
