import { Check, CircleAlert, X } from 'lucide-react'
import {
  Component,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ComponentProps,
  type ReactNode,
} from 'react'
import type { CourseRef, GraphKind } from '../lib/api'
import { cssVar } from '../lib/theme'
import { courseHue } from '../lib/format'

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ')

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-accent-ink hover:brightness-110 shadow-sm',
  secondary: 'border border-line bg-surface-2 text-text hover:bg-surface-3 hover:border-line-strong',
  ghost: 'text-muted hover:bg-surface-2 hover:text-text',
  danger: 'border border-line bg-surface-2 text-danger hover:bg-danger-soft',
}

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md' | 'icon' }) {
  return (
    <button
      type="button"
      {...props}
      className={cx(
        'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-[background,color,border,filter] disabled:pointer-events-none disabled:opacity-50',
        size === 'sm' && 'h-7 px-2.5 text-xs',
        size === 'md' && 'h-9 px-3.5 text-sm',
        size === 'icon' && 'size-8',
        VARIANTS[variant],
        className,
      )}
    />
  )
}

export function Card({
  title,
  action,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cx('hud animate-rise rounded-2xl border border-line bg-surface/90 backdrop-blur-sm', className)}>
      {(title || action) && (
        <header className="flex min-h-12 items-center justify-between gap-3 px-5 pt-3">
          <h2 className="font-display text-[14px] font-semibold tracking-wide text-text uppercase">{title}</h2>
          {action}
        </header>
      )}
      <div className={cx(title || action ? 'px-5 pt-1 pb-5' : 'p-5', bodyClassName)}>{children}</div>
    </section>
  )
}

const TONES = {
  neutral: 'bg-surface-3 text-muted',
  accent: 'bg-accent-soft text-accent',
  danger: 'bg-danger-soft text-danger',
  warn: 'bg-warn-soft text-warn',
  ok: 'bg-ok-soft text-ok',
  exam: 'bg-exam-soft text-exam',
}
export type Tone = keyof typeof TONES

export function Badge({ tone = 'neutral', children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cx('inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-[11px] font-medium whitespace-nowrap', TONES[tone], className)}>
      {children}
    </span>
  )
}

/** "Métodos de Otimização e Análise de Dados" -> "MOAD". */
export function acronym(name: string): string {
  const words = name.split(/\s+/).filter((w) => w.length > 2 || /^[IVX]+$/.test(w))
  return words.map((w) => (/^[IVX]+$/.test(w) ? w : w[0].toUpperCase())).join('')
}

/** Knowledge graph node colours: courses and files follow the accent; exams keep their cyan. */
export const KIND_COLOR: Record<GraphKind, string> = { course: 'var(--holo-pale)', material: 'var(--holo)', concept: 'var(--graph-concept)', task: 'var(--graph-task)', exam: 'var(--graph-exam)' }
/** The same colour resolved to a value WebGL can use. */
export const kindColor = (kind: GraphKind) => {
  const v = KIND_COLOR[kind]
  return v.startsWith('var(') ? cssVar(v.slice(4, -1), '#ffb547') : v
}
export const courseColor = (id: number) => `hsl(${courseHue(id)} 72% 66%)`

export function CourseTag({ course, compact = false }: { course: CourseRef | null; compact?: boolean }) {
  if (!course) return null
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-xs text-muted" title={course.name}>
      <span className="size-2 shrink-0 rounded-full" style={{ background: courseColor(course.id) }} />
      <span className="truncate">{compact ? acronym(course.name) : course.name}</span>
    </span>
  )
}

export function Checkbox({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <span className="relative inline-grid size-5 shrink-0 place-items-center">
      <input
        type="checkbox"
        checked={checked}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
        className="peer size-5 cursor-pointer appearance-none rounded-md border border-line-strong bg-surface-2 transition-colors hover:border-muted checked:border-ok checked:bg-ok"
      />
      <Check className="pointer-events-none absolute size-3.5 text-bg opacity-0 transition-opacity peer-checked:opacity-100" strokeWidth={3} />
    </span>
  )
}

export function Progress({ value, tone = 'accent', label }: { value: number; tone?: 'accent' | 'ok'; label: string }) {
  return (
    <div role="progressbar" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={100} className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
      <div className={cx('h-full rounded-full transition-[width] duration-500', tone === 'ok' ? 'bg-ok' : 'bg-accent')} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  )
}

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="m-auto max-h-[calc(100dvh-2rem)] w-[min(560px,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-[2px]"
    >
      {open && (
        <>
          <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
            <h2 id={titleId} className="text-[15px] font-semibold tracking-tight">
              {title}
            </h2>
            <Button variant="ghost" size="icon" aria-label="Fechar" onClick={onClose}>
              <X className="size-4" />
            </Button>
          </header>
          <div className="px-5 py-4">{children}</div>
          {footer && <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
        </>
      )}
    </dialog>
  )
}

// Full width unless the caller sets a width (two width classes would fight over CSS order).
const control = (className?: string) =>
  cx(
    'rounded-lg border border-line bg-surface-2 px-3 text-sm text-text placeholder:text-faint transition-colors hover:border-line-strong focus:border-accent focus:outline-none',
    !/(^|\s)w-/.test(className ?? '') && 'w-full',
    className,
  )

export const Input = ({ className, ...p }: ComponentProps<'input'>) => <input {...p} className={control(cx('h-9', className))} />
export const Select = ({ className, ...p }: ComponentProps<'select'>) => <select {...p} className={control(cx('h-9 pr-8', className))} />
export const Textarea = ({ className, ...p }: ComponentProps<'textarea'>) => (
  <textarea {...p} className={control(cx('min-h-24 py-2 leading-relaxed', className))} />
)

export function Field({ label, children, hint, className }: { label: string; children: ReactNode; hint?: string; className?: string }) {
  return (
    <label className={cx('grid gap-1.5', className)}>
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </label>
  )
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  label: string
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex max-w-full overflow-x-auto rounded-lg border border-line bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={cx(
            'h-7 rounded-md px-2.5 text-xs font-medium whitespace-nowrap transition-colors',
            o.value === value ? 'bg-surface-3 text-text shadow-sm' : 'text-muted hover:text-text',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Small dropdown menu: keyboard reachable, closes on Escape or outside click. */
export function Menu({ trigger, label, children }: { trigger: ReactNode; label: string; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <Button variant="ghost" size="icon" aria-label={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {trigger}
      </Button>
      {open && (
        <div role="menu" className="animate-rise absolute right-0 z-30 mt-1 min-w-48 rounded-xl border border-line bg-surface-2 p-1 shadow-xl">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

export function MenuItem({ children, onSelect }: { children: ReactNode; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onSelect}
      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-text hover:bg-surface-3 focus:bg-surface-3 focus:outline-none"
    >
      {children}
    </button>
  )
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="grid place-items-center gap-1 rounded-xl border border-dashed border-line px-4 py-8 text-center">
      {icon && <div className="mb-1 text-faint">{icon}</div>}
      <p className="text-[13px] font-medium text-muted">{title}</p>
      {children && <div className="text-xs text-faint">{children}</div>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded-lg bg-surface-2', className)} />
}

export function ErrorNote({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center gap-3 rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-[13px] text-danger">
      <CircleAlert className="size-4 shrink-0" />
      <span className="min-w-0 flex-1">Não foi possível carregar: {error.message}</span>
      {onRetry && (
        <Button size="sm" onClick={onRetry}>
          Tentar de novo
        </Button>
      )}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-surface-2 px-1.5 py-px font-sans text-[10px] font-medium text-muted">{children}</kbd>
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-[13px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

type ToastTone = 'ok' | 'error' | 'info'
type Toast = { id: number; message: string; tone: ToastTone }
const ToastContext = createContext<(message: string, tone?: ToastTone) => void>(() => {})
export const useToast = () => useContext(ToastContext)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((message: string, tone: ToastTone = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, message, tone }])
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000)
  }, [])
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 grid w-[min(360px,calc(100vw-2rem))] gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cx(
              'animate-rise rounded-xl border bg-surface-2 px-4 py-3 text-[13px] shadow-xl',
              t.tone === 'error' ? 'border-danger/40 text-danger' : t.tone === 'ok' ? 'border-ok/30 text-text' : 'border-line text-text',
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

/** Renders `fallback` when a lazily loaded part (WebGL orb, graph) fails, e.g. an old chunk after a redeploy. */
export class ChunkBoundary extends Component<{ children: ReactNode; fallback?: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    return this.state.failed ? (this.props.fallback ?? null) : this.props.children
  }
}
