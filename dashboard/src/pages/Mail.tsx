import { Mail as MailIcon, MoreHorizontal, RefreshCw, Search } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Input, Menu, MenuItem, PageHeader, Progress, Segmented, Skeleton, useToast } from '../components/ui'
import { api, type MailMessage, type MailStatus } from '../lib/api'
import { ago, dayMonth, megabytes, time } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

type View = 'academic' | 'all' | 'largest' | 'removed'
const VIEWS: { value: View; label: string }[] = [
  { value: 'academic', label: 'Da universidade' },
  { value: 'all', label: 'Caixa de entrada' },
  { value: 'largest', label: 'Maiores' },
  { value: 'removed', label: 'Limpos pela PEACE' },
]

const when = (iso: string | null) => (iso ? `${dayMonth(iso)} · ${time(iso)}` : '')

function Usage({ st, now, onSync }: { st: MailStatus; now: Date; onSync: () => void }) {
  const used = st.used_bytes ?? 0
  const percent = st.quota_mb ? Math.round((used / (st.quota_mb * 2 ** 20)) * 100) : null
  return (
    <Card
      title="Caixa de correio"
      action={
        <Button size="sm" variant="ghost" onClick={onSync}>
          <RefreshCw className="size-3.5" /> Ler agora
        </Button>
      }
    >
      <div className="grid gap-3 text-[13px]">
        {st.needs_login ? (
          <p className="text-danger">
            O servidor recusou o login. Atualiza a palavra-passe em <Link className="underline" to="/definicoes">Definições → Integrações</Link>.
          </p>
        ) : st.error ? (
          <p className="text-warn">{st.error}</p>
        ) : null}
        <p>
          {st.used_bytes === null ? 'O espaço ainda não foi medido.' : `Ocupa ${megabytes(used)}${st.quota_mb ? ` de ${st.quota_mb} MB (${percent} %)` : ''}.`}{' '}
          <span className="text-muted">
            {st.inbox} na caixa de entrada · {st.junk} no lixo eletrónico
          </span>
        </p>
        {percent !== null && <Progress value={percent} label="Espaço usado" />}
        <p className="text-xs text-faint">
          {st.last_success ? `Lida ${ago(st.last_success, now)}.` : 'Ainda não foi lida.'}{' '}
          {st.clean_junk ? 'O lixo eletrónico com mais de 14 dias vai para Itens Eliminados.' : 'A limpeza do lixo eletrónico está desligada.'} Outras
          limpezas aparecem em{' '}
          <Link className="underline" to="/rever">
            Rever
          </Link>{' '}
          e só acontecem se aplicares.
        </p>
      </div>
    </Card>
  )
}

function MessageRow({ m, onOpen, onKeep, onRestore }: { m: MailMessage; onOpen: () => void; onKeep: () => void; onRestore: () => void }) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
        <span className={m.seen ? 'block truncate text-[13px]' : 'block truncate text-[13px] font-semibold'}>{m.subject || '(sem assunto)'}</span>
        <span className="block truncate text-xs text-faint">
          {m.sender_name || m.sender} · {when(m.sent_at)}
        </span>
      </button>
      {m.course && <Badge className="hidden sm:inline-flex">{m.course.name}</Badge>}
      {m.notice && <Badge className="hidden sm:inline-flex">aviso de site</Badge>}
      <span className="num hidden text-xs text-muted sm:inline">{megabytes(m.size)}</span>
      {m.removed_at ? (
        m.restorable ? (
          <Button size="sm" variant="ghost" onClick={onRestore}>
            Repor
          </Button>
        ) : (
          <span className="text-xs text-faint">esvaziado</span>
        )
      ) : (
        <Menu label="Mais ações" trigger={<MoreHorizontal className="size-4" />}>
          {(close) => <MenuItem onSelect={() => (close(), onKeep())}>Nunca limpar emails deste remetente</MenuItem>}
        </Menu>
      )}
    </li>
  )
}

function MessageDialog({ m, onClose }: { m: MailMessage | null; onClose: () => void }) {
  const full = useApi(() => (m ? api.mailMessage(m.id) : Promise.resolve(null)), [m?.id])
  return (
    <Dialog open={!!m} onClose={onClose} title={m?.subject || '(sem assunto)'}>
      {m && (
        <div className="grid gap-3 text-[13px]">
          <p className="text-xs text-muted">
            {m.sender_name ? `${m.sender_name} <${m.sender}>` : m.sender} · {when(m.sent_at)}
          </p>
          {m.facts.length > 0 && (
            <ul className="grid gap-1 rounded-lg bg-surface-2 p-3 text-xs">
              {m.facts.map((f, i) => (
                <li key={i}>
                  A PEACE encontrou: {f.kind === 'deadline' ? 'entrega' : 'avaliação'} «{f.title}», {f.timed ? when(f.at) : dayMonth(f.at)}. Vê em{' '}
                  <Link className="underline" to="/rever">
                    Rever
                  </Link>
                  .
                </li>
              ))}
            </ul>
          )}
          {full.data?.body ? (
            <p className="max-h-[50vh] overflow-y-auto whitespace-pre-wrap">{full.data.body}</p>
          ) : (
            <p className="text-muted">{full.data ? 'A PEACE só guarda o texto dos emails da universidade. Abre-o no webmail para o ler.' : 'A carregar…'}</p>
          )}
        </div>
      )}
    </Dialog>
  )
}

export default function Mail() {
  const now = useNow(60_000)
  const toast = useToast()
  const [view, setView] = useState<View>('academic')
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState<MailMessage | null>(null)
  const busy = useRef(false) // a double click arrives before the list re-renders
  const status = useApi(() => api.mailStatus(), [])
  const messages = useApi(() => api.mailMessages(view, search), [view, search])

  async function act(run: () => Promise<unknown>, done: string) {
    if (busy.current) return
    busy.current = true
    try {
      await run()
      toast(done, 'ok')
      status.reload()
      messages.reload()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      busy.current = false
    }
  }
  const senders = status.data?.keep_senders ?? []
  const keep = (sender: string) => act(() => api.saveKeepSenders([...senders, sender]), `Os emails de ${sender} nunca serão limpos`)
  const unkeep = (sender: string) => act(() => api.saveKeepSenders(senders.filter((s) => s !== sender)), 'Remetente retirado')
  function submit(e: FormEvent) {
    e.preventDefault()
    setSearch(query.trim())
  }

  if (status.error) return <ErrorNote error={status.error} onRetry={status.reload} />
  if (!status.data) return <Skeleton className="h-48" />
  const st = status.data
  if (!st.configured || !st.enabled)
    return (
      <div className="grid max-w-4xl gap-6">
        <PageHeader title="Correio" subtitle="O email da universidade, lido pela PEACE." />
        <Card>
          <Empty icon={<MailIcon className="size-5" />} title="O email ainda não está ligado">
            Escreve o endereço do webmail, o utilizador e a palavra-passe em{' '}
            <Link className="underline" to="/definicoes">
              Definições → Integrações
            </Link>
            .
          </Empty>
        </Card>
      </div>
    )

  return (
    <div className="grid max-w-4xl gap-6">
      <PageHeader title="Correio" subtitle="O email da universidade: o que é das cadeiras primeiro, e o que ocupa espaço." />
      <Usage st={st} now={now} onSync={() => act(() => api.syncMail(), 'Correio lido')} />
      <Card>
        <div className="grid gap-4">
          <form onSubmit={submit} role="search" className="flex gap-2">
            <Input aria-label="Procurar nos emails" placeholder="Procurar (ex.: exame AM2)" value={query} onChange={(e) => setQuery(e.target.value)} className="min-w-0 flex-1" />
            <Button type="submit">
              <Search className="size-3.5" /> Procurar
            </Button>
          </form>
          {search ? (
            <p className="text-xs text-muted">
              Resultados para «{search}» ·{' '}
              <button type="button" className="underline" onClick={() => (setSearch(''), setQuery(''))}>
                limpar
              </button>
            </p>
          ) : (
            <Segmented label="Que emails" value={view} onChange={setView} options={VIEWS} />
          )}
          {messages.error && <ErrorNote error={messages.error} onRetry={messages.reload} />}
          {!messages.data ? (
            <Skeleton className="h-40" />
          ) : messages.data.length ? (
            <ul className="grid divide-y divide-line">
              {messages.data.map((m) => (
                <MessageRow key={m.id} m={m} onOpen={() => setOpen(m)} onKeep={() => keep(m.sender)} onRestore={() => act(() => api.restoreMail(m.id), 'Email reposto na caixa de entrada')} />
              ))}
            </ul>
          ) : (
            <Empty title={search ? 'Nenhum email encontrado' : 'Nada aqui'} />
          )}
        </div>
      </Card>
      {senders.length > 0 && (
        <Card title="Nunca limpar">
          <ul className="grid gap-1">
            {senders.map((s) => (
              <li key={s} className="flex items-center justify-between gap-3 text-[13px]">
                <span className="truncate">{s}</span>
                <Button size="sm" variant="ghost" onClick={() => unkeep(s)}>
                  Retirar
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <MessageDialog m={open} onClose={() => setOpen(null)} />
    </div>
  )
}
