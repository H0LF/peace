import { useRef, useState } from 'react'
import { SOURCE_NAME } from '../components/sync'
import { Badge, Button, Card, Checkbox, Empty, ErrorNote, PageHeader, Skeleton, useToast } from '../components/ui'
import { api, type CleanupItem, type PlanSlot, type Proposal, type ProposedConcept, type RequirementDiff, type WorkGap } from '../lib/api'
import { ago, dayMonth, duration, megabytes, shortDay, time } from '../lib/format'
import { refreshAll, useApi, useNow } from '../lib/hooks'

const STATUS: Record<Proposal['status'], [string, 'ok' | 'neutral' | 'warn']> = {
  pending: ['por decidir', 'warn'],
  applied: ['aplicada', 'ok'],
  rejected: ['rejeitada', 'neutral'],
  superseded: ['substituída', 'neutral'],
  undone: ['desfeita', 'neutral'],
}

const when = (iso: string) => `${shortDay(iso)} ${dayMonth(iso)} · ${time(iso)}`
const slot = (s: PlanSlot) => `${when(s.starts_at)}–${time(s.ends_at)}`

function Slots({ title, rows, tone }: { title: string; rows: PlanSlot[]; tone: 'ok' | 'danger' }) {
  if (!rows.length) return null
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted">{title}</p>
      <ul className="grid gap-1">
        {rows.map((s, i) => (
          <li key={s.id ?? i} className="flex items-center gap-2 text-[13px]">
            <Badge tone={tone}>{tone === 'ok' ? '+' : '−'}</Badge>
            <span className="num">{slot(s)}</span>
            {s.title && <span className="min-w-0 truncate text-muted">{s.title}</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Gaps({ title, rows }: { title: string; rows?: WorkGap[] }) {
  if (!rows?.length) return null
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted">{title}</p>
      <ul className="grid gap-1">
        {rows.map((g) => (
          <li key={g.id} className="text-[13px]">
            «{g.title}» · <span className="num">{duration(g.minutes)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** A mailbox cleanup: every message listed with why; untick to keep it. */
function Messages({ rows, keep, onToggle }: { rows: CleanupItem[]; keep: Set<number>; onToggle: (id: number) => void }) {
  return (
    <ul className="grid max-h-80 gap-1 overflow-y-auto">
      {rows.map((m) => (
        <li key={m.ref} className="flex items-center gap-2 text-[13px]">
          <Checkbox checked={!keep.has(Number(m.ref))} onChange={() => onToggle(Number(m.ref))} label={`Limpar «${m.quote}»`} />
          <span className="min-w-0 flex-1 truncate">
            {m.quote || '(sem assunto)'} <span className="text-faint">· {m.sender} · {m.rule}</span>
          </span>
          <span className="num shrink-0 text-xs text-muted">{megabytes(m.size)}</span>
        </li>
      ))}
    </ul>
  )
}

/** A re-read brief: which requirements come, go or change wording. Unchanged ones keep their state. */
function Diff({ diff }: { diff: RequirementDiff }) {
  const block = (title: string, rows: string[], tone: 'ok' | 'danger' | 'warn', sign: string) =>
    rows.length > 0 && (
      <div>
        <p className="mb-1 text-xs font-medium text-muted">{title}</p>
        <ul className="grid gap-1">
          {rows.map((r, i) => (
            <li key={i} className="flex items-start gap-2 text-[13px]">
              <Badge tone={tone}>{sign}</Badge>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      </div>
    )
  return (
    <div className="grid gap-3">
      {block('Novos', diff.added.map((a) => a.text), 'ok', '+')}
      {block('Mudados', diff.changed.map((c) => `${c.before} → ${c.text}`), 'warn', '~')}
      {block('Já não estão no enunciado', diff.removed.map((r) => r.text), 'danger', '−')}
    </div>
  )
}

/** Proposed concepts: each one can be unticked; what it needs first is shown and stays "inferred". */
function Concepts({ rows, skip, onToggle }: { rows: ProposedConcept[]; skip: Set<number>; onToggle: (i: number) => void }) {
  return (
    <ul className="grid max-h-96 gap-2 overflow-y-auto">
      {rows.map((c, i) => (
        <li key={c.name} className="flex items-start gap-2 text-[13px]">
          <Checkbox checked={!skip.has(i)} onChange={() => onToggle(i)} label={`Ficar com «${c.name}»`} />
          <span className="min-w-0 flex-1">
            <span className="font-medium">{c.name}</span> <span className="text-muted">— {c.description}</span>
            <span className="block text-xs text-faint">
              Dos temas: {c.topics.slice(0, 4).join(', ')}
              {c.requires.length > 0 && ` · precisa de: ${c.requires.join(', ')}`}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function PendingCard({ p, onDecided }: { p: Proposal; onDecided: () => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [keep, setKeep] = useState<Set<number>>(new Set())
  const [skip, setSkip] = useState<Set<number>>(new Set())
  const sent = useRef(false) // a double click arrives before the disabled button re-renders
  const ev = p.evidence[0]
  const cleanup = p.kind === 'mail_cleanup'
  async function decide(action: 'apply' | 'reject') {
    if (sent.current) return
    sent.current = true
    setBusy(true)
    try {
      await (action === 'apply' && cleanup
        ? api.applyProposal(p.id, [...keep])
        : action === 'apply' && p.kind === 'concepts'
          ? api.applyConcepts(p.id, [...skip])
          : api.decideProposal(p.id, action))
      toast(action === 'reject' ? 'Sugestão rejeitada' : cleanup ? 'Emails movidos para Itens Eliminados' : p.kind === 'concepts' ? 'Conceitos guardados' : 'Plano atualizado', 'ok')
      onDecided()
    } catch (err) {
      toast((err as Error).message, 'error')
      sent.current = false
      setBusy(false)
    }
  }
  const preview = p.preview!
  return (
    <Card title={p.title}>
      <div className="grid gap-4">
        <div className="grid gap-1">
          <p className="text-[13px]">{p.reason}</p>
          {ev && !cleanup && (
            <p className="text-xs text-faint">
              Origem: {SOURCE_NAME[ev.source] ?? ev.source} · «{ev.quote}»
              {ev.before && ev.after && ` · antes ${when(ev.before)}, agora ${when(ev.after)}`}
            </p>
          )}
          {p.item?.course && <p className="text-xs text-faint">{p.item.course}</p>}
        </div>
        {preview.error ? (
          <p className="text-[13px] text-warn">Já não se aplica: {preview.error}</p>
        ) : preview.concepts ? (
          <Concepts rows={preview.concepts} skip={skip} onToggle={(i) => setSkip((k) => new Set(k.has(i) ? [...k].filter((x) => x !== i) : [...k, i]))} />
        ) : preview.diff ? (
          <Diff diff={preview.diff} />
        ) : preview.messages ? (
          <Messages rows={preview.messages} keep={keep} onToggle={(id) => setKeep((k) => new Set(k.has(id) ? [...k].filter((x) => x !== id) : [...k, id]))} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Slots title="Sai do plano" rows={preview.removed} tone="danger" />
            <Slots title="Entra no plano" rows={preview.created} tone="ok" />
            <Gaps title="Fica de fora do plano (o prazo não muda)" rows={preview.dropped} />
            <Gaps title="Continua sem tempo suficiente" rows={preview.short} />
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy || !!preview.error} onClick={() => decide('apply')}>
            Aplicar
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => decide('reject')}>
            Rejeitar
          </Button>
        </div>
      </div>
    </Card>
  )
}

export default function Review() {
  const now = useNow(60_000)
  const toast = useToast()
  const undoing = useRef(false)
  const [recovering, setRecovering] = useState(false)
  const pending = useApi(() => api.proposals(), [])
  const history = useApi(() => api.proposals(true), [])
  const reload = () => {
    pending.reload()
    history.reload()
    refreshAll()
  }
  async function recover() {
    if (recovering) return
    setRecovering(true)
    try {
      const { options, note } = await api.recover()
      if (options.length) toast(`${options.length === 1 ? '1 maneira' : `${options.length} maneiras`} de recuperar`, 'ok')
      else if (note) toast(`${note} Nenhuma mudança no plano ajuda.`, 'error')
      else toast('Nada para recuperar: o trabalho cabe no plano', 'ok')
      reload()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setRecovering(false)
    }
  }
  async function undo(p: Proposal) {
    if (undoing.current) return
    undoing.current = true
    try {
      await api.decideProposal(p.id, 'undo')
      toast('Plano reposto como estava', 'ok')
      reload()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      undoing.current = false
    }
  }

  return (
    <div className="grid max-w-4xl gap-6">
      <PageHeader
        title="Rever"
        subtitle="O que a PEACE sugere mudar depois de alterações nos sites ou de estudo por fazer. Nada muda sem a tua decisão."
        actions={
          <Button disabled={recovering} onClick={recover}>
            Recuperar estudo em falta
          </Button>
        }
      />
      {pending.error && <ErrorNote error={pending.error} onRetry={pending.reload} />}
      {!pending.data ? (
        <Skeleton className="h-48" />
      ) : pending.data.length ? (
        pending.data.map((p) => <PendingCard key={p.id} p={p} onDecided={reload} />)
      ) : (
        <Card>
          <Empty title="Nada para rever">Quando um prazo mudar ou ficar estudo por fazer, a sugestão aparece aqui.</Empty>
        </Card>
      )}
      <Card title="Histórico">
        {history.error && <ErrorNote error={history.error} onRetry={history.reload} />}
        {!history.data ? (
          <Skeleton className="h-24" />
        ) : history.data.length ? (
          <ul className="grid divide-y divide-line">
            {history.data.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="text-[13px]">{p.title}</p>
                  <p className="text-xs text-faint">{p.reason}</p>
                </div>
                <Badge tone={STATUS[p.status][1]}>{STATUS[p.status][0]}</Badge>
                {p.decided_at && <span className="text-[11px] text-faint">{ago(p.decided_at, now)}</span>}
                {p.status === 'applied' && (
                  <Button size="sm" variant="ghost" onClick={() => undo(p)}>
                    Desfazer
                  </Button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="Ainda não decidiste nenhuma sugestão" />
        )}
      </Card>
    </div>
  )
}
