import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react'

/** Ask every mounted view to reload (after a mutation). */
export const refreshAll = () => window.dispatchEvent(new Event('peace:refresh'))

/** Load data, then keep it fresh: on an interval, on window focus and after `refreshAll()`. */
export function useApi<T>(load: () => Promise<T>, deps: DependencyList, refreshMs = 60_000) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<Error>()
  const [loading, setLoading] = useState(true)
  const loader = useRef(load)
  useEffect(() => {
    loader.current = load
  })

  const latest = useRef(0) // only the newest request may set state (fast dep changes resolve out of order)
  const inFlight = useRef(false)
  const reload = useCallback(async () => {
    const id = ++latest.current
    inFlight.current = true
    try {
      const value = await loader.current()
      if (id !== latest.current) return
      setData(value)
      setError(undefined)
    } catch (e) {
      if (id === latest.current) setError(e as Error)
    } finally {
      if (id === latest.current) {
        inFlight.current = false
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    const onRefresh = () => void reload()
    // Interval ticks skip hidden tabs and never stack on a slow request.
    const onTick = () => !document.hidden && !inFlight.current && void reload()
    window.addEventListener('peace:refresh', onRefresh)
    window.addEventListener('focus', onRefresh)
    const timer = refreshMs ? window.setInterval(onTick, refreshMs) : undefined
    return () => {
      window.removeEventListener('peace:refresh', onRefresh)
      window.removeEventListener('focus', onRefresh)
      window.clearInterval(timer)
    }
  }, [reload, refreshMs])

  return { data, error, loading, reload, setData }
}

/** Current time, re-rendering every `ms` (countdowns, the "now" line). */
export function useNow(ms = 30_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), ms)
    return () => window.clearInterval(timer)
  }, [ms])
  return now
}
