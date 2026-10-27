import { useEffect, useState } from 'react'

// Theme (dark/light). index.html applies the stored value before first paint; this keeps every
// control that shows it (Settings, the cockpit's top bar) in sync.
export type Theme = 'dark' | 'light'
const THEME_KEY = 'peace-theme'

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* private mode: the theme just won't persist */
  }
  window.dispatchEvent(new Event('peace:theme'))
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const read = (): Theme => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
  const [theme, set] = useState<Theme>(read)
  useEffect(() => {
    const sync = () => set(read())
    window.addEventListener('peace:theme', sync)
    return () => window.removeEventListener('peace:theme', sync)
  }, [])
  return [theme, setTheme]
}

// --- accent colour ------------------------------------------------------------------------------
// The user picks one colour; each theme gets a variant with readable contrast (WCAG AA, 4.5:1) and the
// hologram (orb, graph, HUD lines) gets a bright, a pale and a dim tone. index.html re-applies the
// cached CSS before first paint.

export const DEFAULT_ACCENT = '#ffb547'
const ACCENT_KEY = 'peace-accent-css'
const DARK_SURFACE = '#12100d'

type RGB = [number, number, number]
const toRgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB
const toHex = (rgb: RGB) => '#' + rgb.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')
export const isHex = (s: string) => /^#[0-9a-f]{6}$/i.test(s)

export function mix(a: string, b: string, t: number): string {
  const [x, y] = [toRgb(a), toRgb(b)]
  return toHex([0, 1, 2].map((i) => x[i] + (y[i] - x[i]) * t) as RGB)
}

function luminance(hex: string): number {
  const [r, g, b] = toRgb(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrast(a: string, b: string): number {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (l1 + 0.05) / (l2 + 0.05)
}

/** Move `hex` towards `target` (white or black) until it reaches `min` contrast against `bg`. */
export function readable(hex: string, bg: string, target: string, min = 4.5): string {
  for (let t = 0; t <= 1; t += 0.02) {
    const c = mix(hex, target, t)
    if (contrast(c, bg) >= min) return c
  }
  return target
}

export function accentVariants(hex: string) {
  const dark = readable(hex, DARK_SURFACE, '#ffffff')
  const light = readable(hex, '#ffffff', '#000000')
  const ink = (c: string) => (contrast(c, '#000000') >= contrast(c, '#ffffff') ? '#140d02' : '#ffffff')
  return { dark, light, darkInk: ink(dark), lightInk: ink(light), holo: dark, holoPale: mix(dark, '#ffffff', 0.45), holoDim: mix(dark, '#000000', 0.35) }
}

export function accentCss(hex: string): string {
  const v = accentVariants(hex)
  const rgb = (c: string) => toRgb(c).join(' ')
  // Lines and borders carry the accent too (gold's are 10% and 22% of it over the dark surface).
  const darkHolo = `--holo: ${v.holo}; --holo-pale: ${v.holoPale}; --holo-dim: ${v.holoDim}; --line: ${mix(v.dark, DARK_SURFACE, 0.9)}; --line-strong: ${mix(v.dark, DARK_SURFACE, 0.78)};`
  const lightHolo = `--holo: ${v.light}; --holo-pale: ${mix(v.light, '#ffffff', 0.2)}; --holo-dim: ${mix(v.light, '#000000', 0.2)};`
  return (
    `html[data-theme="dark"] { --accent: ${v.dark}; --accent-soft: rgb(${rgb(v.dark)} / 0.12); --accent-ink: ${v.darkInk}; --glow: rgb(${rgb(v.dark)} / 0.35); ${darkHolo} }\n` +
    `html[data-theme="light"] { --accent: ${v.light}; --accent-soft: rgb(${rgb(v.light)} / 0.1); --accent-ink: ${v.lightInk}; --glow: transparent; ${lightHolo} }`
  )
}

/** Apply an accent everywhere (CSS variables, then the WebGL scenes via the `peace:accent` event).
 *  `persist` caches it for the next first paint; a live preview passes false. */
export function applyAccent(hex: string, persist = true) {
  const css = accentCss(isHex(hex) ? hex : DEFAULT_ACCENT)
  let tag = document.getElementById('peace-accent')
  if (!tag) {
    tag = document.createElement('style')
    tag.id = 'peace-accent'
    document.head.append(tag)
  }
  tag.textContent = css
  if (persist) {
    try {
      localStorage.setItem(ACCENT_KEY, css)
    } catch {
      /* not cached: the default gold shows for a moment on the next load */
    }
  }
  window.dispatchEvent(new Event('peace:accent'))
}

/** Re-renders when the accent changes (for code that reads colours in JS, e.g. three.js). */
export function useAccentVersion(): number {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1)
    window.addEventListener('peace:accent', bump)
    return () => window.removeEventListener('peace:accent', bump)
  }, [])
  return version
}

/** A CSS colour variable resolved to the value three.js needs. */
export const cssVar = (name: string, fallback: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback

export const withAlpha = (hex: string, alpha: number) => `rgba(${toRgb(isHex(hex) ? hex : DEFAULT_ACCENT).join(',')},${alpha})`
