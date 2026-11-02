import { ArrowLeft, Check, Plus, RotateCcw, ScanText, Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge, Button, Card, ErrorNote, Field, Input, PageHeader, Select, Skeleton, Textarea, useToast } from '../components/ui'
import { api, type ExamQuestionIn, type ExamQuestionRow, type PastExamDetail } from '../lib/api'
import { EXAM_STATE } from '../lib/format'
import { useApi } from '../lib/hooks'

type Act = (run: () => Promise<unknown>, done?: string) => Promise<void>
const BLANK: ExamQuestionIn = { number: '', weight: null, kind: 'other', text: '', printed: '', asks: '', page: null, concept_id: null }
const fields = (q: ExamQuestionRow): ExamQuestionIn => ({
  number: q.number,
  weight: q.weight,
  kind: q.kind,
  text: q.text,
  printed: q.printed,
  asks: q.asks,
  page: q.page,
  concept_id: q.concept_id,
})

function QuestionForm({ d, q, act, onDone }: { d: PastExamDetail; q: ExamQuestionRow | null; act: Act; onDone?: () => void }) {
  const [form, setForm] = useState<ExamQuestionIn>(q ? fields(q) : { ...BLANK, page: d.pages ? 0 : null })
  const dirty = !q || JSON.stringify(form) !== JSON.stringify(fields(q))
  const set = (k: keyof ExamQuestionIn) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    const v = e.target.value
    setForm((f) => ({ ...f, [k]: k === 'weight' || k === 'page' || k === 'concept_id' ? (v === '' ? null : Number(v)) : v }))
  }
  const save = () =>
    act(async () => {
      await (q ? api.editExamQuestion(q.id, form) : api.addExamQuestion(d.id, form))
      onDone?.()
    }, q ? 'Pergunta guardada' : 'Pergunta juntada')
  const label = q ? `Pergunta ${q.number || '?'}` : 'Nova pergunta'
  return (
    <Card title={label} action={q && <Badge tone={q.status === 'ok' ? 'ok' : 'warn'}>{q.status === 'ok' ? 'confirmada' : 'por confirmar'}</Badge>}>
      <div className="grid gap-3 text-[13px]">
        <div className="grid grid-cols-[5rem_6rem_minmax(0,1fr)] gap-2">
          <Field label="N.º">
            <Input aria-label={`${label}: número`} value={form.number} maxLength={10} onChange={set('number')} />
          </Field>
          <Field label="Peso (%)">
            <Input aria-label={`${label}: peso`} type="number" min={0} max={100} value={form.weight ?? ''} onChange={set('weight')} />
          </Field>
          <Field label="Tipo">
            <Select aria-label={`${label}: tipo`} value={form.kind} onChange={set('kind')}>
              {d.kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Enunciado">
          <Textarea aria-label={`${label}: enunciado`} value={form.text} maxLength={20000} onChange={set('text')} className="min-h-32" />
        </Field>
        <Field label="Impresso com a pergunta" hint="Código, tabelas, resultados de exemplo. O leitor às vezes troca ou inventa linhas: confere com a foto.">
          <Textarea aria-label={`${label}: impresso`} value={form.printed} maxLength={20000} onChange={set('printed')} className="font-mono text-xs" />
        </Field>
        <Field label="O que pede">
          <Input aria-label={`${label}: o que pede`} value={form.asks} maxLength={300} onChange={set('asks')} />
        </Field>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="Conceito">
            <Select aria-label={`${label}: conceito`} value={form.concept_id ?? ''} onChange={set('concept_id')}>
              <option value="">— nenhum —</option>
              {d.concepts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Figura (a foto)">
            <Select aria-label={`${label}: página`} value={form.page ?? ''} onChange={set('page')}>
              <option value="">— sem figura —</option>
              {d.page_names.map((n, i) => (
                <option key={i} value={i}>
                  Página {i + 1} · {n}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" disabled={!dirty || !form.text.trim()} onClick={save}>
            <Check className="size-3.5" /> {q ? 'Guardar' : 'Juntar'}
          </Button>
          {form.page !== null && (
            <a href={`#pagina-${form.page + 1}`} className="text-xs text-accent hover:underline">
              ver a página {form.page + 1}
            </a>
          )}
          {q && (
            <Button variant="ghost" className="ml-auto" aria-label={`Apagar ${label}`} onClick={() => act(() => api.deleteExamQuestion(q.id), 'Pergunta apagada')}>
              <Trash2 className="size-3.5" /> Apagar
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

export default function PastExamPage() {
  const id = Number(useParams().id)
  const toast = useToast()
  const navigate = useNavigate()
  const data = useApi(() => api.pastExam(id), [id])
  const busy = useRef(false)
  const [adding, setAdding] = useState(false)
  const [sending, setSending] = useState<string | null>(null)
  const reading = data.data?.state === 'reading'
  const reload = data.reload
  useEffect(() => {
    if (!reading) return
    const t = setInterval(reload, 3000) // about 10 s per half page
    return () => clearInterval(t)
  }, [reading, reload])

  const act: Act = async (run, done) => {
    if (busy.current) return
    busy.current = true
    try {
      await run()
      if (done) toast(done, 'ok')
      reload()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      busy.current = false
    }
  }
  async function addFiles(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    try {
      for (const [i, f] of files.entries()) {
        setSending(`A enviar ${i + 1} de ${files.length}…`)
        await api.addPastExamFile(id, f)
      }
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      setSending(null)
      reload()
    }
  }

  if (data.error) return <ErrorNote error={data.error} onRetry={reload} />
  if (!data.data) return <Skeleton className="h-96" />
  const d = data.data
  const [stateLabel, tone] = EXAM_STATE[d.state]
  return (
    <div className="grid gap-6">
      <div>
        <Link to={`/cadeiras/${d.course.id}`} className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-text">
          <ArrowLeft className="size-3.5" /> {d.course.name}
        </Link>
        <PageHeader
          title={d.title}
          subtitle={
            <span className="flex flex-wrap items-center gap-2">
              <Badge tone={tone}>{stateLabel}</Badge>
              {d.pages} {d.pages === 1 ? 'página' : 'páginas'} · {d.questions.length} {d.questions.length === 1 ? 'pergunta' : 'perguntas'}
            </span>
          }
          actions={
            <div className="flex flex-wrap gap-2">
              {d.state !== 'confirmed' && (
                <Button disabled={reading || !d.pages} onClick={() => act(() => api.readPastExam(id))}>
                  <ScanText className="size-3.5" /> {reading ? 'A ler…' : d.questions.length ? 'Ler outra vez' : 'Ler as fotos'}
                </Button>
              )}
              {d.state === 'review' && (
                <Button variant="primary" disabled={!d.questions.length} onClick={() => act(() => api.confirmPastExam(id), 'Exame confirmado: conta para o perfil da cadeira')}>
                  <Check className="size-3.5" /> Confirmar exame
                </Button>
              )}
              {d.state === 'confirmed' && (
                <Button onClick={() => act(() => api.reopenPastExam(id))}>
                  <RotateCcw className="size-3.5" /> Corrigir
                </Button>
              )}
              <Button
                variant="ghost"
                disabled={reading}
                onClick={() => {
                  if (window.confirm('Apagar este exame, as fotos e as perguntas?'))
                    void act(async () => {
                      await api.deletePastExam(id)
                      navigate(`/cadeiras/${d.course.id}`)
                    }, 'Exame apagado')
                }}
              >
                <Trash2 className="size-3.5" /> Apagar
              </Button>
            </div>
          }
        />
      </div>
      {d.error && <p className="text-[13px] text-danger">{d.error}</p>}
      {d.state === 'review' && (
        <p className="text-[13px] text-muted">
          Compara cada pergunta com a foto e corrige o que estiver mal. Só depois de «Confirmar exame» conta para o perfil da cadeira e para o treino.
        </p>
      )}
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="grid gap-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          {d.page_names.map((n, i) => (
            <figure key={i} id={`pagina-${i + 1}`} className="grid gap-1">
              <img src={api.pastExamPage(id, i)} alt={`Página ${i + 1} do exame (${n})`} className="w-full rounded-lg border border-line bg-white" loading="lazy" />
              <figcaption className="text-xs text-muted">
                Página {i + 1} · {n}
              </figcaption>
            </figure>
          ))}
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-accent hover:underline">
            <Upload className="size-3.5" /> {sending ?? 'Juntar mais fotos ou PDF'}
            <input type="file" multiple accept="image/jpeg,image/png,image/webp,image/heic,application/pdf" className="sr-only" disabled={reading || !!sending} onChange={addFiles} />
          </label>
        </div>
        <div className="grid gap-4">
          {reading && <Skeleton className="h-40" />}
          {!reading && d.questions.map((q) => <QuestionForm key={`${q.id}:${q.status}`} d={d} q={q} act={act} />)}
          {!reading &&
            (adding ? (
              <QuestionForm d={d} q={null} act={act} onDone={() => setAdding(false)} />
            ) : (
              <Button className="justify-self-start" onClick={() => setAdding(true)}>
                <Plus className="size-3.5" /> Juntar pergunta à mão
              </Button>
            ))}
        </div>
      </div>
    </div>
  )
}
