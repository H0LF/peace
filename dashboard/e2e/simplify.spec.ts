import { expect, test, type Page } from '@playwright/test'

// The default experience, Settings tabs, English, the home model selector, study plans and the course file viewer.
test.describe.configure({ mode: 'serial' })
const TOKEN = 'e2e-token-' + 'x'.repeat(32)
const API_KEY = 'sk-e2e-' + 'k'.repeat(30)

async function login(page: Page) {
  await page.goto('/entrar#token=' + TOKEN)
  await expect(page).toHaveURL('/')
}
const FEATURES = { holo: true, graph: true, mail: true, mastery: true }
const DOCK = ['/', '/calendario', '/estudo', '/tarefas', '/conhecimento', '/ficheiros', '/prazos', '/cadeiras', '/definicoes', 'tool:all', 'tool:new', 'tool:sync', 'tool:holo', 'tool:shortcuts', 'tool:hide']
const putUi = (page: Page, data: object) => page.request.put('/api/v1/preferences/ui', { data, headers: { Authorization: `Bearer ${TOKEN}` } })
/** The seed's settings, in a language. */
const setUi = (page: Page, language: string) => putUi(page, { language, features: FEATURES, dock: DOCK })
/** What a new student has. */
const fresh = (page: Page) => putUi(page, { language: 'pt', features: {}, dock: null })

test('a new student sees everyday pages on the bar; every page stays in the list, and there is no mode to choose', async ({ page }) => {
  await login(page)
  await fresh(page)
  await page.goto('/definicoes')
  const dock = page.getByRole('navigation', { name: 'Principal' }).first()
  await expect(dock.getByRole('link', { name: 'Cadeiras' })).toBeVisible()
  await expect(dock.getByRole('link', { name: 'Graphify' })).toHaveCount(0)
  await expect(dock.getByRole('link', { name: 'Tarefas' })).toHaveCount(0)
  await expect(dock.getByRole('link', { name: 'Ficheiros' })).toHaveCount(0)
  await expect(page.getByRole('tablist', { name: 'Modo' })).toHaveCount(0)
  const sections = page.getByRole('tablist', { name: 'Secções das definições' })
  await sections.getByRole('tab', { name: 'Avançado' }).click()
  await expect(page.getByRole('heading', { name: 'Diagnóstico' })).toBeVisible()
  await dock.getByRole('button', { name: 'Todas as páginas' }).click()
  await page.getByRole('link', { name: 'Tarefas' }).first().click()
  await expect(page).toHaveURL('/tarefas')
  await page.goto('/correio') // off at first, never locked
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
})

test('settings are in tabs, one section at a time, and a link opens the right one', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes')
  await expect(page.getByRole('heading', { name: 'Geral' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Voz' })).toHaveCount(0)
  await page.getByRole('tab', { name: 'Voz' }).click()
  await expect(page).toHaveURL(/separador=voz/)
  await expect(page.getByRole('heading', { name: 'Voz' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Geral' })).toHaveCount(0)
  await page.goto('/definicoes?separador=nao-existe')
  await expect(page.getByRole('heading', { name: 'Geral' })).toBeVisible()
  await page.goto('/definicoes?separador=privacidade')
  await expect(page.getByRole('checkbox', { name: 'Email' })).not.toBeChecked() // mail stays private by default
  await expect(page.getByRole('checkbox', { name: 'Conversa' })).toBeChecked()
})

test('one form for every provider: choose, connect, pick a model; the key is never shown back', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=ia')
  const provider = page.getByLabel('Fornecedor')
  await expect(provider).toHaveValue('')
  await expect(page.getByLabel('Chave de API')).toHaveCount(0) // the model on this PC needs no key
  await expect(page.getByRole('combobox', { name: 'Modelo', exact: true })).toBeVisible()

  await provider.selectOption('openai')
  await expect(page.getByText(/ChatGPT Plus ou Pro não inclui crédito/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Obter uma chave' })).toHaveAttribute('href', /openai\.com/)
  const key = page.getByLabel('Chave de API')
  await expect(key).toHaveAttribute('type', 'password')
  await expect(page.getByRole('combobox', { name: 'Modelo', exact: true })).toHaveCount(0)

  await page.route('**/api/v1/ai/providers/openai/key', (r) => r.fulfill({ status: 400, contentType: 'application/json', body: '{"detail":"invalid_key"}' }))
  await key.fill(API_KEY)
  await page.getByRole('button', { name: 'Ligar' }).click()
  await expect(page.getByText('Não consegui ligar: chave recusada')).toBeVisible()
  await page.unroute('**/api/v1/ai/providers/openai/key')

  const ai = await (await page.request.get('/api/v1/ai', { headers: { Authorization: `Bearer ${TOKEN}` } })).json()
  const connected = { ...ai, providers: ai.providers.map((p: { id: string }) => (p.id === 'openai' ? { ...p, connected: true } : p)) }
  let chosen: unknown
  await page.route('**/api/v1/ai/providers/openai/key', (r) => r.fulfill({ json: connected }))
  await page.route('**/api/v1/ai/providers/openai/models', (r) => r.fulfill({ json: [{ id: 'gpt-e2e', name: 'gpt-e2e', price: 3 }] }))
  await page.route('**/api/v1/ai/active', (r) => {
    chosen = r.request().postDataJSON()
    r.fulfill({ json: connected })
  })
  await page.route('**/api/v1/ai', (r) => r.fulfill({ json: connected }))
  await page.getByRole('button', { name: 'Ligar' }).click()
  await expect(page.getByText('OpenAI ligado')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Remover a chave' })).toBeVisible()
  expect(await page.content()).not.toContain(API_KEY)
  const model = page.getByRole('combobox', { name: 'Modelo', exact: true })
  await expect(model.getByRole('option', { name: /gpt-e2e/ })).toHaveCount(1)
  await model.selectOption('gpt-e2e')
  await expect(page.getByText('Agora: gpt-e2e')).toBeVisible()
  expect(chosen).toEqual({ provider: 'openai', model: 'gpt-e2e' })
})

test('any page can go on the bar', async ({ page }) => {
  await login(page)
  await fresh(page)
  await page.goto('/definicoes?separador=funcionalidades')
  const bar = page.getByRole('navigation', { name: 'Principal' }).first()
  await expect(bar.getByRole('link', { name: 'Tarefas' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Pôr Tarefas na barra' }).click()
  await expect(bar.getByRole('link', { name: 'Tarefas' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Pôr Tarefas na barra' })).toHaveCount(0)
})

test('PEACE changes things on its own until the student turns that off; Rever is not offered for the bar', async ({ page }) => {
  await login(page)
  await fresh(page)
  await page.goto('/definicoes?separador=funcionalidades')
  const auto = page.getByRole('checkbox', { name: 'Deixar a PEACE fazer as mudanças sozinha' })
  await expect(auto).toBeChecked()
  await auto.click()
  await expect(auto).not.toBeChecked()
  await page.reload()
  await expect(auto).not.toBeChecked()
  await auto.click()
  await expect(auto).toBeChecked()
  await expect(page.getByRole('button', { name: 'Pôr Rever na barra' })).toHaveCount(0)
  await page.getByRole('link', { name: 'Ver o que a PEACE mudou' }).click()
  await expect(page).toHaveURL('/rever')
})

test('the start page says when a decision is waiting, and only then', async ({ page }) => {
  await login(page)
  let rows: unknown[] = [{ id: 1, kind: 'mail_cleanup', status: 'pending', title: 'Limpar', reason: '', evidence: [], item: null, preview: { removed: [], created: [], error: null } }]
  await page.route('**/api/v1/proposals?*', (route) => route.fulfill({ json: rows }))
  await page.goto('/')
  const note = page.getByRole('link', { name: 'A PEACE precisa de uma decisão tua' })
  await expect(note).toHaveAttribute('href', '/rever')
  rows = []
  await page.reload()
  await expect(page.getByRole('button', { name: 'Prazos da semana' })).toBeVisible()
  await expect(note).toHaveCount(0)
})

test('Prazos is the one list: tick a task off there, tasks with no date at the end', async ({ page }) => {
  await login(page)
  const headers = { Authorization: `Bearer ${TOKEN}` }
  const undated = await (await page.request.post('/api/v1/tasks', { data: { title: 'Sem prazo nenhum' }, headers })).json()
  const dated = await (await page.request.post('/api/v1/tasks', { data: { title: 'Com prazo amanhã', due_at: new Date(Date.now() + 86_400_000).toISOString() }, headers })).json()
  await page.goto('/prazos')
  await expect(page.getByText('Sem prazo nenhum')).toBeVisible()
  await page.getByRole('checkbox', { name: 'Concluir: Com prazo amanhã' }).click()
  await expect(page.getByRole('checkbox', { name: 'Concluir: Com prazo amanhã' })).toHaveCount(0)
  for (const t of [undated, dated]) await page.request.delete(`/api/v1/tasks/${t.id}`, { headers })
})

test('the sync status always closes the bar, whatever order was saved', async ({ page }) => {
  await login(page)
  await page.request.put('/api/v1/preferences/ui', { data: { language: 'pt', dock: ['tool:sync', '/', 'tool:new'], features: {} }, headers: { Authorization: `Bearer ${TOKEN}` } })
  await page.goto('/definicoes?separador=funcionalidades')
  const labels = page.getByRole('navigation', { name: 'Principal' }).first().locator('a, button')
  await expect(labels).toHaveCount(3)
  await expect(labels.nth(0)).toHaveAttribute('aria-label', 'Início')
  await expect(labels.nth(1)).toHaveAttribute('aria-label', 'Nova tarefa')
  await expect(page.getByRole('button', { name: 'Subir Estado da sincronização' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Descer Nova tarefa' })).toBeDisabled()
})

test('the bar: hide, add and reorder; it shows at once, stays after reloading, and Settings stays reachable', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=funcionalidades')
  const bar = page.getByRole('navigation', { name: 'Principal' }).first()
  const list = page.getByRole('list', { name: 'Na barra' })
  await expect(bar.getByRole('link', { name: 'Tarefas' })).toBeVisible()

  await page.getByRole('button', { name: 'Tirar Tarefas da barra' }).click()
  await expect(bar.getByRole('link', { name: 'Tarefas' })).toHaveCount(0) // at once, no reload
  await page.getByRole('button', { name: 'Pôr Correio na barra' }).click()
  await expect(bar.getByRole('link', { name: 'Correio' })).toBeVisible()

  const labels = () => bar.locator('a, button').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')))
  await page.getByRole('button', { name: 'Descer Início' }).click()
  await expect.poll(async () => (await labels()).indexOf('Início')).toBe(1)
  await list.getByText('Correio').dragTo(list.getByText('Calendário'))
  await expect.poll(async () => (await labels()).indexOf('Correio')).toBeLessThan((await labels()).indexOf('Estudo'))

  await page.getByRole('button', { name: 'Tirar Todas as páginas da barra' }).click()
  await expect(page.getByRole('button', { name: 'Tirar Definições da barra' })).toBeDisabled()

  await page.reload()
  await expect(bar.getByRole('link', { name: 'Correio' })).toBeVisible()
  await expect(bar.getByRole('link', { name: 'Tarefas' })).toHaveCount(0)
  await expect.poll(async () => (await labels()).indexOf('Correio')).toBeLessThan((await labels()).indexOf('Início'))

  await page.goto('/tarefas') // hidden from the bar, still there
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.goto('/definicoes?separador=funcionalidades')
  await page.getByRole('button', { name: 'Repor' }).click()
  await expect(bar.getByRole('link', { name: 'Cadeiras' })).toBeVisible() // back to the default bar
  await expect(bar.getByRole('link', { name: 'Tarefas' })).toHaveCount(0)
  await expect(bar.getByRole('link', { name: 'Correio' })).toHaveCount(0)
})

test('optional features: Holo Hands starts off and can be chosen; off means gone everywhere, data untouched', async ({ page }) => {
  await login(page)
  await fresh(page)
  await page.goto('/definicoes?separador=funcionalidades')
  const bar = page.locator('aside').first()
  const holo = page.getByRole('checkbox', { name: 'Holo Hands' })
  await expect(holo).not.toBeChecked()
  await expect(bar.getByRole('button', { name: 'Holo Hands' })).toHaveCount(0)
  await holo.check()
  await expect(bar.getByRole('button', { name: 'Holo Hands' })).toBeVisible()
  await page.reload()
  await expect(bar.getByRole('button', { name: 'Holo Hands' })).toBeVisible()
  await page.getByRole('checkbox', { name: 'Holo Hands' }).uncheck()
  await expect(bar.getByRole('button', { name: 'Holo Hands' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Tirar Holo Hands da barra' })).toHaveCount(0)

  await page.getByRole('checkbox', { name: 'Microfone no Início' }).uncheck()
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Falar' })).toHaveCount(0)
  await page.goto('/definicoes?separador=funcionalidades')
  await page.getByRole('checkbox', { name: 'Microfone no Início' }).check()
  await fresh(page)
})

for (const theme of ['light', 'dark'] as const) {
  test.describe(`${theme} theme`, () => {
    test.use({ viewport: { width: 1366, height: 650 } })
    test.beforeEach(async ({ context }) => {
      await context.addInitScript((t) => localStorage.setItem('peace-theme', t), theme)
    })

    test('no page scrolls sideways, on any card; the bar never covers the question box', async ({ page }) => {
      await login(page)
      await page.goto('/')
      const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(await sideways()).toBe(0)
      const next = page.locator('button[aria-label="Cartão seguinte"]') // hidden on the last card, so not by role
      while (await next.isEnabled()) {
        await next.click()
        await page.waitForTimeout(150)
        expect(await sideways()).toBe(0)
      }
      const box = await page.getByLabel('Pergunta à PEACE').boundingBox()
      const bar = await page.locator('aside').first().boundingBox()
      expect(box!.y + box!.height).toBeLessThanOrEqual(bar!.y)
    })

    test('cards use the theme, not a fixed dark picture', async ({ page }) => {
      await login(page)
      await page.goto('/')
      await page.getByRole('button', { name: 'Cartão seguinte' }).click()
      await page.waitForTimeout(800)
      const look = await page.locator('article[data-active="true"]').evaluate((card) => ({
        text: getComputedStyle(card.querySelector('h2')!).color,
        bg: getComputedStyle(card.querySelector('svg.wp')!).backgroundColor,
      }))
      const lightness = (c: string) => {
        const n = c.match(/[\d.]+/g)!.map(Number)
        if (c.startsWith('rgb')) return (n[0] + n[1] + n[2]) / 3 / 255
        return n[0] // color(srgb r g b) and oklab(L ...) are already 0 to 1 first
      }
      if (theme === 'light') {
        expect(lightness(look.text)).toBeLessThan(0.3)
        expect(lightness(look.bg)).toBeGreaterThan(0.6)
      } else {
        expect(lightness(look.text)).toBeGreaterThan(0.7)
        expect(lightness(look.bg)).toBeLessThan(0.3)
      }
    })

    test('a very long name cannot widen a page, at any width', async ({ page }) => {
      await login(page)
      const long = 'x'.repeat(400)
      await page.route('**/api/v1/tasks*', async (r) => {
        const rows = await (await r.fetch()).json()
        await r.fulfill({ json: Array.isArray(rows) ? rows.map((t: object) => ({ ...t, title: long })) : rows })
      })
      await page.route('**/api/v1/materials*', async (r) => {
        const rows = await (await r.fetch()).json()
        await r.fulfill({ json: Array.isArray(rows) ? rows.map((m: object) => ({ ...m, title: long, filename: long })) : rows })
      })
      for (const width of [1366, 820, 390]) {
        await page.setViewportSize({ width, height: 800 })
        for (const path of ['/tarefas', '/cadeiras/1', '/ficheiros', '/definicoes', '/definicoes?separador=funcionalidades', '/definicoes?separador=ia', '/calendario', '/correio']) {
          await page.goto(path)
          await page.waitForTimeout(250)
          const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
          expect(over, `${path} at ${width}`).toBe(0)
        }
      }
    })
  })
}

test('a ChatGPT subscription connects with a code on OpenAI\'s page, never a key', async ({ page }) => {
  await login(page)
  let connected = false
  const overview = () => ({
    active: { provider: '', model: 'qwen3:4b', reachable: true, error: null }, previous: null, automatic: false, share: { chat: true, documents: true, background: false, mail: false }, key_storage: 'file',
    providers: [{ id: 'chatgpt_plan', name: 'Subscrição ChatGPT', keys_url: '', login: true, connected, error: null }],
    choices: [{ provider: '', model: 'qwen3:4b', size: 1, price: null }],
  })
  await page.route('**/api/v1/ai', (r) => r.fulfill({ json: overview() }))
  await page.route('**/api/v1/ai/providers/chatgpt_plan/login', (r) => r.fulfill({ json: { url: 'https://auth.openai.com/codex/device', code: 'ABCD-EFGH', interval: 1 } }))
  await page.route('**/api/v1/ai/providers/chatgpt_plan/login/poll', (r) => {
    connected = true
    return r.fulfill({ json: { connected: true } })
  })
  await page.route('**/api/v1/ai/providers/chatgpt_plan/models', (r) => r.fulfill({ json: [{ id: 'gpt-sub', name: 'GPT', price: null }] }))
  await page.goto('/definicoes?separador=ia')
  await page.getByLabel('Fornecedor').selectOption('chatgpt_plan')
  await expect(page.getByLabel('Chave de API')).toHaveCount(0)
  await page.getByRole('button', { name: 'Entrar com o ChatGPT' }).click()
  await expect(page.getByText('ABCD-EFGH')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Abrir a página da OpenAI' })).toHaveAttribute('href', 'https://auth.openai.com/codex/device')
  await expect(page.getByRole('combobox', { name: 'Modelo', exact: true }).getByRole('option', { name: /gpt-sub/ })).toHaveCount(1)
  await expect(page.getByText('Pagas ao fornecedor')).toHaveCount(0)
})

test('the main page shows how much of the subscription is left', async ({ page }) => {
  await login(page)
  const overview = {
    active: { provider: 'chatgpt_plan', model: 'gpt-sub', reachable: true, error: null }, previous: null, automatic: false, share: { chat: true, documents: true, background: false, mail: false }, key_storage: 'file',
    providers: [{ id: 'chatgpt_plan', name: 'Subscrição ChatGPT', keys_url: '', login: true, connected: true, error: null }],
    choices: [{ provider: 'chatgpt_plan', model: 'gpt-sub', size: null, price: null }],
  }
  await page.route('**/api/v1/ai', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/v1/ai/providers/chatgpt_plan/usage', (r) => r.fulfill({ json: { plan: 'plus', reached: false, windows: [
    { seconds: 18000, used: 12, resets_at: new Date(Date.now() + 3_600_000).toISOString() }, { seconds: 604800, used: 40, resets_at: new Date(Date.now() + 86_400_000 * 3).toISOString() }] } }))
  await page.goto('/')
  const selector = page.getByRole('button', { name: 'Modelo da PEACE' })
  await expect(selector).toContainText('60%') // the tighter of the two
  await selector.click()
  const limits = page.getByRole('region', { name: 'Limites do plano' })
  await expect(limits).toContainText('5 horas')
  await expect(limits).toContainText('88% restante')
  await expect(limits).toContainText('60% restante')
})

test('the main page shows the model and switches it, saying what the change means', async ({ page }) => {
  await login(page)
  const overview = {
    active: { provider: '', model: 'qwen3:4b', reachable: true, error: null }, previous: null, automatic: false, share: { chat: true, documents: true, background: false, mail: false }, key_storage: 'file',
    providers: [{ id: 'openai', name: 'OpenAI', keys_url: 'https://x', connected: true, error: null }],
    choices: [{ provider: '', model: 'qwen3:4b', size: 1, price: null }, { provider: 'openai', model: 'gpt-e2e', size: null, price: 3 }],
  }
  let sent: unknown
  await page.route('**/api/v1/ai', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/v1/ai/active', (r) => {
    sent = r.request().postDataJSON()
    r.fulfill({ json: overview })
  })
  await page.goto('/')
  const selector = page.getByRole('button', { name: 'Modelo da PEACE' })
  await expect(selector).toContainText('qwen3:4b')
  await selector.click()
  await expect(page.getByRole('group', { name: 'Neste PC' }).getByRole('option', { name: 'qwen3:4b' })).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('option', { name: 'gpt-e2e' })).toHaveCount(0)
  await selector.click()
  await expect(page.getByRole('option', { name: 'qwen3:4b' })).toBeFocused() // opens on the model in use
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('option', { name: 'gpt-e2e' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByText(/Usa o crédito da tua conta OpenAI/)).toBeVisible()
  expect(sent).toEqual({ provider: 'openai', model: 'gpt-e2e' })
  await expect(page.getByRole('option', { name: 'gpt-e2e' })).toHaveCount(0)

  await page.route('**/api/v1/ai/active', (r) => r.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"invalid_key"}' }))
  await selector.click()
  await page.getByRole('option', { name: 'gpt-e2e' }).click()
  await expect(page.getByText('Não consegui mudar: chave recusada')).toBeVisible()
})

test('no scrollbar is ever drawn, and long lists still scroll', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes')
  const widths = await page.evaluate(() => {
    const box = document.createElement('div')
    box.style.cssText = 'width:100px;height:100px;overflow:scroll'
    box.innerHTML = '<div style="height:500px"></div>'
    document.body.append(box)
    box.scrollTop = 50
    const out = { bar: box.offsetWidth - box.clientWidth, scrolled: box.scrollTop, page: window.innerWidth - document.documentElement.clientWidth }
    box.remove()
    return out
  })
  expect(widths).toEqual({ bar: 0, scrolled: 50, page: 0 })
})

test('asking in words about the model answers from the backend', async ({ page }) => {
  await login(page)
  await page.goto('/')
  await page.getByLabel('Pergunta à PEACE').fill('que modelo estás a usar?')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByText(/^(Estou a usar|Modelo:)/)).toBeVisible()
})

test('a study plan is made for an exam, shows why, and tracks what is done', async ({ page }) => {
  await login(page)
  await page.goto('/estudo')
  const create = page.getByLabel('Criar um plano de estudo para')
  await create.selectOption({ index: 1 })
  await expect(page.getByText('Plano de estudo criado')).toBeVisible()
  await expect(page.getByText('Como foi feito este plano')).toBeVisible()
  await page.getByText('Como foi feito este plano').click()
  await expect(page.getByText(/valor por defeito|estimativa/).first()).toBeVisible()
  await expect(page.getByLabel('Progresso do plano', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /^Marcar como feita:/ }).first().click()
  await expect(page.getByText(/Estudado: (?!0 min)/)).toBeVisible()

  await page.getByRole('button', { name: 'Reorganizar' }).first().click()
  await expect(page.getByText('Plano reorganizado com o tempo que resta')).toBeVisible()

  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Apagar plano' }).first().click()
  await expect(page.getByText('Como foi feito este plano')).toHaveCount(0)
})

test('a course lists its files: search, read inside PEACE, ask, come back to it', async ({ page }) => {
  await login(page)
  await page.route('**/api/v1/materials/*/file', (r) => r.fulfill({ contentType: 'application/pdf', body: Buffer.from('%PDF-1.4\n%%EOF') }))
  await page.goto('/cadeiras')
  const materials = await (await page.request.get('/api/v1/materials', { headers: { Authorization: `Bearer ${TOKEN}` } })).json()
  const file = materials.find((m: { kind: string; course: unknown }) => m.kind === 'file' && m.course)
  await page.route(`**/api/v1/materials?course_id=${file.course.id}*`, async (r) => {
    const response = await r.fetch()
    const rows = await response.json()
    await r.fulfill({ json: rows.map((m: object) => ({ ...m, fetched_at: '2026-01-01T00:00:00Z' })) })
  })
  await page.goto(`/cadeiras/${file.course.id}`)
  await page.getByLabel('Procurar nos ficheiros').fill('zzzz-nada')
  await expect(page.getByText('Nenhum ficheiro corresponde.')).toBeVisible()
  await page.getByLabel('Procurar nos ficheiros').fill(file.title.split(' ')[0])
  await page.getByRole('button', { name: new RegExp(file.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) }).first().click()
  await expect(page).toHaveURL(new RegExp(`ficheiro=${file.id}`))
  const viewer = page.getByRole('dialog', { name: file.title })
  await expect(viewer.getByRole('link', { name: 'Descarregar' })).toHaveAttribute('href', `/api/v1/materials/${file.id}/file`)

  await page.route('**/ask', (r) => r.fulfill({ json: { reply: 'Trata de normalização.', sources: [] } }))
  await viewer.getByRole('button', { name: 'Resumir' }).click()
  await expect(viewer.getByText('Trata de normalização.')).toBeVisible()

  await viewer.getByRole('button', { name: 'Fechar' }).click()
  await expect(page).not.toHaveURL(/ficheiro=/)
  await expect(page.getByRole('region', { name: 'Continuar a ler' }).getByRole('button', { name: file.title })).toBeVisible()
})

test('opening a file says what it needs: a file to open is a link, a hand-in is not; copies are not listed', async ({ page }) => {
  await login(page)
  const course = (await (await page.request.get('/api/v1/courses', { headers: { Authorization: `Bearer ${TOKEN}` } })).json())[0]
  const base = { course: { id: course.id, name: course.name }, section: 'Aulas', kind: 'file', size: 10, mimetype: 'text/plain', modified_at: null, url: null, vault_path: null, created_at: '2026-11-01T00:00:00Z', fetched_at: '2026-11-01T00:00:00Z' }
  await page.route('**/api/v1/materials?*', (r) => r.fulfill({ json: [
    { ...base, id: 9001, title: 'Guia da aula', filename: 'guia.txt', summary: 'Explica a primeira aula.', file_refs: [
      { name: 'dados.zip', why: 'tem os dados da aula', action: 'abrir', material_id: 9002 }, { name: 'falta.zip', why: '', action: 'abrir', material_id: null }, { name: 'grupo.zip', why: 'a entrega', action: 'entregar', material_id: null }] },
    { ...base, id: 9002, title: 'dados.zip', filename: 'dados.zip' },
    { ...base, id: 9003, title: 'Cópia do guia', filename: 'guia2.txt', duplicate_of_id: 9001 },
  ] }))
  await page.goto(`/cadeiras/${course.id}`)
  await expect(page.getByRole('button', { name: /Cópia do guia/ })).toHaveCount(0)
  await page.getByRole('button', { name: /Guia da aula/ }).click()
  const box = page.getByRole('status').filter({ hasText: 'usares também' })
  await expect(page.getByText('Explica a primeira aula.')).toBeVisible()
  await expect(box).toContainText('tem os dados da aula')
  await expect(box).toContainText('não está nos teus ficheiros')
  await expect(page.getByText('Tens de entregar: grupo.zip')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cópia do guia' })).toBeVisible() // joined, not out of reach
  await box.getByRole('button', { name: 'dados.zip' }).click()
  await expect(page.getByRole('dialog').getByText('dados.zip').first()).toBeVisible()
})

test('a file not downloaded yet says so instead of an error', async ({ page }) => {
  await login(page)
  const materials = await (await page.request.get('/api/v1/materials', { headers: { Authorization: `Bearer ${TOKEN}` } })).json()
  const file = materials.find((m: { kind: string; course: unknown; fetched_at: unknown }) => m.kind === 'file' && m.course && !m.fetched_at)
  await page.goto(`/cadeiras/${file.course.id}?ficheiro=${file.id}`)
  await expect(page.getByText('Este ficheiro ainda não foi descarregado')).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
})

test.afterEach(async ({ page }) => {
  await setUi(page, 'pt') // whatever a test left, the next spec file starts from the seed's settings
})

test('English: the whole interface, dates and server replies; switching back loses nothing', async ({ page }) => {
  await login(page)
  await setUi(page, 'en')
  await page.goto('/definicoes')
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'AI & models' })).toBeVisible()
  await expect(page.getByText('Definições')).toHaveCount(0)
  await page.getByRole('tab', { name: 'AI & models' }).click() // React keeps working on translated text
  await expect(page).toHaveURL(/separador=ia/)
  await page.getByLabel('Provider').selectOption('openai')
  await expect(page.getByText(/includes no API credit/)).toBeVisible()

  await page.goto('/')
  await page.getByLabel('Ask PEACE').fill('que modelo estás a usar?')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText(/^(I'm using|Model:)/)).toBeVisible()

  await page.goto('/calendario')
  await expect(page.getByText(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b/).first()).toBeVisible()
  await page.goto('/definicoes')
  await page.getByLabel('Language').selectOption('pt')
  await expect(page.getByRole('heading', { name: 'Definições' })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('lang', 'pt-PT')
})

test('updates: a copy managed by hand only shows its version', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes')
  await expect(page.getByText(/Versão instalada:/)).toBeVisible()
  await expect(page.getByText(/gerida à mão/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Atualizar agora' })).toHaveCount(0)
})

test('updates: a new version shows its notes, installs on request and failures are explained', async ({ page }) => {
  await login(page)
  const info = { version: '1.0.0', kind: 'docker', policy: 'notify', available: { version: '2.0.0', notes: 'Planos de estudo' }, checked_at: null, check_error: null, status: 'idle', message: null }
  let asked = false
  await page.route('**/api/v1/updates', (r) => r.fulfill({ json: asked ? { ...info, status: 'requested' } : info }))
  await page.route('**/api/v1/updates/install', (r) => {
    asked = true
    r.fulfill({ json: { ...info, status: 'requested' } })
  })
  await page.goto('/definicoes')
  await expect(page.getByText('Planos de estudo')).toBeVisible()
  await page.getByRole('button', { name: 'Atualizar agora' }).click()
  await expect(page.getByRole('button', { name: /A atualizar/ })).toBeDisabled()
  await page.unroute('**/api/v1/updates')
  await page.route('**/api/v1/updates', (r) => r.fulfill({ json: { ...info, status: 'failed', message: 'signature' } }))
  await page.reload()
  await expect(page.getByText(/versão anterior foi reposta/)).toBeVisible()
  await expect(page.getByText(/assinatura do ficheiro não era válida/)).toBeVisible()
})
