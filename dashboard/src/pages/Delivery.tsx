import { CheckCircle2, FileText, GitBranch, Play, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router'
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, PageHeader, Select, Skeleton, useToast } from '../components/ui'
import { api, type Delivery, type ProjectCheck, type Requirement, type RequirementKind, type RequirementState } from '../lib/api'
import { countdown } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

const KIND: Record<RequirementKind, string> = { functional: 'funcional', constraint: 'regra', deliverable: 'a entregar', grading: 'avaliação' }
const STATE: Record<RequirementState, string> = { unverified: 'Por verificar', verified: 'Feito', missing: 'Em falta' }
const CHECK_TONE = { passed: 'ok', failed: 'danger', error: 'warn' } as const
const CHECK_LABEL = { passed: 'passou', failed: 'falhou', error: 'erro' } as const
type Act = (run: () => Promise<unknown>, done?: string, guard?: boolean) => Promise<void>

/** One button action at a time: a double click arrives before the button re-renders as disabled.
 *  A select change can't double-fire, so it passes guard=false: dropping it would lose the choice silently. */
function useAct(reload: () => void): Act {
  const toast = useToast()
  const busy = useRef(false)
  return async (run, done, guard = true) => {
    if (guard && busy.current) return
    if (guard) busy.current = true
    try {
      await run()
      if (done) toast(done, 'ok')
      reload()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      if (guard) busy.current = false
    }
  }
}

function Brief({ d, act }: { d: Delivery; act: Act }) {
  const reading = d.reading.state === 'reading'
  const chosen = d.brief?.id ?? ''
  return (
    <Card title="Enunciado">
      <div className="grid gap-3 text-[13px]">
        <Field label="Ficheiro do enunciado" hint={d.suggested && !d.brief ? 'Sugerido pela PEACE: confirma ou escolhe outro.' : undefined}>
          <Select value={chosen || (d.suggested ?? '')} onChange={(e) => act(() => api.setBrief(d.task.id, e.target.value ? Number(e.target.value) : null), 'Enunciado escolhido', false)}>
            <option value="">— nenhum —</option>
            {d.options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.filename}
                {o.id === d.suggested ? ' (sugerido)' : ''}
              </option>
            ))}
          </Select>
        </Field>
        {d.brief_changed && <p className="text-warn">O ficheiro do enunciado mudou desde a última leitura. Lê-o outra vez: as diferenças aparecem em Rever.</p>}
        {d.reading.state === 'error' && <p className="text-danger">{d.reading.error}</p>}
        {!d.model.available && (
          <p className="text-muted">O modelo local {d.model.paused ? `está em pausa (${d.model.paused})` : 'não está disponível'}; podes escrever os requisitos à mão.</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            disabled={reading || !d.model.available || !(chosen || d.suggested)}
            onClick={() =>
              act(async () => {
                if (!chosen && d.suggested) await api.setBrief(d.task.id, d.suggested)
                await api.readRequirements(d.task.id)
              })
            }
          >
            <FileText className="size-3.5" /> {reading ? 'A ler o enunciado…' : d.requirements.length ? 'Ler outra vez' : 'Ler requisitos'}
          </Button>
          {reading && <span className="text-xs text-muted">Pode demorar um minuto num enunciado longo.</span>}
        </div>
      </div>
    </Card>
  )
}

function RequirementRow({ r, act }: { r: Requirement; act: Act }) {
  const [text, setText] = useState(r.text)
  return (
    <li className="grid gap-1.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label={`Estado: ${r.text}`} value={r.state} className="w-36" onChange={(e) => act(() => api.updateRequirement(r.id, { state: e.target.value as RequirementState }), undefined, false)}>
          {Object.entries(STATE).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </Select>
        <Badge>{KIND[r.kind]}</Badge>
        <Input
          aria-label="Requisito"
          value={text}
          maxLength={500}
          className="min-w-0 flex-1"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => text.trim() && text.trim() !== r.text && act(() => api.updateRequirement(r.id, { text: text.trim() }), 'Requisito guardado')}
        />
        <Button size="sm" variant="ghost" aria-label={`Apagar: ${r.text}`} onClick={() => act(() => api.deleteRequirement(r.id), 'Requisito apagado')}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>
      <p className="text-xs text-faint" title={r.evidence?.quote}>
        {r.evidence
          ? `${r.evidence.file}${r.evidence.page ? ` · p. ${r.evidence.page}` : ''} · «${r.evidence.quote.slice(0, 140)}${r.evidence.quote.length > 140 ? '…' : ''}»`
          : 'Escrito por ti'}
        {r.verified_by?.startsWith('check:') && ' · verificado por uma verificação'}
      </p>
    </li>
  )
}

function Requirements({ d, act }: { d: Delivery; act: Act }) {
  const [kind, setKind] = useState<RequirementKind>('functional')
  const [text, setText] = useState('')
  function add(e: FormEvent) {
    e.preventDefault()
    if (!text.trim()) return
    void act(() => api.addRequirement(d.task.id, { kind, text: text.trim() }), 'Requisito juntado').then(() => setText(''))
  }
  return (
    <Card title="Requisitos">
      {d.requirements.length ? (
        <ul className="grid divide-y divide-line">
          {d.requirements.map((r) => (
            <RequirementRow key={`${r.id}:${r.text}`} r={r} act={act} />
          ))}
        </ul>
      ) : (
        <Empty title="Ainda sem requisitos">Lê o enunciado ou escreve-os aqui.</Empty>
      )}
      <form onSubmit={add} className="mt-3 flex flex-wrap gap-2">
        <Select aria-label="Tipo de requisito" value={kind} className="w-36" onChange={(e) => setKind(e.target.value as RequirementKind)}>
          {Object.entries(KIND).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </Select>
        <Input aria-label="Novo requisito" placeholder="Ex.: Pôr os nomes no relatório" value={text} maxLength={500} onChange={(e) => setText(e.target.value)} className="min-w-0 flex-1" />
        <Button type="submit">
          <Plus className="size-3.5" /> Juntar
        </Button>
      </form>
    </Card>
  )
}

function CheckRow({ c, d, act }: { c: ProjectCheck; d: Delivery; act: Act }) {
  const linked = d.requirements.find((r) => r.id === c.requirement_id)
  return (
    <li className="grid gap-1.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium">{c.name}</span>
        {c.last_status && <Badge tone={CHECK_TONE[c.last_status]}>{CHECK_LABEL[c.last_status]}</Badge>}
        <span className="flex-1" />
        <Button size="sm" disabled={!d.project || !d.sandbox} onClick={() => act(() => api.runCheck(c.id))}>
          <Play className="size-3.5" /> Correr
        </Button>
        <Button size="sm" variant="ghost" aria-label={`Apagar verificação ${c.name}`} onClick={() => act(() => api.deleteCheck(c.id), 'Verificação apagada')}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>
      <code className="block truncate rounded bg-surface-2 px-2 py-1 text-xs">{c.command}</code>
      {linked && <p className="text-xs text-faint">Se passar, marca como feito: «{linked.text}»</p>}
      {c.last_output && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">Resultado</summary>
          <pre className="mt-1 max-h-60 overflow-auto rounded bg-surface-2 p-2 whitespace-pre-wrap">{c.last_output}</pre>
        </details>
      )}
    </li>
  )
}

function Project({ d, act }: { d: Delivery; act: Act }) {
  const [path, setPath] = useState(d.project ?? '')
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [req, setReq] = useState('')
  const git = d.readiness.git
  function addCheck(e: FormEvent) {
    e.preventDefault()
    if (!name.trim() || !command.trim()) return
    void act(() => api.addCheck(d.task.id, { name: name.trim(), command: command.trim(), requirement_id: req ? Number(req) : null }), 'Verificação criada').then(() => {
      setName('')
      setCommand('')
      setReq('')
    })
  }
  return (
    <Card title="Projeto e verificações">
      <div className="grid gap-4 text-[13px]">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void act(() => api.setProject(d.task.id, path.trim() || null), 'Pasta guardada')
          }}
        >
          <Field label="Pasta do projeto" hint="A PEACE só a lê: nunca muda nada lá dentro." className="min-w-0 flex-1">
            <Input value={path} placeholder="~/projetos/trabalho" maxLength={1000} onChange={(e) => setPath(e.target.value)} />
          </Field>
          <Button type="submit">Guardar</Button>
        </form>
        {git && !git.error && (
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <GitBranch className="size-3.5" /> {git.branch} · {git.changed ? `${git.changed} ficheiros por guardar no git` : 'tudo guardado no git'}
            {git.commits?.[0] && ` · último: ${git.commits[0]}`}
          </p>
        )}
        {!d.sandbox && <p className="text-warn">O bubblewrap não está instalado: as verificações não podem correr.</p>}
        <p className="text-xs text-faint">
          As verificações correm numa caixa fechada: sem internet, sem os teus outros ficheiros, e o que mudarem desaparece no fim. Só correm comandos escritos por ti.
        </p>
        {d.checks.length > 0 && (
          <ul className="grid divide-y divide-line">
            {d.checks.map((c) => (
              <CheckRow key={c.id} c={c} d={d} act={act} />
            ))}
          </ul>
        )}
        <form onSubmit={addCheck} className="grid gap-2 sm:grid-cols-2">
          <Input aria-label="Nome da verificação" placeholder="Nome (ex.: Compila)" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          <Input aria-label="Comando" placeholder="Comando (ex.: javac *.java)" value={command} maxLength={2000} onChange={(e) => setCommand(e.target.value)} />
          <Select aria-label="Requisito que verifica" value={req} onChange={(e) => setReq(e.target.value)}>
            <option value="">— não marca nenhum requisito —</option>
            {d.requirements.map((r) => (
              <option key={r.id} value={r.id}>
                {r.text}
              </option>
            ))}
          </Select>
          <Button type="submit">
            <Plus className="size-3.5" /> Juntar verificação
          </Button>
        </form>
      </div>
    </Card>
  )
}

export default function DeliveryPage() {
  const id = Number(useParams().id)
  const now = useNow(60_000)
  const data = useApi(() => api.delivery(id), [id])
  const act = useAct(data.reload)
  const reading = data.data?.reading.state === 'reading'
  const reload = data.reload
  useEffect(() => {
    if (!reading) return
    const t = setInterval(reload, 3000) // the brief is read in the background
    return () => clearInterval(t)
  }, [reading, reload])

  if (data.error) return <ErrorNote error={data.error} onRetry={data.reload} />
  if (!data.data) return <Skeleton className="h-64" />
  const d = data.data
  const r = d.readiness
  return (
    <div className="grid max-w-4xl gap-6">
      <PageHeader
        title={d.task.title}
        subtitle={
          <>
            {d.task.course ?? 'Sem cadeira'}
            {d.task.due_at && ` · entrega ${countdown(d.task.due_at, now)}`} ·{' '}
            <Link className="underline" to="/tarefas">
              Tarefas
            </Link>
          </>
        }
      />
      <Card
        title="Pronto para entregar?"
        action={
          <Button size="sm" variant="ghost" onClick={() => act(() => api.exportReadiness(d.task.id), 'Relatório guardado: aparece no Obsidian em Entregas')}>
            Guardar no Obsidian
          </Button>
        }
      >
        <div className="grid gap-2 text-[13px]">
          <p className="flex items-center gap-2">
            {r.ready ? (
              <Badge tone="ok">
                <CheckCircle2 className="size-3" /> pronto
              </Badge>
            ) : (
              <Badge tone="warn">ainda não</Badge>
            )}
            <span className="num text-muted">
              {r.counts.verified} feitos · {r.counts.missing} em falta · {r.counts.unverified} por verificar
            </span>
          </p>
          <p>{r.summary}</p>
          <p className="text-xs text-faint">A PEACE nunca entrega trabalhos: a entrega é tua, no Moodle.</p>
        </div>
      </Card>
      <Brief d={d} act={act} />
      <Requirements d={d} act={act} />
      <Project key={d.project ?? ''} d={d} act={act} />
    </div>
  )
}
