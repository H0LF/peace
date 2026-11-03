import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { api, type AiChoice } from '../lib/api'
import { shortDay, time } from '../lib/format'
import { refreshAll, useApi } from '../lib/hooks'
import { Progress, cx, useToast } from './ui'

const REASON: Record<string, string> = {
  invalid_key: 'chave recusada',
  login: 'é preciso entrar outra vez',
  expired: 'o início de sessão expirou',
  quota: 'sem crédito',
  rate: 'demasiados pedidos',
  network: 'sem ligação',
  model: 'modelo indisponível',
  refused: 'pedido recusado',
}

export function reasonText(code: string | null | undefined) {
  return code ? (REASON[code] ?? code) : ''
}

const value = (c: { provider: string; model: string }) => `${c.provider}|${c.model}`

const windowName = (seconds: number) => (seconds >= 86_400 ? 'Semana' : `${Math.round(seconds / 3600)} horas`)

function renews(iso: string) {
  return new Date(iso).toDateString() === new Date().toDateString() ? time(iso) : `${shortDay(iso)} ${time(iso)}`
}

/** Small and out of the way, on the main page: which AI is answering, and a quick change. */
export function ModelSelector({ paused }: { paused?: string | null }) {
  const toast = useToast()
  const ai = useApi(() => api.ai(), [], 15_000)
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const plan = ai.data?.providers.find((p) => p.login && p.connected)?.id
  const usage = useApi(() => (plan ? api.providerUsage(plan) : Promise.resolve(null)), [plan], 60_000)
  useEffect(() => {
    if (!open) return
    const list = box.current
    ;(list?.querySelector<HTMLElement>('[aria-selected="true"]') ?? list?.querySelector<HTMLElement>('[role="option"]'))?.focus()
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false)
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  const data = ai.data
  if (!data) return null
  const { active, providers, choices } = data
  const groups = [
    { name: 'Neste PC', rows: choices.filter((c) => !c.provider) },
    ...providers.map((p) => ({ name: p.name, rows: choices.filter((c) => c.provider === p.id) })),
  ].filter((g) => g.rows.length)
  const shown = active.model || 'Sem modelo'
  const onSubscription = !!providers.find((p) => p.id === active.provider)?.login
  const windows = usage.data?.windows ?? []
  const left = windows.length ? 100 - Math.max(...windows.map((w) => w.used)) : null

  async function choose(choice: AiChoice) {
    setOpen(false)
    trigger.current?.focus()
    if (value(choice) === value(active)) return
    const provider = providers.find((p) => p.id === choice.provider)
    try {
      await api.setActiveModel(choice.provider, choice.model)
      ai.reload()
      refreshAll()
      toast(
        !provider
          ? `Agora: ${choice.model}, neste PC. Gratuito e privado.`
          : provider.login
            ? `Agora: ${choice.model}. Usa os limites da tua subscrição, e o que partilhas sai deste PC.`
            : `Agora: ${choice.model}. Usa o crédito da tua conta ${provider.name}, e o que partilhas sai deste PC.`,
        'ok',
      )
    } catch (e) {
      toast(`Não consegui mudar: ${reasonText((e as Error).message)}`, 'error')
    }
  }

  function moveFocus(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const rows = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="option"]')]
    const next = rows.indexOf(document.activeElement as HTMLElement) + (e.key === 'ArrowDown' ? 1 : -1)
    rows[(next + rows.length) % rows.length]?.focus()
  }

  const trouble = active.provider ? reasonText(active.error) : !active.reachable ? (paused === 'a carregar o modelo' ? 'a carregar…' : paused ? 'em pausa' : 'offline') : ''
  return (
    <div ref={box} className="relative z-40">
      <button
        ref={trigger}
        type="button"
        aria-label="Modelo da PEACE"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={active.provider ? (onSubscription ? 'Modelo na nuvem, com a tua subscrição' : 'Modelo na nuvem, com a tua chave') : paused ? `Em pausa: ${paused}. Volta sozinho quando houver memória.` : 'Modelo neste PC'}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-9 max-w-64 items-center gap-2 rounded-full border border-line bg-surface/60 px-3.5 text-xs hover:border-accent/50 focus-visible:border-accent"
      >
        <span className={cx('size-2 shrink-0 rounded-full', active.reachable ? 'bg-accent shadow-[0_0_6px_var(--glow)]' : 'bg-line-strong')} />
        <span className="truncate">{shown}</span>
        {trouble && <span className="shrink-0 text-faint">{trouble}</span>}
        {onSubscription && left !== null && !trouble && <span className={cx('num shrink-0', left <= 10 ? 'text-danger' : 'text-faint')}>{left}%</span>}
        <ChevronDown aria-hidden className={cx('size-3.5 shrink-0 text-muted transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label="Modelo da PEACE"
          onKeyDown={moveFocus}
          className="animate-rise absolute right-0 mt-1 max-h-[min(27rem,70vh)] w-64 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-line bg-surface-2 p-1 shadow-xl"
        >
          {groups.map((g) => (
            <div key={g.name} role="group" aria-label={g.name}>
              <p aria-hidden className="px-2.5 pt-2 pb-1 text-[11px] font-semibold tracking-wide text-faint uppercase">{g.name}</p>
              {g.rows.map((c) => {
                const selected = value(c) === value(active)
                return (
                  <button
                    key={value(c)}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => void choose(c)}
                    className={cx('flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-surface-3 focus:bg-surface-3 focus:outline-none', selected ? 'text-accent' : 'text-text')}
                  >
                    <span className="min-w-0 flex-1 truncate">{c.model}</span>
                    {selected && <Check aria-hidden className="size-3.5 shrink-0" />}
                  </button>
                )
              })}
            </div>
          ))}
          {windows.length > 0 && (
            <section aria-label="Limites do plano" className="mt-1 grid gap-2 border-t border-line px-2.5 pt-2 pb-1.5">
              <p className="text-[11px] font-semibold tracking-wide text-faint uppercase">Limites do plano</p>
              {windows.map((w) => (
                <div key={w.seconds} className="grid gap-1 text-xs">
                  <div className="flex justify-between gap-2">
                    <span>{windowName(w.seconds)}</span>
                    <span className="num text-muted">{`${100 - w.used}% restante`}</span>
                  </div>
                  <Progress value={100 - w.used} label={windowName(w.seconds)} />
                  <span className="text-[11px] text-faint">{`Renova ${renews(w.resets_at)}`}</span>
                </div>
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
