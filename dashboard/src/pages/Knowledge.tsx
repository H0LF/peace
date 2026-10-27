import { BookOpen, Network, NotebookPen, Search, X } from 'lucide-react'
import { lazy, Suspense, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { obsidianHref } from '../components/materials'
import { Badge, Button, Card, ChunkBoundary, Empty, ErrorNote, Input, PageHeader, Select, Skeleton, KIND_COLOR, cx } from '../components/ui'
import { api, type GraphKind, type GraphLink, type GraphNode } from '../lib/api'
import { useApi } from '../lib/hooks'

const KnowledgeGraph = lazy(() => import('../components/KnowledgeGraph'))
const KINDS: { kind: GraphKind; label: string }[] = [
  { kind: 'course', label: 'Cadeiras' },
  { kind: 'material', label: 'Ficheiros' },
  { kind: 'concept', label: 'Conceitos' },
  { kind: 'task', label: 'Tarefas' },
  { kind: 'exam', label: 'Avaliações' },
]
const KIND_NAME: Record<GraphKind, string> = { course: 'cadeira', material: 'ficheiro', concept: 'conceito', task: 'tarefa', exam: 'avaliação' }
const RELATION: Record<GraphLink['relation'], string> = {
  contains: 'contém',
  mentions: 'fala de',
  similar: 'parecido com',
  has_task: 'tarefa',
  has_exam: 'avaliação',
  study_with: 'estudar com',
}
const fold = (s: string) => s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

/** Kind, course and inferred-link filters. Concepts left without links are dropped. */
function filtered(all: { nodes: GraphNode[]; links: GraphLink[] }, kinds: Set<GraphKind>, course: number | null, inferred: boolean) {
  let keep = all.nodes.filter((n) => kinds.has(n.kind) && (course === null || n.kind === 'concept' || n.course_id === course))
  let ids = new Set(keep.map((n) => n.id))
  const links = all.links.filter((l) => ids.has(l.source) && ids.has(l.target) && (inferred || !l.inferred))
  const linked = new Set(links.flatMap((l) => [l.source, l.target]))
  keep = keep.filter((n) => n.kind !== 'concept' || linked.has(n.id))
  ids = new Set(keep.map((n) => n.id))
  return { nodes: keep, links: links.filter((l) => ids.has(l.source) && ids.has(l.target)) }
}

function Details({ node, all, courses, onSelect }: { node: GraphNode; all: { nodes: GraphNode[]; links: GraphLink[] }; courses: Map<number, GraphNode>; onSelect: (id: string) => void }) {
  const byId = useMemo(() => new Map(all.nodes.map((n) => [n.id, n])), [all])
  const edges = all.links
    .filter((l) => l.source === node.id || l.target === node.id)
    .flatMap((l) => {
      const other = byId.get(l.source === node.id ? l.target : l.source)
      return other ? [{ link: l, other }] : []
    })
    .sort((a, b) => Number(a.link.inferred) - Number(b.link.inferred) || b.link.weight - a.link.weight)
  const course = node.course_id !== null ? courses.get(node.course_id) : undefined
  return (
    <div className="grid gap-4">
      <div>
        <div className="flex items-center gap-2">
          <span aria-hidden className="size-2.5 rounded-full" style={{ background: KIND_COLOR[node.kind], boxShadow: `0 0 10px ${KIND_COLOR[node.kind]}` }} />
          <span className="text-[11px] font-semibold tracking-wide text-faint uppercase">{KIND_NAME[node.kind]}</span>
        </div>
        <h2 className="font-display mt-1 text-lg leading-snug font-semibold break-words">{node.label}</h2>
        {course && node.kind !== 'course' && (
          <button type="button" onClick={() => onSelect(course.id)} className="mt-0.5 text-xs text-muted hover:text-accent">
            {course.label}
          </button>
        )}
        <p className="num mt-1 text-xs text-faint">{edges.length} ligações</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {node.vault_path && (
          <a href={obsidianHref(node.vault_path)} className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-ink hover:brightness-110">
            <BookOpen className="size-4" /> Abrir no Obsidian
          </a>
        )}
        <a href={obsidianHref(node.note)} className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-[13px] font-medium hover:border-accent/50 hover:text-accent">
          <NotebookPen className="size-4" /> Nota do grafo
        </a>
        {node.kind === 'course' && node.course_id !== null && (
          <Link to={`/cadeiras/${node.course_id}`} className="inline-flex h-8 items-center rounded-lg border border-line px-3 text-[13px] font-medium hover:border-accent/50 hover:text-accent">
            Ver cadeira
          </Link>
        )}
      </div>
      <ul aria-label="Ligações" className="grid max-h-[45vh] gap-0.5 overflow-y-auto pr-1">
        {edges.map(({ link, other }) => (
          <li key={`${link.source}>${link.target}:${link.relation}`}>
            <button type="button" onClick={() => onSelect(other.id)} className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-2">
              <span aria-hidden className="mt-1.5 size-1.5 shrink-0 rounded-full" style={{ background: KIND_COLOR[other.kind] }} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px]">{other.label}</span>
                <span className="block truncate text-[11px] text-faint">
                  {RELATION[link.relation]}
                  {link.why ? ` · ${link.why}` : ''}
                </span>
              </span>
              {link.inferred && <Badge tone="exam">inferida</Badge>}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function Knowledge() {
  const graph = useApi(() => api.knowledgeGraph(), [], 0)
  const [kinds, setKinds] = useState(() => new Set<GraphKind>(KINDS.map((k) => k.kind)))
  const [course, setCourse] = useState<number | null>(null)
  const [inferred, setInferred] = useState(true)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)

  const all = useMemo(() => graph.data ?? { nodes: [], links: [] }, [graph.data])
  const shown = useMemo(() => filtered(all, kinds, course, inferred), [all, kinds, course, inferred])
  const courses = useMemo(() => new Map(all.nodes.filter((n) => n.kind === 'course' && n.course_id !== null).map((n) => [n.course_id!, n])), [all])
  // A selection hidden by the filters counts as none.
  const node = selected ? shown.nodes.find((n) => n.id === selected) : undefined
  const results = useMemo(() => {
    const q = fold(query.trim())
    if (!q) return []
    return shown.nodes
      .filter((n) => fold(n.label).includes(q))
      .sort((a, b) => Number(!fold(a.label).startsWith(q)) - Number(!fold(b.label).startsWith(q)) || b.degree - a.degree)
      .slice(0, 8)
  }, [query, shown])
  const communities = new Set(all.nodes.map((n) => n.community)).size

  const pick = (id: string | null) => {
    setSelected(id)
    setQuery('')
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (results[0]) pick(results[0].id)
  }
  const toggle = (k: GraphKind) =>
    setKinds((prev) => {
      const next = new Set(prev)
      if (!next.delete(k)) next.add(k)
      return next
    })

  return (
    <div className="grid gap-5">
      <PageHeader
        title="Conhecimento"
        subtitle="Cadeiras, ficheiros, conceitos, tarefas e avaliações, ligados pelo conteúdo dos teus materiais. Ligações inferidas são calculadas e podem falhar."
      />
      {graph.error && <ErrorNote error={graph.error} onRetry={graph.reload} />}
      {!graph.data ? (
        <Skeleton className="h-[70dvh]" />
      ) : !all.nodes.length ? (
        <Card>
          <Empty icon={<Network className="size-5" />} title="Ainda sem ficheiros analisados">
            O grafo aparece depois de a sincronização descarregar e ler os materiais das cadeiras.
          </Empty>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <form onSubmit={submit} role="search" className="relative w-full sm:w-72">
              <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint" />
              <Input aria-label="Procurar no grafo" placeholder="Procurar conceito, ficheiro…" value={query} onChange={(e) => setQuery(e.target.value)} className="w-full pl-9" />
              {results.length > 0 && (
                <ul aria-label="Resultados" className="absolute top-full right-0 left-0 z-20 mt-1 grid rounded-xl border border-line bg-surface p-1 shadow-xl">
                  {results.map((n) => (
                    <li key={n.id}>
                      <button type="button" onClick={() => pick(n.id)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-surface-2">
                        <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: KIND_COLOR[n.kind] }} />
                        <span className="min-w-0 flex-1 truncate">{n.label}</span>
                        <span className="text-[11px] text-faint">{KIND_NAME[n.kind]}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </form>
            <Select aria-label="Filtrar por cadeira" value={course ?? ''} onChange={(e) => setCourse(e.target.value ? Number(e.target.value) : null)} className="w-full sm:w-64">
              <option value="">Todas as cadeiras</option>
              {[...courses.values()]
                .sort((a, b) => a.label.localeCompare(b.label))
                .map((c) => (
                  <option key={c.id} value={c.course_id!}>
                    {c.label}
                  </option>
                ))}
            </Select>
            <div role="group" aria-label="Tipos de nó" className="flex flex-wrap gap-1.5">
              {KINDS.map(({ kind, label }) => (
                <button
                  key={kind}
                  type="button"
                  aria-pressed={kinds.has(kind)}
                  onClick={() => toggle(kind)}
                  className={cx(
                    'inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition-colors',
                    kinds.has(kind) ? 'border-line-strong bg-surface-2 text-text' : 'border-line text-faint line-through',
                  )}
                >
                  <span aria-hidden className="size-2 rounded-full ring-1 ring-line-strong" style={{ background: KIND_COLOR[kind], opacity: kinds.has(kind) ? 1 : 0.3 }} />
                  {label}
                </button>
              ))}
              <button
                type="button"
                aria-pressed={inferred}
                onClick={() => setInferred((v) => !v)}
                className={cx('inline-flex h-8 items-center rounded-lg border px-2.5 text-xs font-medium', inferred ? 'border-exam/40 bg-exam-soft text-exam' : 'border-line text-faint')}
              >
                Ligações inferidas
              </button>
            </div>
          </div>

          <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="hud relative h-[62dvh] min-h-[420px] overflow-hidden rounded-2xl border border-line bg-[var(--scene-bg)]">
              <ChunkBoundary fallback={<p className="p-6 text-[13px] text-muted">Não foi possível carregar o grafo. Recarrega a página.</p>}>
                <Suspense fallback={<Skeleton className="absolute inset-0" />}>
                  <KnowledgeGraph nodes={shown.nodes} links={shown.links} selected={node?.id ?? null} onSelect={setSelected} />
                </Suspense>
              </ChunkBoundary>
              <p className="num pointer-events-none absolute bottom-3 left-4 text-[11px] text-muted">
                {shown.nodes.length} nós · {shown.links.length} ligações · {communities} comunidades (Graphify)
              </p>
            </div>
            <Card
              title={node ? 'Detalhes' : 'Como usar'}
              action={
                node && (
                  <Button size="sm" variant="ghost" aria-label="Fechar detalhes" onClick={() => setSelected(null)}>
                    <X className="size-4" />
                  </Button>
                )
              }
            >
              {node ? (
                <Details node={node} all={all} courses={courses} onSelect={setSelected} />
              ) : (
                <div className="grid gap-3 text-[13px] text-muted">
                  <p>Clica num nó para ver as ligações e abrir o ficheiro ou a nota no Obsidian. Arrasta para rodar, usa a roda para aproximar.</p>
                  <ul className="grid gap-1.5">
                    {KINDS.map(({ kind, label }) => (
                      <li key={kind} className="flex items-center gap-2">
                        <span aria-hidden className="size-2.5 rounded-full ring-1 ring-line-strong" style={{ background: KIND_COLOR[kind], boxShadow: `0 0 8px ${KIND_COLOR[kind]}` }} />
                        <span className="text-text">{label}</span>
                        <span className="num ml-auto text-xs text-faint">{all.nodes.filter((n) => n.kind === kind).length}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="text-xs text-faint">
                    As partículas mostram o material a estudar para cada tarefa. As notas do grafo são copiadas para <span className="text-muted">07 - Generated/Graphify</span> no cofre.
                  </p>
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
