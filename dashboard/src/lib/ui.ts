import { useEffect, useSyncExternalStore } from 'react'
import { api, type UiPrefs } from './api'

// Language and Simple/Advanced mode. The server keeps them (they follow the student across devices); this browser
// keeps a copy so the first paint is already right.
const KEY = 'peace-ui'
const DEFAULTS: UiPrefs = { language: 'pt', dock: null, features: {} }

function cached(): UiPrefs {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') }
  } catch {
    return DEFAULTS // storage blocked or damaged: the server's value arrives right after
  }
}

let current = cached()
const listeners = new Set<() => void>()

function publish(next: UiPrefs) {
  const reload = next.language !== current.language // dates and every label are set at load
  current = next
  try {
    localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* storage blocked: the server still has it */
  }
  listeners.forEach((l) => l())
  if (reload) window.location.reload()
}

export const language = () => current.language

export function useUi(): UiPrefs {
  return useSyncExternalStore((l) => (listeners.add(l), () => listeners.delete(l)), () => current)
}


/** Shows at once everywhere; goes back if the server refuses. A language change reloads, so it waits for the server. */
export async function saveUi(change: Partial<UiPrefs>) {
  const before = current
  const next = { ...current, ...change }
  if (next.language === before.language) publish(next)
  try {
    publish(await api.saveUiPrefs(next))
  } catch (e) {
    publish(before)
    throw e
  }
}

// Optional parts of PEACE. Some start off; a student's own choice always wins.
export type Feature = 'holo' | 'graph' | 'mail' | 'mastery' | 'timer' | 'practice' | 'mic'
const OFF_AT_FIRST: Feature[] = ['holo', 'graph', 'mail', 'mastery']

export const featureOn = (p: UiPrefs, f: Feature) => p.features[f] ?? !OFF_AT_FIRST.includes(f)
export const useFeature = (f: Feature) => featureOn(useUi(), f)

/** In the shell: what another device or tab chose wins over this browser's copy. */
export function useUiSync() {
  useEffect(() => {
    const load = () => {
      api.uiPrefs().then((p) => {
        if (JSON.stringify(p) !== JSON.stringify(current)) publish(p)
      }, () => undefined)
    }
    const otherTab = (e: StorageEvent) => {
      if (e.key === KEY) load()
    }
    load()
    window.addEventListener('focus', load) // changed on another device while this one was in the background
    window.addEventListener('storage', otherTab)
    return () => {
      window.removeEventListener('focus', load)
      window.removeEventListener('storage', otherTab)
    }
  }, [])
}
