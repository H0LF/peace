import { ArrowLeft, Check, ChevronRight, Clock, Flag, X } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router'
import { MODE_LABEL } from '../components/exams'
import { Badge, Button, Card, ErrorNote, Input, PageHeader, Progress, Skeleton, Textarea, cx, useToast } from '../components/ui'
import { api, type PracticeQuestion, type PracticeResponse, type PracticeSummary } from '../lib/api'
import { useApi } from '../lib/hooks'

const STATE_LABEL = { untested: 'sem dados', weak: 'fraco', developing: 'a crescer', mastered: 'dominado' } as const
const LANGUAGE = { python: 'Python', c: 'C', sql: 'SQL (SQLite)' } as const
const tenths = (score: number) => (Math.round(score * 100) / 10).toLocaleString('pt-PT')

/** A written answer's Estimated grade: the criteria the model marked, and the student's confirm or change. */
function Estimated({ q, onGrade }: { q: PracticeQuestion; onGrade: (score: number | null) => Promise<void> }) {
  const a = q.answered!
  const f = a.feedback
  const [mine, setMine] = useState('')
  if (f?.state === 'grading') return <p className="rounded-lg bg-surface-2 p-3 text-[13px] text-muted">A corrigir com o modelo local…</p>
  const own = Number(mine.replace(',', '.'))
  const valid = mine.trim() !== '' && Number.isFinite(own) && own >= 0 && own <= 10
  return (
    <div className="grid gap-2 rounded-lg bg-surface-2 p-3 text-[13px]">
      {f?.state === 'waiting' && (
        <p className="text-muted">O modelo local não está disponível: a resposta fica guardada e é corrigida quando voltar. Também podes dar tu a nota.</p>
      )}
      {f?.state === 'error' && <p className="text-muted">O modelo não conseguiu corrigir esta resposta: dá tu a nota.</p>}
      {a.score !== null && (
        <p className="flex flex-wrap items-center gap-2 font-semibold">
          {tenths(a.score)} em 10
          <Badge tone={f?.confirmed ? 'ok' : 'warn'}>{f?.confirmed ? 'nota confirmada por ti' : 'nota estimada pelo modelo'}</Badge>
        </p>
      )}
      {f?.criteria && (
        <ul className="grid gap-1">
          {f.criteria.map((c, i) => (
            <li key={i} className="flex gap-2">
              <span className="num w-14 shrink-0 text-muted">
                {c.points.toLocaleString('pt-PT')}/{c.max.toLocaleString('pt-PT')}
              </span>
              <span className="min-w-0">
                {c.criterion}
                {c.comment && <span className="text-muted"> · {c.comment}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {f?.feedback && <p>{f.feedback}</p>}
      {!f?.confirmed && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) void onGrade(own / 10)
          }}
        >
          {a.score !== null && (
            <Button type="button" size="sm" onClick={() => void onGrade(null)}>
              <Check className="size-3.5" /> Concordo
            </Button>
          )}
          <Input
            aria-label="A tua nota (0 a 10)"
            inputMode="decimal"
            placeholder="Nota 0–10"
            value={mine}
            onChange={(e) => setMine(e.target.value)}
            className="w-28"
          />
          <Button type="submit" size="sm" variant="ghost" disabled={!valid}>
            {a.score === null ? 'Dar nota' : 'Corrigir nota'}
          </Button>
        </form>
      )}
    </div>
  )
}

function Feedback({ q, onGrade }: { q: PracticeQuestion; onGrade: (score: number | null) => Promise<void> }) {
  const a = q.answered!
  if (q.qtype === 'written') return <Estimated q={q} onGrade={onGrade} />
  const right = a.score === 1
  return (
    <div className={cx('grid gap-2 rounded-lg p-3 text-[13px]', right ? 'bg-ok-soft' : 'bg-danger-soft')}>
      <p className="font-semibold">
        {q.qtype === 'code'
          ? right
            ? 'Passou em todos os testes.'
            : a.feedback?.status === 'error'
              ? 'Não correu até ao fim (tempo ou limites esgotados).'
              : 'Não passou nos testes.'
          : right
            ? 'Certo.'
            : q.qtype === 'numeric'
              ? `Errado: a resposta é ${a.answer?.value}.`
              : 'Errado.'}
      </p>
      {q.qtype === 'code' && a.feedback?.output && (
        <pre className="max-h-48 overflow-auto rounded bg-surface p-2 text-xs whitespace-pre-wrap">{a.feedback.output}</pre>
      )}
      {q.qtype === 'code' && a.answer?.solution && (
        <details>
          <summary className="cursor-pointer text-xs">Solução de referência</summary>
          <pre className="mt-1 overflow-x-auto rounded bg-surface p-2 text-xs">{a.answer.solution}</pre>
        </details>
      )}
      {a.explanation && <p>{a.explanation}</p>}
    </div>
  )
}

function Question({
  q,
  n,
  total,
  timed,
  onAnswer,
  onGrade,
}: {
  q: PracticeQuestion
  n: number
  total: number
  timed: boolean
  onAnswer: (a: PracticeResponse) => Promise<void>
  onGrade: (score: number | null) => Promise<void>
}) {
  const a = q.answered
  const saved = a?.response
  const [choice, setChoice] = useState<number | null>(saved?.choice ?? null)
  const [value, setValue] = useState(saved?.value !== undefined ? String(saved.value) : '')
  const [text, setText] = useState(saved?.code ?? saved?.text ?? '')
  const locked = !!a && !timed // practice: one answer; timed: change it until the end
  const graded = !!a && !timed
  const num = Number(value.replace(',', '.'))
  const ready = q.qtype === 'mcq' ? choice !== null : q.qtype === 'numeric' ? value.trim() !== '' && Number.isFinite(num) : text.trim() !== ''
  function submit(e: FormEvent) {
    e.preventDefault()
    if (locked || !ready) return
    if (q.qtype === 'mcq') void onAnswer({ choice: choice! })
    else if (q.qtype === 'numeric') void onAnswer({ value: num })
    else void onAnswer(q.qtype === 'code' ? { code: text } : { text })
  }
  return (
    <Card
      title={`Pergunta ${n} de ${total}`}
      action={
        <span className="flex flex-wrap gap-1.5">
          {q.concept && <Badge>{q.concept}</Badge>}
          {q.language && <Badge tone="accent">{LANGUAGE[q.language]}</Badge>}
          <Badge tone="accent">{q.kind_label}</Badge>
        </span>
      }
    >
      <form onSubmit={submit} className="grid gap-4 text-[14px]">
        <p className="leading-relaxed whitespace-pre-line">{q.text}</p>
        {q.printed && <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-xs">{q.printed}</pre>}
        {q.figure && (
          <details>
            <summary className="cursor-pointer text-xs text-muted">Ver a folha do exame (figuras e tabelas)</summary>
            <img
              src={api.pastExamPage(q.figure.exam_id, q.figure.page)}
              alt="Folha do exame com esta pergunta"
              className="mt-2 w-full rounded-lg border border-line bg-white"
              loading="lazy"
            />
          </details>
        )}
        {q.qtype === 'mcq' && q.options && (
          <fieldset className="grid gap-2" disabled={locked}>
            <legend className="sr-only">Opções</legend>
            {q.options.map((o, i) => {
              const picked = locked ? a.response.choice === i : choice === i
              const key = graded && a.answer?.choice === i
              return (
                <label
                  key={i}
                  className={cx(
                    'flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2',
                    key
                      ? 'border-ok bg-ok-soft'
                      : picked && graded
                        ? 'border-danger bg-danger-soft'
                        : picked
                          ? 'border-accent bg-accent-soft'
                          : 'border-line hover:bg-surface-2',
                  )}
                >
                  <input type="radio" name={`q${q.id}`} className="mt-1" checked={picked} onChange={() => setChoice(i)} />
                  <span className="min-w-0 flex-1">{o}</span>
                  {key && <Check className="size-4 text-ok" aria-label="resposta certa" />}
                  {picked && graded && !key && <X className="size-4 text-danger" aria-label="a tua resposta" />}
                </label>
              )
            })}
          </fieldset>
        )}
        {q.qtype === 'numeric' && (
          <Input
            aria-label="A tua resposta (um número)"
            inputMode="decimal"
            placeholder="Escreve um número"
            value={locked ? String(a.response.value) : value}
            disabled={locked}
            onChange={(e) => setValue(e.target.value)}
            className="max-w-48"
          />
        )}
        {(q.qtype === 'code' || q.qtype === 'written') && (
          <Textarea
            aria-label={q.qtype === 'code' ? 'O teu código' : 'A tua resposta'}
            placeholder={q.qtype === 'code' ? (q.language === 'sql' ? 'Uma consulta SELECT' : 'O teu código') : 'Escreve a tua resposta'}
            value={text}
            readOnly={locked}
            spellCheck={q.qtype === 'written'}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (q.qtype !== 'code' || e.key !== 'Tab' || e.shiftKey || locked) return
              e.preventDefault() // a tab indents code; Shift+Tab still leaves the field
              const t = e.currentTarget
              const at = t.selectionStart
              setText(text.slice(0, at) + '    ' + text.slice(t.selectionEnd))
              requestAnimationFrame(() => t.setSelectionRange(at + 4, at + 4))
            }}
            className={cx(q.qtype === 'code' ? 'min-h-48 font-mono text-xs' : 'min-h-36')}
          />
        )}
        {!graded && (
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary" disabled={!ready}>
              {timed && a ? 'Guardar alteração' : timed ? 'Guardar resposta' : q.qtype === 'code' ? 'Correr os testes' : 'Responder'}
            </Button>
            {timed && a && <span className="text-xs text-muted">Resposta guardada; vês a nota no fim.</span>}
          </div>
        )}
      </form>
      {graded && (
        <div className="mt-4">
          <Feedback q={q} onGrade={onGrade} />
        </div>
      )}
    </Card>
  )
}

function Summary({ s, courseId }: { s: PracticeSummary; courseId: number }) {
  return (
    <Card title="Resultado">
      <div className="grid gap-4 text-[13px]">
        <p className="text-lg font-semibold">
          {s.right.toLocaleString('pt-PT')} de {s.answered}
          {s.answered + s.grading < s.questions && (
            <span className="text-sm font-normal text-muted"> ({s.questions - s.answered - s.grading} sem resposta)</span>
          )}
        </p>
        {s.grading > 0 && (
          <p className="text-muted">
            {s.grading} {s.grading === 1 ? 'resposta escrita ainda a ser corrigida' : 'respostas escritas ainda a ser corrigidas'}: vê-as em cada pergunta.
          </p>
        )}
        {s.concepts.length > 0 && (
          <ul className="grid divide-y divide-line">
            {s.concepts.map((c) => (
              <li key={c.id} className="grid gap-1 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 font-medium">{c.name}</span>
                  <span className="num text-muted">
                    {c.right.toLocaleString('pt-PT')}/{c.answered}
                  </span>
                  <Badge tone={c.state === 'mastered' ? 'ok' : c.state === 'weak' ? 'danger' : c.state === 'developing' ? 'warn' : 'neutral'}>
                    {STATE_LABEL[c.state]}
                  </Badge>
                </div>
                {c.revisit.length > 0 && <p className="text-xs text-muted">Rever: {c.revisit.map((f) => f.file).join(', ')}</p>}
              </li>
            ))}
          </ul>
        )}
        <Link to={`/cadeiras/${courseId}`} className="text-accent hover:underline">
          Voltar à cadeira
        </Link>
      </div>
    </Card>
  )
}

/** Time left on the server's clock, counted down here; at zero the attempt is reloaded (the server grades it). */
function Countdown({ seconds, onEnd }: { seconds: number; onEnd: () => void }) {
  const [left, setLeft] = useState(seconds)
  useEffect(() => {
    const end = Date.now() + seconds * 1000
    setLeft(seconds)
    const t = setInterval(() => {
      const s = Math.max(0, Math.round((end - Date.now()) / 1000))
      setLeft(s)
      if (s === 0) {
        clearInterval(t)
        setTimeout(onEnd, 32_000) // past the server's grace period, so it has graded the attempt
      }
    }, 1000)
    return () => clearInterval(t)
  }, [seconds, onEnd])
  return (
    <span
      role="timer"
      aria-label="Tempo que falta"
      className={cx('num inline-flex items-center gap-1 font-semibold', left < 300 ? 'text-danger' : 'text-text')}
    >
      <Clock className="size-3.5" /> {left === 0 ? 'Tempo esgotado' : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`}
    </span>
  )
}

export default function PracticePage() {
  const id = Number(useParams().id)
  const toast = useToast()
  const data = useApi(() => api.practice(id), [id])
  const [at, setAt] = useState<number | null>(null)
  const [summary, setSummary] = useState<PracticeSummary | null>(null)
  const busy = useRef(false)
  const reload = data.reload
  const grading = !!data.data?.questions.some((q) => q.answered?.feedback?.state === 'grading')
  useEffect(() => {
    if (!grading) return
    const t = setInterval(reload, 3000) // the model marks written answers in the background
    return () => clearInterval(t)
  }, [grading, reload])

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    if (busy.current) return
    busy.current = true
    try {
      return await fn()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      busy.current = false
    }
  }

  if (data.error) return <ErrorNote error={data.error} onRetry={reload} />
  if (!data.data) return <Skeleton className="h-96" />
  const d = data.data
  const qs = d.questions
  const timed = d.mode === 'timed' && !d.finished
  const firstOpen = qs.findIndex((q) => !q.answered)
  const index = Math.min(at ?? (firstOpen === -1 ? qs.length - 1 : firstOpen), qs.length - 1)
  const q = qs[index]
  const answered = qs.filter((x) => x.answered).length
  const finish = async () => {
    const s = await run(() => api.finishPractice(id))
    if (s) setSummary(s)
    reload()
  }
  return (
    <div className="mx-auto grid w-full max-w-3xl gap-6">
      <div>
        <Link to={`/cadeiras/${d.course.id}`} className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-text">
          <ArrowLeft className="size-3.5" /> {d.course.name}
        </Link>
        <PageHeader
          title={MODE_LABEL[d.mode]}
          subtitle={`${answered} de ${qs.length} respondidas · cada resposta corrigida conta para o domínio dos conceitos`}
          actions={timed && d.seconds_left !== null ? <Countdown seconds={d.seconds_left} onEnd={reload} /> : undefined}
        />
        <div className="mt-3">
          <Progress value={qs.length ? Math.round((answered / qs.length) * 100) : 0} label="Perguntas respondidas" />
        </div>
      </div>
      {summary && <Summary s={summary} courseId={d.course.id} />}
      {d.finished && !summary && (
        <Button variant="primary" className="justify-self-start" onClick={finish}>
          <Flag className="size-3.5" /> Ver resultado
        </Button>
      )}
      {q && (
        <>
          {(timed || d.finished) && qs.length > 1 && (
            <nav aria-label="Perguntas" className="flex flex-wrap gap-1.5">
              {qs.map((x, i) => (
                <button
                  key={x.id}
                  type="button"
                  aria-label={`Pergunta ${i + 1}${x.answered ? ', respondida' : ''}`}
                  aria-current={i === index ? 'step' : undefined}
                  onClick={() => setAt(i)}
                  className={cx(
                    'num size-8 rounded-md border text-xs',
                    i === index ? 'border-accent bg-accent-soft' : x.answered ? 'border-line bg-surface-2' : 'border-line',
                  )}
                >
                  {i + 1}
                </button>
              ))}
            </nav>
          )}
          <Question
            key={`${q.id}:${timed}`}
            q={q}
            n={index + 1}
            total={qs.length}
            timed={timed}
            onGrade={async (score) => {
              await run(() => api.gradePractice(id, q.id, score))
              reload()
            }}
            onAnswer={async (a) => {
              setAt(index) // stay on this question to read the feedback
              const r = await run(() => api.answerPractice(id, { question_id: q.id, ...a }))
              if (r && timed) toast('Resposta guardada.', 'ok')
              reload() // also after a refusal: the time may have run out and the attempt is now graded
            }}
          />
          {!d.finished && (
            <div className="flex flex-wrap gap-2">
              {(q.answered || timed) && index < qs.length - 1 && (
                <Button variant="primary" onClick={() => setAt(index + 1)}>
                  Seguinte <ChevronRight className="size-3.5" />
                </Button>
              )}
              {answered === qs.length && !timed ? (
                <Button variant="primary" onClick={finish}>
                  <Flag className="size-3.5" /> Ver resultado
                </Button>
              ) : (
                <Button variant="ghost" onClick={finish}>
                  <Flag className="size-3.5" /> {timed ? 'Entregar agora' : 'Terminar agora'}
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
