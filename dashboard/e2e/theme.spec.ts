import { expect, test } from '@playwright/test'
import { accentCss, accentVariants, contrast, mix, readable } from '../src/lib/theme'

// Pure colour checks (no browser needed).
const DARK_PANEL = '#12100d'

test('contrast follows WCAG', () => {
  expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 0)
  expect(contrast('#ffffff', '#ffffff')).toBe(1)
  expect(mix('#000000', '#ffffff', 0.5)).toBe('#808080')
})

test('any accent stays readable in both themes', () => {
  for (const hex of ['#ffb547', '#0000ff', '#ffff00', '#111111', '#ffffff', '#6fd3ff', '#a78bfa']) {
    const v = accentVariants(hex)
    expect(contrast(v.dark, DARK_PANEL), `${hex} dark`).toBeGreaterThanOrEqual(4.5)
    expect(contrast(v.light, '#ffffff'), `${hex} light`).toBeGreaterThanOrEqual(4.5)
    expect(contrast(v.darkInk, v.dark), `${hex} ink`).toBeGreaterThanOrEqual(4.5)
  }
  expect(accentVariants('#ffb547').dark).toBe('#ffb547') // gold needs no change on dark
  expect(readable('#ffff00', '#ffffff', '#000000')).not.toBe('#ffff00') // yellow on white is darkened
})

test('accent CSS beats the stylesheet regardless of order', () => {
  const css = accentCss('#6fd3ff')
  expect(css).toContain('html[data-theme="dark"] { --accent: #6fd3ff;')
  expect(css).toContain('html[data-theme="light"]')
  expect(css).toContain('--holo: #6fd3ff;')
  expect(css).toContain(`--line: ${mix('#6fd3ff', '#12100d', 0.9)};`) // cyan lines, not gold
})
