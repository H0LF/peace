import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { MaterialList } from '../components/materials'
import { Card, ErrorNote, Input, PageHeader, Select, Skeleton } from '../components/ui'
import { api } from '../lib/api'
import { plural } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'

const fold = (s: string) => s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

/** Every course file and link from Moodle and inforestudante, searchable. */
export default function Files() {
  const now = useNow(300_000)
  const materials = useApi(() => api.materials(), [], 300_000)
  const [query, setQuery] = useState('')
  const [course, setCourse] = useState('')
  const all = useMemo(() => materials.data ?? [], [materials.data])
  const courses = useMemo(() => [...new Map(all.filter((m) => m.course).map((m) => [m.course!.id, m.course!.name])).entries()].sort((a, b) => a[1].localeCompare(b[1])), [all])
  const shown = useMemo(() => {
    const q = fold(query.trim())
    return all.filter(
      (m) => (!course || String(m.course?.id) === course) && (!q || fold(`${m.title} ${m.filename ?? ''} ${m.section}`).includes(q)),
    )
  }, [all, query, course])
  const files = all.filter((m) => m.kind === 'file').length

  return (
    <div className="grid gap-5">
      <PageHeader title="Ficheiros" subtitle={materials.data ? `${plural(files, 'ficheiro', 'ficheiros')} das tuas cadeiras, copiados para o cofre do Obsidian.` : undefined} />
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
      </div>
      <Card>{materials.data ? <MaterialList items={shown} now={now} showCourse={!course} /> : <Skeleton className="h-64" />}</Card>
    </div>
  )
}
