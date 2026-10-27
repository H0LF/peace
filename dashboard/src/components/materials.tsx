import { ExternalLink, FileText, FolderOpen, Link2 } from 'lucide-react'
import type { Material, StudyMaterial } from '../lib/api'
import { dayMonth } from '../lib/format'
import { Badge, Empty, cx } from './ui'

export const VAULT = 'Peace Vault'
/** Scraped links are untrusted: only http(s) becomes a link (no file:, data: or custom app schemes). */
export const safeHttp = (url: string | null | undefined) => (url && /^https?:\/\//i.test(url) ? url : undefined)
export const obsidianHref = (path: string) => `obsidian://open?vault=${encodeURIComponent(VAULT)}&file=${encodeURIComponent(path)}`

/** Files suggested for a study session: open the vault copy in Obsidian, or the original on the site. */
export function StudyMaterials({ items, compact = false, className }: { items: StudyMaterial[]; compact?: boolean; className?: string }) {
  if (!items.length) return null
  if (compact)
    return (
      <ul aria-label="Material para estudar" className={cx('flex flex-wrap gap-1.5', className)}>
        {items.map((m) => (
          <li key={m.id} className="min-w-0">
            <a
              href={m.vault_path ? obsidianHref(m.vault_path) : safeHttp(m.url)}
              {...(!m.vault_path && { target: '_blank', rel: 'noreferrer noopener' })}
              title={m.why}
              className="inline-flex max-w-56 items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted hover:border-accent/40 hover:text-accent"
            >
              <FileText className="size-3 shrink-0" />
              <span className="truncate">{m.title}</span>
            </a>
          </li>
        ))}
      </ul>
    )
  return (
    <ul aria-label="Material para estudar" className={cx('grid gap-1', className)}>
      {items.map((m) => (
        <li key={m.id} className="flex items-center gap-3 rounded-lg border border-line/70 bg-surface-2/60 px-3 py-2">
          <FileText className="size-4 shrink-0 text-accent" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-medium">{m.title}</div>
            <div className="truncate text-[11px] text-faint">{m.why}</div>
          </div>
          {m.vault_path && (
            <a
              href={obsidianHref(m.vault_path)}
              className="rounded-md px-2 py-1 text-xs font-medium text-accent hover:bg-accent-soft"
              aria-label={`Abrir ${m.title} no Obsidian`}
            >
              Obsidian
            </a>
          )}
          {safeHttp(m.url) && (
            <a
              href={safeHttp(m.url)}
              target="_blank"
              rel="noreferrer noopener"
              aria-label={`Abrir ${m.title} no site da cadeira`}
              className="grid size-7 place-items-center rounded-md text-muted hover:bg-surface-3 hover:text-text"
            >
              <ExternalLink className="size-3.5" />
            </a>
          )}
        </li>
      ))}
    </ul>
  )
}

const size = (bytes: number | null) =>
  bytes == null ? '' : bytes < 1_000_000 ? `${Math.max(1, Math.round(bytes / 1000))} KB` : `${(bytes / 1_000_000).toFixed(1).replace('.', ',')} MB`

/** Course files and links grouped by section; `showCourse` prefixes each section with its course. */
export function MaterialList({ items, now, showCourse = false }: { items: Material[]; now: Date; showCourse?: boolean }) {
  if (!items.length)
    return <Empty icon={<FolderOpen className="size-5" />} title="Sem materiais">Aparecem aqui depois da sincronização.</Empty>
  const group = (m: Material) => (showCourse ? `${m.course?.name ?? 'Sem cadeira'} · ${m.section}` : m.section)
  const sections = [...new Set(items.map(group))]
  const Heading = showCourse ? 'h2' : 'h3'

  return (
    <div className="grid gap-4">
      <p className="text-xs text-faint">
        Os ficheiros são copiados para o cofre em <span className="text-muted">02 - University/Courses/…/Materiais</span>.
      </p>
      {sections.map((section) => (
        <section key={section}>
          <Heading className="mb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">{section}</Heading>
          <ul className="grid">
            {items
              .filter((m) => group(m) === section)
              .map((m) => {
                const fresh = m.modified_at && now.getTime() - new Date(m.modified_at).getTime() < 7 * 86_400_000
                return (
                  <li key={m.id} className="flex items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-surface-2">
                    {m.kind === 'link' ? <Link2 className="size-4 shrink-0 text-faint" /> : <FileText className="size-4 shrink-0 text-faint" />}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px]">{m.title}</div>
                      {m.kind === 'file' && (
                        <div className="num truncate text-[11px] text-faint">
                          {[m.filename !== m.title && m.filename, size(m.size), m.modified_at && dayMonth(m.modified_at)].filter(Boolean).join(' · ')}
                        </div>
                      )}
                    </div>
                    {fresh && <Badge tone="accent">novo</Badge>}
                    {m.vault_path && (
                      <a
                        href={obsidianHref(m.vault_path)}
                        className="rounded-md px-2 py-1 text-xs font-medium text-accent hover:bg-accent-soft"
                        aria-label={`Abrir ${m.title} no Obsidian`}
                      >
                        Obsidian
                      </a>
                    )}
                    {safeHttp(m.url) && (
                      <a
                        href={safeHttp(m.url)}
                        target="_blank"
                        rel="noreferrer noopener"
                        aria-label={`Abrir ${m.title} ${m.kind === 'link' ? '' : 'no Moodle'}`}
                        className="grid size-7 place-items-center rounded-md text-muted hover:bg-surface-3 hover:text-text"
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    )}
                  </li>
                )
              })}
          </ul>
        </section>
      ))}
    </div>
  )
}
