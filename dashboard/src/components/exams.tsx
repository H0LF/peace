import { Camera, FileQuestion, Play, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, type PracticeMode } from '../lib/api'
import { EXAM_STATE } from '../lib/format'
import { useApi } from '../lib/hooks'
import { Badge, Button, Card, Empty, ErrorNote, Input, Segmented, Skeleton, useToast } from './ui'

/** Photos or PDFs of past exams: read by the image model, checked by the student, summed into the Exam profile. */
export function PastExamsCard({ courseId }: { courseId: number }) {
  const toast = useToast()
  const navigate = useNavigate()
  const data = useApi(() => api.pastExams(courseId), [courseId])
  const [title, setTitle] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState<string | null>(null)

  async function send(e: FormEvent) {
    e.preventDefault()
    if (sending || !files.length) return
    let id: number | null = null
    try {
      setSending('A criar…')
      id = (await api.createPastExam(courseId, title.trim() || `Exame ${new Date().toLocaleDateString('pt-PT')}`)).id
      for (const [i, f] of files.entries()) {
        setSending(`A enviar ${i + 1} de ${files.length}…`)
        await api.addPastExamFile(id, f)
      }
      if (data.data?.reader.available) await api.readPastExam(id)
      navigate(`/exames/${id}`)
    } catch (err) {
      toast((err as Error).message, 'error')
      if (id !== null) navigate(`/exames/${id}`) // what was sent is kept; the rest can be added there
    } finally {
      setSending(null)
    }
  }

  if (data.error) return <ErrorNote error={data.error} onRetry={data.reload} />
  if (!data.data) return <Skeleton className="h-32" />
  const { exams, profile, reader } = data.data
  return (
    <Card title="Exames antigos">
      <div className="grid gap-3 text-[13px]">
        {profile.exams > 0 && (
          <div>
            <p className="mb-1 text-xs text-muted">
              Como são os exames ({profile.exams} {profile.exams === 1 ? 'exame confirmado' : 'exames confirmados'}, peso médio):
            </p>
            <ul className="grid gap-1">
              {profile.kinds.map((k) => (
                <li key={k.kind} className="flex items-center gap-2">
                  <span className="min-w-0 flex-1">{k.label}</span>
                  <span className="num text-muted">
                    {k.weight} % · em {k.exams} de {profile.exams}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {exams.length ? (
          <ul className="grid divide-y divide-line">
            {exams.map((e) => (
              <li key={e.id} className="flex items-center gap-2 py-2">
                <Link to={`/exames/${e.id}`} className="min-w-0 flex-1 truncate font-medium hover:underline">
                  {e.title}
                </Link>
                <span className="num text-xs text-muted">
                  {e.pages} p. · {e.questions} perg.
                </span>
                <Badge tone={EXAM_STATE[e.state][1]}>{EXAM_STATE[e.state][0]}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <Empty icon={<Camera className="size-5" />} title="Ainda sem exames antigos">
            Envia fotos ou o PDF de um exame: a PEACE fica a saber como são e faz perguntas parecidas.
          </Empty>
        )}
        <form onSubmit={send} className="grid gap-2">
          <Input
            aria-label="Nome do exame"
            placeholder="Ex.: Exame época normal 2024/25"
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="file"
              multiple
              accept="image/jpeg,image/png,image/webp,image/heic,application/pdf"
              aria-label="Fotos ou PDF do exame"
              className="min-w-0 flex-1 text-xs"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
            <Button type="submit" variant="primary" disabled={!files.length || !!sending}>
              <Camera className="size-3.5" /> {sending ?? 'Enviar e ler'}
            </Button>
          </div>
          <p className="text-xs text-faint">
            Fotos de todas as páginas, pela ordem. O que os alunos escreveram à mão é ignorado.
            {!reader.available && ` O leitor de imagens está em pausa (${reader.paused}); as fotos ficam guardadas e lês depois.`}
          </p>
        </form>
      </div>
    </Card>
  )
}

export const MODE_LABEL: Record<PracticeMode, string> = {
  practice: 'Treino',
  timed: 'Exame cronometrado',
  weakness: 'Pontos fracos',
  retake: 'Repetir erradas',
}
const MODE_HINT: Record<PracticeMode, string> = {
  practice: 'Vês se acertaste a cada pergunta.',
  timed: 'Com tempo contado pela PEACE; só vês as notas no fim. Podes mudar respostas até lá.',
  weakness: 'Só perguntas dos conceitos fracos ou a crescer.',
  retake: 'Só as perguntas que erraste da última vez.',
}

/** The course's question bank and practice. */
export function PracticeCard({ courseId }: { courseId: number }) {
  const toast = useToast()
  const navigate = useNavigate()
  const data = useApi(() => api.questionBank(courseId), [courseId])
  const busy = useRef(false)
  const [mode, setMode] = useState<PracticeMode>('practice')
  const [minutes, setMinutes] = useState('')
  const generating = data.data?.generating.state === 'reading'
  const reload = data.reload
  useEffect(() => {
    if (!generating) return
    const t = setInterval(reload, 4000) // the model writes and checks in the background
    return () => clearInterval(t)
  }, [generating, reload])

  async function act(run: () => Promise<unknown>) {
    if (busy.current) return
    busy.current = true
    try {
      await run()
    } catch (err) {
      toast((err as Error).message, 'error')
    } finally {
      busy.current = false
      reload()
    }
  }

  if (data.error) return <ErrorNote error={data.error} onRetry={reload} />
  if (!data.data) return <Skeleton className="h-28" />
  const b = data.data
  const g = b.generating
  return (
    <Card
      title="Treino"
      action={
        <Button
          size="sm"
          variant="ghost"
          disabled={generating || !b.model.available || !b.has_concepts}
          onClick={() => act(() => api.generateQuestions(courseId))}
        >
          <Sparkles className="size-3.5" /> {generating ? 'A gerar…' : 'Gerar perguntas'}
        </Button>
      }
    >
      <div className="grid gap-3 text-[13px]">
        <p>
          <span className="num font-semibold">{b.ready}</span> {b.ready === 1 ? 'pergunta pronta' : 'perguntas prontas'}
          {b.discarded > 0 && <span className="text-muted"> · {b.discarded} deitadas fora por não passarem a verificação</span>}
        </p>
        {g.state === 'done' && (
          <p className="text-muted">
            Última geração: {g.kept} novas, {g.discarded} deitadas fora.
          </p>
        )}
        {g.state === 'error' && <p className="text-danger">{g.error}</p>}
        {!b.has_concepts && <p className="text-muted">As perguntas são feitas por conceito: escolhe primeiro os conceitos da cadeira.</p>}
        {b.has_concepts && !b.model.available && (
          <p className="text-muted">O modelo local {b.model.paused ? `está em pausa (${b.model.paused})` : 'não está disponível'}.</p>
        )}
        {b.past > 0 && (
          <p className="text-muted">
            Mais {b.past} {b.past === 1 ? 'pergunta' : 'perguntas'} de exames antigos, de resposta escrita: a nota é estimada pelo modelo e podes corrigi-la.
          </p>
        )}
        {b.ready + b.past > 0 ? (
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              const m = Number(minutes)
              void act(async () => navigate(`/treino/${(await api.startPractice(courseId, mode, mode === 'timed' && m >= 5 ? m : undefined)).id}`))
            }}
          >
            <Segmented
              label="Modo"
              value={mode}
              onChange={setMode}
              options={(Object.keys(MODE_LABEL) as PracticeMode[]).map((m) => ({ value: m, label: MODE_LABEL[m] }))}
            />
            <p className="text-xs text-muted">{MODE_HINT[mode]}</p>
            <div className="flex flex-wrap items-center gap-2">
              {mode === 'timed' && (
                <Input
                  aria-label="Minutos (vazio: conforme as perguntas)"
                  inputMode="numeric"
                  placeholder="Minutos (auto)"
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.value.replace(/\D/g, ''))}
                  className="w-36"
                />
              )}
              <Button type="submit" variant="primary">
                <Play className="size-3.5" /> Começar ({Math.min(10, b.ready + b.past)})
              </Button>
            </div>
          </form>
        ) : (
          <Empty icon={<FileQuestion className="size-5" />} title="Ainda sem perguntas">
            Cada pergunta vem dos ficheiros da cadeira e só fica se o modelo, sem ver a resposta, chegar à mesma.
          </Empty>
        )}
        {b.attempts.length > 0 && (
          <ul className="grid gap-1 text-xs text-muted">
            {b.attempts.map((a) => (
              <li key={a.id}>
                <Link to={`/treino/${a.id}`} className="hover:underline">
                  {new Date(a.started_at).toLocaleDateString('pt-PT')} · {MODE_LABEL[a.mode]}: {a.right} de {a.answered}
                  {a.answered < a.questions && !a.finished && ` (${a.questions - a.answered} por responder)`}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}
