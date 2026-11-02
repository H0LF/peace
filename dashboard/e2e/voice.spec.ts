import { expect, test } from '@playwright/test'

const TOKEN = 'e2e-token-' + 'x'.repeat(32)

test('speaking in the browser: records, sends WAV, asks as voice, plays the reply', async ({ page }) => {
  let wav: Buffer | null = null
  let asked: { text: string; source: string } | undefined
  let said = ''
  await page.route('**/api/v1/voice/hear', (route) => {
    wav = route.request().postDataBuffer()
    return route.fulfill({ json: { text: 'o que tenho hoje?' } })
  })
  await page.route('**/api/v1/assistant/ask', (route) => {
    asked = route.request().postDataJSON()
    return route.fulfill({ json: { reply: 'Nada hoje.', intent: 'today', items: [] } })
  })
  await page.route('**/api/v1/voice/say', (route) => {
    said = route.request().postDataJSON().text
    return route.fulfill({ status: 204 })
  })
  await page.goto('/entrar#token=' + TOKEN)
  await expect(page).toHaveURL('/')

  await page.getByRole('button', { name: 'Falar' }).click()
  const stop = page.getByRole('button', { name: 'Parar e enviar' })
  await expect(stop).toBeVisible()
  await page.waitForTimeout(600)
  await stop.click()

  await expect(page.getByText('Nada hoje.')).toBeVisible()
  expect(wav!.subarray(0, 4).toString()).toBe('RIFF')
  expect(wav!.readUInt32LE(24)).toBe(16000) // the rate the speech services expect
  expect(wav!.length).toBeGreaterThan(44 + 16000 * 2 * 0.2) // real audio, not an empty header
  expect(asked).toEqual({ text: 'o que tenho hoje?', source: 'voice' })
  expect(said).toBe('Nada hoje.')
})

test('a typed question is not spoken', async ({ page }) => {
  let said = false
  await page.route('**/api/v1/voice/say', (route) => {
    said = true
    return route.fulfill({ status: 204 })
  })
  await page.route('**/api/v1/assistant/ask', (route) => route.fulfill({ json: { reply: 'Escrito.', intent: 'today', items: [] } }))
  await page.goto('/entrar#token=' + TOKEN)
  await page.getByLabel('Pergunta à PEACE').fill('olá')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByText('Escrito.')).toBeVisible()
  expect(said).toBe(false)
})

test('away from the PC, files open in the browser instead of Obsidian', async ({ page }) => {
  await page.goto('http://peace.test:8431/entrar#token=' + TOKEN)
  await expect(page).toHaveURL('http://peace.test:8431/')
  await page.goto('http://peace.test:8431/ficheiros')
  const file = page.getByRole('link', { name: 'Abrir Slides: Introdução', exact: true }).first()
  await expect(file).toHaveAttribute('href', /^\/api\/v1\/materials\/\d+\/file$/)
  await expect(file).toHaveAttribute('target', '_blank')
  await expect(page.locator('a[href^="obsidian://"]')).toHaveCount(0)
})
