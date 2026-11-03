import { expect, test, type Page } from '@playwright/test'

const TOKEN = 'e2e-token-' + 'x'.repeat(32)
const AUTH = { Authorization: `Bearer ${TOKEN}` }

async function login(page: Page) {
  await page.goto('/entrar')
  await page.getByLabel('Token de acesso').fill(TOKEN)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL('/')
}

test('calendar: study of one subject separated by breaks is one block that lists its parts', async ({ page }) => {
  await login(page)
  const day = new Date()
  day.setHours(9, 0, 0, 0)
  const at = (minutes: number) => new Date(day.getTime() + minutes * 60_000).toISOString()
  const session = (id: number, from: number, to: number, topic: string) => ({
    id, task: null, event: { id: 77, title: 'AM1 (Frequência 1)' }, course_id: 1, starts_at: at(from), ends_at: at(to),
    status: 'planned', reason: '', materials: [], topic,
  })
  await page.route('**/api/v1/sessions?*', (route) => route.fulfill({
    json: [session(1, 0, 45, 'Limites'), session(2, 55, 100, 'Derivadas'), session(3, 110, 155, 'Integrais'), session(4, 165, 240, 'Séries')],
  }))
  await page.goto('/calendario')
  const block = page.getByRole('button', { name: /AM1 \(Frequência 1\)/ })
  await expect(block).toHaveCount(1)
  await block.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Partes')).toBeVisible()
  await expect(dialog.getByRole('listitem')).toHaveCount(4)
  await expect(dialog.getByText('Séries', { exact: false })).toBeVisible()
})

test('a double click on Criar creates one event', async ({ page }) => {
  await login(page)
  await page.goto('/calendario')
  await page.getByRole('button', { name: /Novo evento|Evento/ }).first().click()
  const title = `Duplo ${Date.now()}`
  await page.getByLabel('Título').fill(title)
  await page.route('**/api/v1/events', async (route) => {
    if (route.request().method() === 'POST') await new Promise((r) => setTimeout(r, 300)) // slow enough for the second click
    await route.continue()
  })
  await page.getByRole('button', { name: 'Criar' }).dblclick()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const week = await (await page.request.get(`/api/v1/events?start=${new Date(Date.now() - 864e5 * 30).toISOString()}&end=${new Date(Date.now() + 864e5 * 30).toISOString()}`, { headers: AUTH })).json()
  expect(week.filter((e: { title: string }) => e.title === title)).toHaveLength(1)
})

test('settings: voices appear without a desktop agent and play in the browser', async ({ page }) => {
  await login(page)
  let asked = ''
  await page.route('**/api/v1/assistant/status', async (route) => {
    const real = await (await route.fetch()).json()
    await route.fulfill({ json: { ...real, desktop: { ...real.desktop, connected: false }, voice: { ...real.voice, voices: ['F1', 'F2'] } } })
  })
  await page.route('**/api/v1/voice/say', (route) => {
    asked = route.request().postDataJSON().voice
    return route.fulfill({ status: 204 })
  })
  await page.goto('/definicoes?tab=voz')
  const tab = page.getByRole('tab', { name: 'Voz' })
  if (await tab.count()) await tab.click()
  await expect(page.getByLabel('Voz', { exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Ouvir a voz Voz 2' }).click()
  await expect.poll(() => asked).toBe('F2')
})

test('the assistant name replaces PEACE on screen and goes back', async ({ page }) => {
  await login(page)
  const current = await (await page.request.get('/api/v1/preferences/appearance', { headers: AUTH })).json()
  await page.request.put('/api/v1/preferences/appearance', { headers: AUTH, data: { ...current, assistant_name: 'Jarvis' } })
  try {
    await page.goto('/')
    await expect(page).toHaveTitle(/Jarvis/)
    await expect(page.getByRole('link', { name: 'Jarvis, início' })).toBeVisible()
  } finally {
    await page.request.put('/api/v1/preferences/appearance', { headers: AUTH, data: current })
    await page.evaluate(() => localStorage.removeItem('peace-assistant-name'))
  }
})
