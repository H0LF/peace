import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { MaterialGallery, MaterialList } from '../components/materials'
import { Card, ErrorNote, Input, PageHeader, Segmented, Select, Skeleton, courseColor } from '../components/ui'
import { api } from '../lib/api'
import { plural } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

type View = 'list' | 'gallery'
const VIEW_KEY = 'peace-files-view'

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'gallery' ? 'gallery' : 'list'
  } catch {
    return 'list'
  }
}

const fold = (s: string) => s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

/** Every course file and link from Moodle and inforestudante, searchable, one card per course, as a list or a gallery. */
export default function Files() {
  const now = useNow(300_000)
  const materials = useApi(() => api.materials(), [], 300_000)
  const [query, setQuery] = useState('')
  const [course, setCourse] = useState('')
  const [view, setView] = useState<View>(readView)
  const choose = (v: View) => {
    setView(v)
    try {
      localStorage.setItem(VIEW_KEY, v)
    } catch {
      /* private mode: the choice just won't persist */
    }
  }
  const all = useMemo(() => materials.data ?? [], [materials.data])
  const courses = useMemo(() => [...new Map(all.filter((m) => m.course).map((m) => [m.course!.id, m.course!.name])).entries()].sort((a, b) => a[1].localeCompare(b[1])), [all])
  const shown = useMemo(() => {
    const q = fold(query.trim())
    return all.filter(
      (m) => (!course || String(m.course?.id) === course) && (!q || fold(`${m.title} ${m.filename ?? ''} ${m.section}`).includes(q)),
    )
  }, [all, query, course])
  const files = all.filter((m) => m.kind === 'file').length
  const groups = useMemo(() => {
    const by = new Map<string, { id: number | null; name: string; items: typeof shown }>()
    for (const m of shown) {
      const key = String(m.course?.id ?? 'none')
      if (!by.has(key)) by.set(key, { id: m.course?.id ?? null, name: m.course?.name ?? 'Sem cadeira', items: [] })
      by.get(key)!.items.push(m)
    }
    return [...by.values()].sort((a, b) => (a.id == null ? 1 : b.id == null ? -1 : a.name.localeCompare(b.name)))
  }, [shown])

  return (
    <div className="grid gap-5">
      <PageHeader title="Ficheiros" subtitle={materials.data ? `${plural(files, 'ficheiro', 'ficheiros')} das tuas cadeiras, copiados para o cofre do Obsidian (02 - University/Courses/…/Materiais).` : undefined} />
      {materials.error && <ErrorNote error={materials.error} onRetry={materials.reload} />}
      <div className="flex flex-wrap gap-2">
        <div role="search" className="relative w-full sm:w-80">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint" />
          <Input aria-label="Procurar ficheiros" placeholder="Procurar ficheiro…" value={query} onChange={(e) => setQuery(e.target.value)} className="w-full pl-9" />
        </div>
        <Select aria-label="Filtrar por cadeira" value={course} onChange={(e) => setCourse(e.target.value)} className="w-full sm:w-72">
          <option value="">Todas as cadeiras</option>
          {courses.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </Select>
        <Segmented
          label="Vista"
          value={view}
          onChange={choose}
          options={[
            { value: 'list', label: 'Lista' },
            { value: 'gallery', label: 'Galeria' },
          ]}
        />
      </div>
      {!materials.data ? (
        <Card><Skeleton className="h-64" /></Card>
      ) : !groups.length ? (
        <Card><MaterialList items={[]} now={now} /></Card>
      ) : (
        groups.map((g) => (
          <Card
            key={g.id ?? 'none'}
            title={
              <span className="inline-flex items-center gap-2">
                {g.id != null && <span aria-hidden className="size-2.5 rounded-full" style={{ background: courseColor(g.id) }} />}
                {g.name}
                <span className="num text-xs font-normal text-faint">{g.items.length}</span>
              </span>
            }
          >
            {view === 'list' ? <MaterialList items={g.items} now={now} note={false} /> : <MaterialGallery items={g.items} now={now} />}
          </Card>
        ))
      )}
    </div>
  )
}
