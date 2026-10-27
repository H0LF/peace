import {
  Activity,
  BookOpen,
  CalendarDays,
  FolderOpen,
  GraduationCap,
  Hourglass,
  House,
  Keyboard,
  LayoutList,
  Menu as MenuIcon,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings,
  SquareCheckBig,
  X,
} from 'lucide-react'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { api } from '../lib/api'
import { ago } from '../lib/format'
import { useApi, useNow } from '../lib/hooks'
import { applyAccent } from '../lib/theme'
import { healthTone, SOURCE_NAME } from './sync'
import { TaskDialog } from './tasks'
import { Button, Dialog, Kbd, cx } from './ui'

// `rail`: shown as an icon in the narrow sidebar; the rest live in the drawer, the shortcuts and the pages.
const NAV = [
  { to: '/', label: 'Início', icon: House, key: 'h', rail: true },
  { to: '/calendario', label: 'Calendário', icon: CalendarDays, key: 'c', rail: true },
  { to: '/painel', label: 'Painel', icon: LayoutList, key: 'd', rail: true },
  { to: '/estudo', label: 'Estudo', icon: GraduationCap, key: 'e', rail: true },
  { to: '/tarefas', label: 'Tarefas', icon: SquareCheckBig, key: 't', rail: true },
  { to: '/conhecimento', label: 'Conhecimento', icon: Network, key: 'k', rail: true },
  { to: '/ficheiros', label: 'Ficheiros', icon: FolderOpen, key: 'f', rail: true },
  { to: '/prazos', label: 'Prazos', icon: Hourglass, key: 'p', rail: false },
  { to: '/cadeiras', label: 'Cadeiras', icon: BookOpen, key: 'u', rail: false },
  { to: '/atividade', label: 'Atividade', icon: Activity, key: 'a', rail: false },
  { to: '/definicoes', label: 'Definições', icon: Settings, key: 's', rail: true },
]
const RAIL_KEY = 'peace-rail'

const typing = (e: KeyboardEvent) => {
  const el = e.target as HTMLElement
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || !!document.querySelector('dialog[open]')
}

function readRail(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) !== 'hidden'
  } catch {
    return true
  }
}

const Orb = lazy(() => import('./Orb'))

/** The same scene as the cockpit, rendered once at a lower particle density. */
export function OrbMark({ className }: { className?: string }) {
  return <Suspense fallback={<img src="/icon.svg" alt="" className={className} />}><Orb compact className={className} /></Suspense>
}

function syncSummary(data: Awaited<ReturnType<typeof api.syncStatus>>, now: Date) {
  const worst = data.some((s) => healthTone(s) === 'danger') ? 'danger' : data.some((s) => healthTone(s) === 'warn') ? 'warn' : 'ok'
  const last = data.map((s) => s.last_success_at).filter(Boolean).sort().at(0)
  const text =
    worst === 'danger'
      ? `Falha: ${data.filter((s) => healthTone(s) === 'danger').map((s) => SOURCE_NAME[s.source]).join(', ')}`
      : last
        ? `Sincronizado ${ago(last, now)}`
        : 'Ainda não sincronizado'
  return { worst, text }
}

function SyncDot({ withText = false }: { withText?: boolean }) {
  const now = useNow(60_000)
  const { data } = useApi(() => api.syncStatus(), [], 120_000)
  if (!data) return null
  const { worst, text } = syncSummary(data, now)
  return (
    <NavLink
      to="/definicoes"
      title={text}
      aria-label={text}
      className={cx('flex items-center gap-2.5 rounded-lg text-xs text-muted hover:bg-surface-2 hover:text-text', withText ? 'px-2.5 py-2' : 'size-10 justify-center')}
    >
      <span className={cx('size-2 rounded-full', worst === 'ok' ? 'bg-ok' : worst === 'warn' ? 'bg-warn' : 'bg-danger')} />
      {withText && <span className="min-w-0 truncate">{text}</span>}
    </NavLink>
  )
}

export function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const [newTask, setNewTask] = useState(false)
  const [help, setHelp] = useState(false)
  const [rail, setRail] = useState(readRail)
  const cockpit = location.pathname === '/'

  useEffect(() => setMobileOpen(false), [location.pathname])
  // The saved accent (index.html already painted the cached one; this catches changes from another device).
  useEffect(() => {
    api.appearance().then((a) => applyAccent(a.accent), () => undefined)
  }, [])

  const railToggle = useRef<HTMLButtonElement>(null) // focus follows the toggle across hide/show
  const railMoved = useRef(false)
  useEffect(() => {
    if (railMoved.current) railToggle.current?.focus()
  }, [rail])
  const toggleRail = () =>
    setRail((open) => {
      railMoved.current = true
      try {
        localStorage.setItem(RAIL_KEY, open ? 'hidden' : 'shown')
      } catch {
        /* storage blocked: the choice lasts until reload */
      }
      return !open
    })

  useEffect(() => {
    const onUnauthorized = () => navigate('/entrar', { replace: true })
    window.addEventListener('peace:unauthorized', onUnauthorized)
    return () => window.removeEventListener('peace:unauthorized', onUnauthorized)
  }, [navigate])

  useEffect(() => {
    let pendingG = 0
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e)) return
      if (pendingG && Date.now() - pendingG < 1200) {
        const item = NAV.find((n) => n.key === e.key.toLowerCase())
        pendingG = 0
        if (item) {
          e.preventDefault()
          navigate(item.to)
        }
        return
      }
      if (e.key === 'g') pendingG = Date.now()
      else if (e.key === 'n') {
        e.preventDefault()
        setNewTask(true)
      } else if (e.key === '?') setHelp(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  const drawerNav = (
    <nav aria-label="Principal" className="grid gap-0.5">
      {NAV.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) =>
            cx(
              'flex h-9 items-center gap-3 rounded-lg px-2.5 text-[13px] font-medium transition-colors',
              isActive ? 'bg-surface-2 text-text' : 'text-muted hover:bg-surface-2/60 hover:text-text',
            )
          }
        >
          {({ isActive }) => (
            <>
              <Icon className={cx('size-4', isActive ? 'text-accent' : 'text-faint')} />
              {label}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )

  const brand = (
    <div className="flex items-center gap-2.5 px-2.5">
      <OrbMark className="size-7" />
      <div className="font-display text-[14px] font-semibold tracking-[0.2em]">PEACE</div>
    </div>
  )

  return (
    <div className="flex min-h-dvh">
      {rail && (
        <aside className="sticky top-0 hidden h-dvh w-[76px] shrink-0 flex-col items-center gap-5 border-r border-line/70 py-5 lg:flex">
          <NavLink to="/" aria-label="PEACE, início" className="grid justify-items-center gap-1.5">
            <OrbMark className="size-11" />
            <span className="font-display text-[10px] font-semibold tracking-[0.25em] text-muted">PEACE</span>
          </NavLink>
          <nav aria-label="Principal" className="mt-3 grid gap-2">
            {NAV.filter((n) => n.rail).map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                title={label}
                aria-label={label}
                className={({ isActive }) =>
                  cx(
                    'grid size-11 place-items-center rounded-xl border transition-colors',
                    isActive ? 'rail-active border-accent/30 bg-accent-soft text-accent' : 'border-transparent text-faint hover:bg-surface-2 hover:text-text',
                  )
                }
              >
                <Icon className="size-[19px]" strokeWidth={1.75} />
              </NavLink>
            ))}
          </nav>
          <div className="mt-auto grid justify-items-center gap-1">
            <button type="button" title="Nova tarefa (n)" aria-label="Nova tarefa" onClick={() => setNewTask(true)} className="grid size-10 place-items-center rounded-lg text-faint hover:bg-surface-2 hover:text-text">
              <Plus className="size-4" />
            </button>
            <SyncDot />
            <button type="button" title="Atalhos de teclado (?)" aria-label="Atalhos de teclado" onClick={() => setHelp(true)} className="grid size-10 place-items-center rounded-lg text-faint hover:bg-surface-2 hover:text-text">
              <Keyboard className="size-4" />
            </button>
            <button ref={railToggle} type="button" title="Esconder barra lateral" aria-label="Esconder barra lateral" onClick={toggleRail} className="grid size-10 place-items-center rounded-lg text-faint hover:bg-surface-2 hover:text-text">
              <PanelLeftClose className="size-4" />
            </button>
          </div>
        </aside>
      )}
      {!rail && (
        <button
          ref={railToggle}
          type="button"
          aria-label="Mostrar barra lateral"
          title="Mostrar barra lateral"
          onClick={toggleRail}
          className="fixed top-4 left-4 z-30 hidden size-10 place-items-center rounded-xl border border-line bg-surface/80 text-muted backdrop-blur hover:text-text lg:grid"
        >
          <PanelLeftOpen className="size-4" />
        </button>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur lg:hidden">
          <Button variant="ghost" size="icon" aria-label="Abrir menu" onClick={() => setMobileOpen(true)}>
            <MenuIcon className="size-5" />
          </Button>
          {brand}
          <Button variant="ghost" size="icon" className="ml-auto" aria-label="Nova tarefa" onClick={() => setNewTask(true)}>
            <Plus className="size-5" />
          </Button>
        </header>
        {mobileOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
            <div className="animate-rise absolute inset-y-0 left-0 flex w-72 flex-col gap-6 border-r border-line bg-bg px-3 py-5">
              <div className="flex items-center justify-between">
                {brand}
                <Button variant="ghost" size="icon" aria-label="Fechar menu" onClick={() => setMobileOpen(false)}>
                  <X className="size-5" />
                </Button>
              </div>
              {drawerNav}
              <div className="mt-auto">
                <SyncDot withText />
              </div>
            </div>
          </div>
        )}
        <main className={cx('w-full', cockpit ? 'min-h-0 flex-1' : 'mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-10 lg:py-8')}>
          <Outlet />
        </main>
      </div>

      <TaskDialog open={newTask} onClose={() => setNewTask(false)} />
      <Dialog open={help} onClose={() => setHelp(false)} title="Atalhos de teclado">
        <ul className="grid gap-2 text-[13px]">
          {NAV.map((n) => (
            <li key={n.key} className="flex items-center justify-between">
              <span className="text-muted">{n.label}</span>
              <span className="flex gap-1">
                <Kbd>g</Kbd>
                <Kbd>{n.key}</Kbd>
              </span>
            </li>
          ))}
          <li className="flex items-center justify-between">
            <span className="text-muted">Nova tarefa</span>
            <Kbd>n</Kbd>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-muted">Procurar (em Tarefas)</span>
            <Kbd>/</Kbd>
          </li>
        </ul>
      </Dialog>
    </div>
  )
}
