// English for the whole interface. Screens are written in Portuguese; when the student chooses English, this translates
// what is on screen, the moment it appears, from a dictionary (en.ts). No screen has to know, so a Portuguese string in
// any component, or in a reply from the server, is covered by adding one line. A string with no entry stays in Portuguese
// rather than breaking: scripts/i18n-extract.mjs and its test list what is missing. See ADR-027.
import { language } from './ui'

const ATTRIBUTES = ['title', 'aria-label', 'placeholder', 'alt']
const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE'])

type Pattern = [RegExp, string]
let exact = new Map<string, string>()
let patterns: Pattern[] = []
const seen = new Map<string, string>()

// What the student calls the assistant. Same idea as the language: a browser copy for the first paint, and a change reloads.
const NAME_KEY = 'peace-assistant-name'
const DEFAULT_NAME = 'PEACE'
const usable = (name: string | null) => (name && !/\bPEACE\b/.test(name) ? name : DEFAULT_NAME) // containing the word would rewrite itself forever
function storedName() {
  try {
    return usable(localStorage.getItem(NAME_KEY))
  } catch {
    return DEFAULT_NAME
  }
}
const assistant = storedName()

export function setAssistantName(name: string) {
  if (usable(name) === assistant) return
  try {
    localStorage.setItem(NAME_KEY, name)
  } catch {
    return // storage blocked: this browser keeps the default rather than reloading for ever
  }
  window.location.reload()
}

export function named(text: string, name = assistant): string {
  if (name === DEFAULT_NAME || !text.includes(DEFAULT_NAME)) return text
  return text
    .replace(/\b[Dd]a PEACE\b/g, () => `de ${name}`)
    .replace(/\b[Nn]a PEACE\b/g, () => `em ${name}`)
    .replace(/\b[Aa] PEACE\b/g, () => name)
    .replace(/\bPEACE\b/g, () => name)
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** "{0} sessões" becomes a pattern; each hole is translated on its own ("há {0}" holding "3 dias"). */
export function load(dictionary: Record<string, string>) {
  exact = new Map(Object.entries(dictionary).filter(([k]) => !k.includes('{0}')))
  patterns = Object.entries(dictionary)
    .filter(([k]) => k.includes('{0}'))
    .sort((a, b) => b[0].length - a[0].length) // the most specific first
    .map(([k, v]) => [new RegExp('^' + k.split(/\{\d\}/).map(escape).join('(.+?)') + '$', 's'), v])
  seen.clear()
}

function phrase(text: string): string {
  const known = exact.get(text)
  if (known !== undefined) return known
  for (const [re, english] of patterns) {
    const m = re.exec(text)
    if (m) return english.replace(/\{(\d)\}/g, (_, n) => phrase(m[Number(n) + 1] ?? ''))
  }
  return text
}

export function translate(text: string): string {
  const core = text.trim()
  if (!core || !/\p{L}/u.test(core)) return text
  let out = seen.get(core)
  if (out === undefined) {
    out = named(phrase(core))
    seen.set(core, out)
  }
  return out === core ? text : text.replace(core, () => out)
}

function translateNode(node: Node) {
  if (node.nodeType === Node.TEXT_NODE) {
    if (node.parentElement && SKIP.has(node.parentElement.tagName)) return
    const next = translate(node.nodeValue ?? '')
    if (next !== node.nodeValue) node.nodeValue = next // React keeps the same node and just sets it again
    return
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return
  const el = node as Element
  if (SKIP.has(el.tagName)) return
  for (const name of ATTRIBUTES) {
    const value = el.getAttribute(name)
    if (value) {
      const next = translate(value)
      if (next !== value) el.setAttribute(name, next)
    }
  }
  node.childNodes.forEach(translateNode)
}

export async function startTranslation() {
  document.documentElement.lang = language() === 'en' ? 'en' : 'pt-PT'
  document.title = named(document.title)
  if (language() !== 'en' && assistant === DEFAULT_NAME) return
  if (language() === 'en') load((await import('./en')).EN)
  translateNode(document.body)
  // Runs before the browser paints, so the Portuguese is never seen.
  new MutationObserver((changes) => {
    for (const c of changes) {
      if (c.type === 'childList') c.addedNodes.forEach(translateNode)
      else if (c.type === 'characterData') translateNode(c.target)
      if (c.type === 'attributes') {
        const el = c.target as Element
        const value = el.getAttribute(c.attributeName!)
        if (value) {
          const next = translate(value)
          if (next !== value) el.setAttribute(c.attributeName!, next)
        }
      }
    }
  }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES })
}
