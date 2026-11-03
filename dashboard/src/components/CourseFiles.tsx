import { ChevronLeft, ChevronRight, Download, ExternalLink, FileText, History, Link2, MonitorUp, Search, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { api, type Material } from '../lib/api'
import { dayMonth } from '../lib/format'
import { useApi } from '../lib/hooks'
import { onThisPc, safeHttp, size, vaultLink } from './materials'
import { Badge, Button, Empty, Input, Segmented, cx, useToast } from './ui'

type Filter = 'all' | 'pdf' | 'other'
const isPdf = (m: Material) => m.mimetype === 'application/pdf' || /\.pdf$/i.test(m.filename ?? '')
const fileUrl = (m: Material) => `/api/v1/materials/${m.id}/file`
const canShow = () => navigator.pdfViewerEnabled !== false // phones often cannot show a PDF inside a page
const FRESH_DAYS = 7

function Viewer({ file, files, allFiles, onChoose, onClose }: { file: Material; files: Material[]; allFiles?: Material[]; onChoose: (m: Material) => void; onClose: () => void }) {
  const toast = useToast()
  const ref = useRef<HTMLDialogElement>(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<{ q: string; text: string; sources: { title: string; detail: string }[] }>()
  const [busy, setBusy] = useState(false)
  const at = files.findIndex((f) => f.id === file.id)
  const ready = !!file.fetched_at
  const needs = (file.file_refs ?? []).filter((r) => r.action === 'abrir')
  const hands = (file.file_refs ?? []).filter((r) => r.action === 'entregar')
  const others = (allFiles ?? []).filter((f) => f.duplicate_of_id === file.id && f.duplicate_kind !== 'dataset') // nothing joined is ever out of reach

  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
    return () => d?.close()
  }, [])
  useEffect(() => {
    setAnswer(undefined)
    api.markOpened(file.id).catch(() => undefined) // only "continue reading" depends on it
  }, [file.id])

  async function ask(q: string, summary: boolean) {
    setBusy(true)
    try {
      const r = await api.askDocument(file.id, q, summary)
      setAnswer({ q: summary ? 'Resumo' : q, text: r.reply, sources: r.sources })
      setQuestion('')
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  async function openOnPc() {
    try {
      const r = await api.openOnPc(file.id)
      toast(r.reply, r.connected ? 'ok' : 'error')
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (question.trim()) void ask(question.trim(), false)
  }

  return (
    <dialog ref={ref} aria-label={file.title} onClose={onClose} className="m-auto h-[calc(100dvh-1rem)] max-h-none w-[calc(100vw-1rem)] max-w-none rounded-2xl border border-line bg-surface p-0 text-text shadow-2xl backdrop:bg-black/60">
      <div className="flex h-full flex-col">
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <Button variant="ghost" size="icon" aria-label="Ficheiro anterior" disabled={at <= 0} onClick={() => onChoose(files[at - 1])}>
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Ficheiro seguinte" disabled={at < 0 || at >= files.length - 1} onClick={() => onChoose(files[at + 1])}>
            <ChevronRight className="size-4" />
          </Button>
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{file.title}</h2>
          {ready && (
            <>
              <a href={fileUrl(file)} target="_blank" rel="noreferrer noopener" className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs text-muted hover:bg-surface-2 hover:text-text">
                <ExternalLink className="size-3.5" /> Novo separador
              </a>
              <a href={fileUrl(file)} download={file.filename ?? undefined} className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs text-muted hover:bg-surface-2 hover:text-text">
                <Download className="size-3.5" /> Descarregar
              </a>
              {onThisPc && (
                <button type="button" onClick={() => void openOnPc()} className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs text-muted hover:bg-surface-2 hover:text-text">
                  <MonitorUp className="size-3.5" /> Abrir no programa do PC
                </button>
              )}
            </>
          )}
          <Button variant="ghost" size="icon" aria-label="Fechar" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </header>
        {(file.summary || needs.length > 0 || hands.length > 0 || others.length > 0) && (
          <div aria-label="Sobre este ficheiro" className="grid gap-1.5 border-b border-line px-4 py-2.5 text-[13px]">
            {file.summary && <p className="text-muted">{file.summary}</p>}
            {needs.length > 0 && (
              <div role="status" className="rounded-lg border border-accent/40 bg-accent-soft px-3 py-2">
                <p className="font-medium">Este ficheiro diz-te para usares também:</p>
                <ul className="mt-1 grid gap-1">
                  {needs.map((r) => {
                    const target = files.find((f) => f.id === r.material_id) ?? allFiles?.find((f) => f.id === r.material_id)
                    return (
                      <li key={r.name} className="flex flex-wrap items-baseline gap-x-2">
                        {target ? <button type="button" onClick={() => onChoose(target)} className="font-medium text-accent hover:underline">{r.name}</button> : <span className="font-medium">{r.name}</span>}
                        {!target && <span className="text-xs text-faint">não está nos teus ficheiros</span>}
                        {r.why && <span className="text-xs text-muted">{r.why}</span>}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
            {hands.length > 0 && (
              <p className="text-xs text-muted">Tens de entregar: {hands.map((r) => r.name).join(', ')}</p>
            )}
            {others.length > 0 && (
              <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
                Também em:
                {others.map((o) => (
                  <button key={o.id} type="button" onClick={() => onChoose(o)} className="text-accent hover:underline">{o.title}</button>
                ))}
              </p>
            )}
          </div>
        )}
        <div className="min-h-0 flex-1 bg-surface-2">
          {!ready ? (
            <div className="grid h-full place-items-center p-6">
              <Empty icon={<FileText className="size-5" />} title="Este ficheiro ainda não foi descarregado">
                A PEACE vai buscá-lo na próxima sincronização.{' '}
                {safeHttp(file.url) && <a href={safeHttp(file.url)} target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">Abrir no Moodle</a>}
              </Empty>
            </div>
          ) : isPdf(file) && canShow() ? (
            <iframe key={file.id} title={file.title} src={`${fileUrl(file)}#view=FitH`} className="size-full" />
          ) : (
            <div className="grid h-full place-items-center p-6">
              <Empty icon={<FileText className="size-5" />} title={isPdf(file) ? 'Este browser não mostra PDFs dentro da página' : 'Este tipo de ficheiro abre fora da PEACE'}>
                <a href={fileUrl(file)} download={isPdf(file) ? undefined : (file.filename ?? undefined)} target={isPdf(file) ? '_blank' : undefined} rel="noreferrer noopener" className="text-accent hover:underline">
                  {isPdf(file) ? 'Abrir o PDF' : 'Descarregar'}
                </a>
              </Empty>
            </div>
          )}
        </div>
        <footer className="border-t border-line p-3">
          {answer && (
            <div aria-live="polite" className="mb-2 max-h-40 overflow-y-auto rounded-lg bg-surface-2 p-3 text-[13px] leading-relaxed">
              <p className="text-xs text-faint">{answer.q}</p>
              <p className="mt-1">{answer.text}</p>
              {answer.sources.length > 0 && <p className="mt-1 text-xs text-faint">{answer.sources.map((s) => s.detail || s.title).join(' · ')}</p>}
            </div>
          )}
          <form onSubmit={submit} className="flex items-center gap-2">
            <Sparkles aria-hidden className="size-4 shrink-0 text-accent" />
            <Input aria-label="Pergunta sobre este documento" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} placeholder="Pergunta sobre este documento…" />
            <Button type="submit" disabled={busy || !question.trim()}>{busy ? 'A ler…' : 'Perguntar'}</Button>
            <Button disabled={busy} onClick={() => void ask('', true)}>Resumir</Button>
          </form>
        </footer>
      </div>
    </dialog>
  )
}

/** A course's files: find one, read it here, ask about it. */
export function CourseFiles({ items, now }: { items: Material[]; now: Date }) {
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const recent = useApi(() => api.recentReading(), [])
  const everything = useMemo(() => items.filter((m) => m.kind === 'file'), [items])
  const files = useMemo(() => everything.filter((m) => !m.duplicate_of_id || m.duplicate_kind === 'dataset'), [everything]) // a copy opens through the file it copies
  const links = useMemo(() => items.filter((m) => m.kind !== 'file'), [items])
  const opened = files.find((m) => String(m.id) === params.get('ficheiro'))
  const open = (m: Material) => setParams((p) => (p.set('ficheiro', String(m.id)), p), { replace: true })
  const close = () => {
    setParams((p) => (p.delete('ficheiro'), p), { replace: true })
    recent.reload()
  }

  if (!items.length) return <Empty icon={<FileText className="size-5" />} title="Sem materiais">Aparecem aqui depois da sincronização.</Empty>
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const shown = files.filter((m) => {
    const text = `${m.title} ${m.filename ?? ''} ${m.section}`.toLowerCase()
    return words.every((w) => text.includes(w)) && (filter === 'all' || (filter === 'pdf') === isPdf(m))
  })
  const sections = [...new Set(shown.map((m) => m.section))]
  const lastRead = (recent.data ?? []).map((r) => files.find((f) => f.id === r.id)).filter((m): m is Material => !!m).slice(0, 3)

  return (
    <div className="grid gap-4">
      {lastRead.length > 0 && (
        <section aria-label="Continuar a ler">
          <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-faint uppercase">
            <History aria-hidden className="size-3" /> Continuar a ler
          </h3>
          <div className="flex flex-wrap gap-2">
            {lastRead.map((m) => (
              <button key={m.id} type="button" onClick={() => open(m)} className="max-w-64 truncate rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-xs hover:border-accent/50">
                {m.title}
              </button>
            ))}
          </div>
        </section>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-48 flex-1">
          <Search aria-hidden className="absolute top-2.5 left-2.5 size-4 text-faint" />
          <Input aria-label="Procurar nos ficheiros" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Procurar nos ficheiros…" className="pl-8" />
        </div>
        <Segmented<Filter>
          label="Tipo de ficheiro"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'Todos' },
            { value: 'pdf', label: 'PDF' },
            { value: 'other', label: 'Outros' },
          ]}
        />
      </div>
      {!shown.length && <p className="text-[13px] text-muted">Nenhum ficheiro corresponde.</p>}
      {sections.map((section) => (
        <section key={section}>
          <h3 className="mb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">{section}</h3>
          <ul className="grid">
            {shown.filter((m) => m.section === section).map((m) => (
              <li key={m.id} className="flex items-center gap-1 rounded-lg hover:bg-surface-2">
                <button type="button" onClick={() => open(m)} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg px-2 py-1.5 text-left">
                  <FileText aria-hidden className={cx('size-4 shrink-0', isPdf(m) ? 'text-accent' : 'text-faint')} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px]">{m.title}</span>
                    <span className="num block truncate text-[11px] text-faint">{[m.filename !== m.title && m.filename, size(m.size), m.modified_at && dayMonth(m.modified_at)].filter(Boolean).join(' · ')}</span>
                  </span>
                  {m.modified_at && now.getTime() - new Date(m.modified_at).getTime() < FRESH_DAYS * 864e5 && <Badge tone="accent">novo</Badge>}
                  {!m.fetched_at && <Badge>por descarregar</Badge>}
                </button>
                {m.vault_path && (
                  <a {...vaultLink({ id: m.id, vault_path: m.vault_path })} aria-label={`Abrir ${m.title} no Obsidian`} className="shrink-0 rounded-md px-2 py-1 text-xs text-muted hover:text-accent">
                    Obsidian
                  </a>
                )}
                {safeHttp(m.url) && (
                  <a href={safeHttp(m.url)} target="_blank" rel="noreferrer noopener" aria-label={`Abrir ${m.title} no Moodle`} className="grid size-7 shrink-0 place-items-center rounded-md text-muted hover:bg-surface-3 hover:text-text">
                    <ExternalLink className="size-3.5" />
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
      {links.length > 0 && (
        <section>
          <h3 className="mb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">Ligações</h3>
          <ul className="grid">
            {links.map((m) => (
              <li key={m.id}>
                <a href={safeHttp(m.url)} target="_blank" rel="noreferrer noopener" className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-[13px] hover:bg-surface-2">
                  <Link2 aria-hidden className="size-4 shrink-0 text-faint" />
                  <span className="min-w-0 flex-1 truncate">{m.title}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
      {opened && <Viewer file={opened} files={shown.includes(opened) ? shown : files} allFiles={everything} onChoose={open} onClose={close} />}
    </div>
  )
}
