import { Link2, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api, type ConceptRow, type MasteryState } from '../lib/api'
import { useApi } from '../lib/hooks'
import { Badge, Button, Card, Empty, ErrorNote, Input, Select, Skeleton, useToast } from './ui'

const STATE: Record<MasteryState, [string, 'neutral' | 'danger' | 'warn' | 'ok']> = {
  untested: ['sem dados', 'neutral'],
  weak: ['fraco', 'danger'],
  developing: ['a crescer', 'warn'],
  mastered: ['dominado', 'ok'],
}
const SAY = ['1 · nada', '2 · pouco', '3 · mais ou menos', '4 · bem', '5 · muito bem']
const pct = (x: number | null) => (x === null ? '' : `${Math.round(x * 100)} %`)
type Act = (run: () => Promise<unknown>, done?: string, guard?: boolean) => Promise<void>

function ConceptItem({ c, all, act }: { c: ConceptRow; all: ConceptRow[]; act: Act }) {
  const m = c.mastery
  const [label, tone] = STATE[m.state]
  return (
    <li className="grid gap-1.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-[13px] font-medium">{c.name}</span>
        <Badge tone={tone}>
          {label}
          {m.mean !== null && ` · ${pct(m.mean)}`}
        </Badge>
        <Select
          aria-label={`O que achas que sabes: ${c.name}`}
          value={c.confidence ?? ''}
          className="w-40"
          onChange={(e) => act(() => api.updateConcept(c.id, e.target.value ? { confidence: Number(e.target.value) } : { clear_confidence: true }), undefined, false)}
        >
          <option value="">Achas que sabes?</option>
          {SAY.map((s, i) => (
            <option key={s} value={i + 1}>
              {s}
            </option>
          ))}
        </Select>
        <Button size="sm" variant="ghost" aria-label={`Apagar conceito ${c.name}`} onClick={() => act(() => api.deleteConcept(c.id), 'Conceito apagado')}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>
      {c.description && <p className="text-xs text-muted">{c.description}</p>}
      <p className="text-xs text-faint">
        {m.state === 'untested'
          ? 'Ainda sem respostas avaliadas. O domínio só conta respostas a perguntas, nunca o tempo nem a leitura.'
          : `Entre ${pct(m.low)} e ${pct(m.high)} (80 %), de ${m.answers} ${m.answers === 1 ? 'resposta' : 'respostas'}; as mais antigas contam menos.`}
        {c.files.length > 0 && ` Vem de: ${c.files.map((f) => f.file).join(', ')}.`}
      </p>
      {m.evidence.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted">Respostas que contam</summary>
          <ul className="mt-1 grid gap-0.5">
            {m.evidence.map((e, i) => (
              <li key={i}>
                {new Date(e.answered_at).toLocaleDateString('pt-PT')} · {Math.round(e.score * 100)} % ·{' '}
                {e.grade === 'verified' ? 'nota verificada' : e.confirmed ? 'nota estimada, confirmada' : 'nota estimada (conta metade)'} · peso {e.weight}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        {c.requires.map((r) => (
          <span key={r.link} className="inline-flex items-center gap-1 rounded-md bg-surface-2 px-2 py-0.5">
            precisa de «{r.name}»{!r.approved && <span className="text-faint">(inferido)</span>}
            {!r.approved && (
              <button type="button" className="text-accent hover:underline" onClick={() => act(() => api.linkConcepts(c.id, r.id), 'Dependência confirmada')}>
                confirmar
              </button>
            )}
            <button type="button" aria-label={`Retirar dependência de ${r.name}`} className="text-faint hover:text-danger" onClick={() => act(() => api.unlinkConcepts(r.link))}>
              ×
            </button>
          </span>
        ))}
        {all.length > 1 && (
          <Select
            aria-label={`Juntar dependência a ${c.name}`}
            value=""
            className="h-7 w-auto text-xs"
            onChange={(e) => e.target.value && act(() => api.linkConcepts(c.id, Number(e.target.value)), 'Dependência juntada', false)}
          >
            <option value="">+ precisa de…</option>
            {all
              .filter((o) => o.id !== c.id && !c.requires.some((r) => r.id === o.id))
              .map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
          </Select>
        )}
      </div>
    </li>
  )
}

/** A course's concepts: proposed by the model from the files' topics and kept by the student; Mastery from answers. */
export function ConceptsCard({ courseId }: { courseId: number }) {
  const toast = useToast()
  const data = useApi(() => api.concepts(courseId), [courseId])
  const busy = useRef(false) // a double click arrives before the button re-renders as disabled
  const [name, setName] = useState('')
  const reading = data.data?.proposing.state === 'reading'
  const reload = data.reload
  useEffect(() => {
    if (!reading) return
    const t = setInterval(reload, 3000) // the model reads in the background
    return () => clearInterval(t)
  }, [reading, reload])

  // A select change can't double-fire, so it skips the guard: dropping it would lose the student's choice silently.
  const act: Act = async (run, done, guard = true) => {
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
  function add(e: FormEvent) {
    e.preventDefault()
    if (name.trim()) void act(() => api.addConcept(courseId, { name: name.trim() }), 'Conceito juntado').then(() => setName(''))
  }

  if (data.error) return <ErrorNote error={data.error} onRetry={reload} />
  if (!data.data) return <Skeleton className="h-32" />
  const d = data.data
  return (
    <Card
      title="Conceitos e domínio"
      action={
        <Button size="sm" variant="ghost" disabled={reading || !d.has_topics || !d.model.available} onClick={() => act(() => api.proposeConcepts(courseId))}>
          <Sparkles className="size-3.5" /> {reading ? 'A propor…' : 'Propor conceitos'}
        </Button>
      }
    >
      <div className="grid gap-2 text-[13px]">
        {d.proposing.state === 'done' && d.proposing.proposal && (
          <p className="text-accent">
            Há conceitos propostos em{' '}
            <Link className="underline" to="/rever">
              Rever
            </Link>
            : escolhe os que ficam.
          </p>
        )}
        {d.proposing.state === 'done' && !d.proposing.proposal && <p className="text-muted">O modelo não encontrou conceitos novos.</p>}
        {d.proposing.state === 'error' && <p className="text-danger">{d.proposing.error}</p>}
        {!d.has_topics && <p className="text-muted">Os ficheiros desta cadeira ainda não foram lidos pelo modelo; escreve os conceitos à mão ou volta mais tarde.</p>}
        {d.concepts.length ? (
          <ul className="grid divide-y divide-line">
            {d.concepts.map((c) => (
              <ConceptItem key={c.id} c={c} all={d.concepts} act={act} />
            ))}
          </ul>
        ) : (
          <Empty icon={<Link2 className="size-5" />} title="Ainda sem conceitos">
            «Propor conceitos» lê os temas dos ficheiros; tu escolhes os que ficam.
          </Empty>
        )}
        <form onSubmit={add} className="flex gap-2">
          <Input aria-label="Novo conceito" placeholder="Ex.: Normalização" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} className="min-w-0 flex-1" />
          <Button type="submit">
            <Plus className="size-3.5" /> Juntar
          </Button>
        </form>
      </div>
    </Card>
  )
}
