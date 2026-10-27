import { expect, test, type Page } from '@playwright/test'
import { accentCss } from '../src/lib/theme'

// Uses the real test API; appearance and voice-state events are controlled for this rendering matrix.
const colors = { gold: '#ffb547', purple: '#a78bfa', cyan: '#6fd3ff', red: '#ff3333', green: '#4ade80' }
async function login(page: Page) {
  await page.goto('/entrar')
  await page.getByLabel('Token de acesso').fill('e2e-token-' + 'x'.repeat(32))
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL('/')
}

for (const theme of ['dark', 'light'] as const) {
  for (const [name, accent] of Object.entries(colors)) {
    test(`${theme} ${name}: canonical branding, transparent orb and themed graph`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.setViewportSize({ width: 1709, height: 920 })
      await page.addInitScript(({ theme, css }) => {
        localStorage.setItem('peace-theme', theme)
        localStorage.setItem('peace-accent-css', css)
      }, { theme, css: accentCss(accent) })
      await page.route('**/api/v1/preferences/appearance', (route) => route.fulfill({ json: { accent, name: 'Ana' } }))
      await page.goto('/entrar')
      await expect(page.locator('.orb[data-quality="mark"] canvas')).toHaveCount(1)
      await page.screenshot({ path: `test-results/visual-${theme}-${name}-login.png` })
      await login(page)
      const hero = page.locator('.orb-hero')
      await expect(hero.locator('canvas')).toHaveCount(1)
      await expect(page.locator('aside .orb[data-quality="mark"] canvas').first()).toBeVisible()
      expect(await hero.evaluate((el) => {
        const style = getComputedStyle(el)
        return [style.backgroundColor, style.backgroundImage]
      })).toEqual(['rgba(0, 0, 0, 0)', 'none'])
      const canvas = await hero.locator('canvas').elementHandle()
      // A theme switch changes materials in the same renderer, without adding contexts.
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme === 'light' ? 'dark' : 'light'
        window.dispatchEvent(new Event('peace:theme'))
      }, theme)
      expect(await canvas!.evaluate((el) => el === document.querySelector('.orb-hero canvas'))).toBe(true)
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme
        window.dispatchEvent(new Event('peace:theme'))
      }, theme)
      await page.screenshot({ path: `test-results/visual-${theme}-${name}-cockpit.png` })
      await page.goto('/painel')
      await expect(page.locator('main .orb canvas')).toHaveCount(1)
      await expect(page.locator('main .orb')).toHaveCSS('background-image', 'none')
      await page.screenshot({ path: `test-results/visual-${theme}-${name}-dashboard.png`, fullPage: true })
      await page.goto('/conhecimento')
      await expect(page.getByTestId('knowledge-graph').locator('canvas')).toHaveCount(1)
      expect(await page.getByTestId('knowledge-graph').evaluate((el) => getComputedStyle(el.parentElement!).backgroundColor))
        .toBe(theme === 'light' ? 'rgb(245, 245, 247)' : 'rgb(9, 11, 13)')
      await page.getByLabel('Procurar no grafo').fill('slides')
      await page.getByRole('list', { name: 'Resultados' }).getByRole('button', { name: /Slides: Introdução/ }).click()
      await expect(page.getByRole('heading', { name: 'Slides: Introdução' })).toBeVisible()
      await page.screenshot({ path: `test-results/visual-${theme}-${name}-graph.png`, fullPage: true })
    })
  }
}

test('orb handles status events in every state with reduced motion on a small screen', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 390, height: 844 })
  let state = 'idle'
  await page.route('**/api/v1/assistant/status', async (route) => {
    const response = await route.fetch()
    await route.fulfill({ json: { ...await response.json(), state } })
  })
  await login(page)
  await expect(page.locator('.orb-hero canvas')).toHaveCount(1)
  const canvas = await page.locator('.orb-hero canvas').elementHandle()
  for (const next of ['idle', 'listening', 'thinking', 'speaking', 'idle']) {
    state = next
    await expect(page.locator('.orb-hero')).toHaveAttribute('data-state', next)
    expect(await canvas!.evaluate((el) => el === document.querySelector('.orb-hero canvas'))).toBe(true)
    await page.screenshot({ path: `test-results/visual-mobile-${next}.png`, fullPage: true })
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
