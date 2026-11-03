import { expect, test, type Page } from '@playwright/test'

// Runs against scripts/e2e_server.py: a fresh database seeded relative to "now". Tests share it, in order.
test.describe.configure({ mode: 'serial' })
const TOKEN = 'e2e-token-' + 'x'.repeat(32)

async function login(page: Page) {
  await page.goto('/entrar')
  await page.getByLabel('Token de acesso').fill(TOKEN)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL('/')
}

const go = async (page: Page, keys: string) => {
  for (const k of keys) await page.keyboard.press(k)
}

test('pages are protected and a wrong token is refused', async ({ page }) => {
  await page.goto('/tarefas')
  await expect(page).toHaveURL(/\/entrar$/)
  await page.getByLabel('Token de acesso').fill('errado')
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('alert')).toHaveText('Token inválido.')
})

test('the installer link logs in once and leaves no token in the address', async ({ page }) => {
  await page.goto('/entrar#token=' + TOKEN)
  await expect(page).toHaveURL('/')
  expect(await page.evaluate(() => window.location.href)).not.toContain(TOKEN)
  await page.goBack()
  expect(await page.evaluate(() => window.location.href)).not.toContain(TOKEN)
})

test('notification settings queue a native desktop test and explain visibility limits', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=avancado')
  const diagnostics = page.getByRole('region', { name: 'Diagnóstico de notificações' })
  await expect(diagnostics.getByText(/A aceitação pelo serviço não confirma/)).toBeVisible()
  const posted = page.waitForResponse((r) => r.url().endsWith('/api/v1/notifications/test') && r.request().method() === 'POST')
  await diagnostics.getByRole('button', { name: 'Testar notificação nativa' }).click()
  expect((await posted).status()).toBe(201)
  await expect(diagnostics.getByRole('status')).toContainText(/Teste #\d+ em fila/)
  await diagnostics.getByText('Histórico de notificações', { exact: true }).click()
  await expect(diagnostics.getByText('PEACE — teste de notificação nativa')).toBeVisible()
})

test('cockpit: orb, real panels and status, asking PEACE', async ({ page }) => {
  await login(page)
  await expect(page.locator('.orb-hero canvas')).toHaveCount(1)
  await expect(page.getByRole('main').getByText('PEACE', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Hoje', exact: true }).getByText('RC (P1)').first()).toBeVisible()
  await expect(page.getByRole('list', { name: 'Esta semana' }).getByRole('listitem')).toHaveCount(7)
  await expect(page.getByRole('link', { name: 'Graphify' })).toBeVisible()
  // Cards in date order; arrows and the buttons move between them.
  const cards = page.getByRole('region', { name: 'Cartões' })
  await page.keyboard.press('ArrowRight')
  await expect(cards.getByRole('heading', { name: /em atraso/ })).toBeVisible()
  await cards.getByRole('button', { name: 'Cartão seguinte' }).click()
  await expect(cards.getByRole('heading', { name: 'Entregar ficha 3' })).toBeVisible()
  await cards.getByRole('button', { name: 'O que fazer' }).click()
  await cards.getByRole('button', { name: 'Fechar' }).click()
  await page.getByRole('button', { name: 'Tudo por data' }).click()
  const all = page.getByRole('dialog', { name: 'Tudo por data' })
  await expect(all.getByRole('region', { name: 'Para entregar' }).getByText('Projeto final de BD')).toBeVisible()
  await expect(all.getByRole('region', { name: 'Para estudar' }).getByText('BD (Teste 1)')).toBeVisible()
  await all.getByRole('button', { name: /fechar/i }).click()
  await cards.getByRole('button', { name: 'Cartão anterior' }).click()
  await cards.getByRole('button', { name: 'Cartão anterior' }).click()

  await page.getByRole('button', { name: 'Prazos da semana' }).click()
  await expect(page.getByText(/^Nos próximos 7 dias: \d+ prazos? e 1 avaliação\./)).toBeVisible()
  await page.getByLabel('Pergunta à PEACE').fill('liga o modo foco 25 min')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByText(/^Modo foco ligado até às \d\d:\d\d/)).toBeVisible()
  await page.getByRole('button', { name: /^Foco até/ }).click()
  await expect(page.getByRole('button', { name: /^Foco até/ })).toHaveCount(0)

  await page.screenshot({ path: 'test-results/cockpit.png', animations: 'disabled' })
})

test('the rail hides and comes back; files are searchable', async ({ page }) => {
  await login(page)
  await page.getByRole('button', { name: 'Esconder barra lateral' }).click()
  await page.reload()
  await page.getByRole('button', { name: 'Mostrar barra lateral' }).click()
  await page.getByRole('link', { name: 'Ficheiros' }).click()
  await expect(page).toHaveURL('/ficheiros')
  await page.getByLabel('Procurar ficheiros').fill('intro')
  await expect(page.getByText('Slides: Introdução')).toBeVisible()
  await page.getByLabel('Procurar ficheiros').fill('nada disto')
  await expect(page.getByText('Sem materiais')).toBeVisible()
})

test('files: one card per course, list or gallery, the choice is remembered', async ({ page }) => {
  await login(page)
  await page.goto('/ficheiros')
  await expect(page.getByRole('heading', { name: /Redes de Computadores/ })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Abrir Slides: Introdução no Obsidian' })).toBeVisible()
  await page.getByRole('tab', { name: 'Galeria' }).click()
  const tile = page.getByRole('link', { name: 'Abrir Slides: Introdução no Obsidian' })
  await expect(tile).toContainText('PDF')
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Galeria' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: 'Lista' }).click()
})

test('painel shows the day at a glance', async ({ page }) => {
  await login(page)
  await page.goto('/painel')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.locator('.orb[data-quality="standard"] canvas')).toHaveCount(1) // the gold hologram (WebGL)
  await expect(page.getByText('Recomendado agora')).toBeVisible()
  // Work due within four hours outranks overdue work (ADR-005); the overdue report stays listed.
  await expect(page.getByRole('heading', { name: /min em «Entregar ficha 3»/ })).toBeVisible()
  await expect(page.getByText('prazo iminente').first()).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Em atraso', exact: true })).toBeVisible()
  await expect(page.getByText('Relatório de sockets').first()).toBeVisible()
  await expect(page.getByText('RC (P1)').first()).toBeVisible()
  await expect(page.getByText('Novo trabalho: Projeto final de BD')).toBeVisible()
  await expect(page.getByText('BD (Teste 1)').first()).toBeVisible()
  await expect(page.getByText(/Última sincronização há 1 h/).first()).toBeVisible()
  await page.screenshot({ path: 'test-results/home-desktop.png', fullPage: true, animations: 'disabled' })
})

test('every button has an accessible name', async ({ page }) => {
  await login(page)
  for (const path of ['/', '/painel', '/tarefas', '/calendario', '/estudo', '/ficheiros', '/rever', '/correio']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const unnamed = await page.$$eval('button, a[href]', (els) =>
      els.filter((el) => !(el.getAttribute('aria-label') || el.textContent?.trim() || el.getAttribute('title'))).map((el) => el.outerHTML.slice(0, 80)),
    )
    expect(unnamed, path).toEqual([])
  }
})

test('completing a task persists across reloads', async ({ page }) => {
  await login(page)
  await page.goto('/painel')
  await page.getByRole('checkbox', { name: 'Concluir: Entregar ficha 3' }).check()
  await expect(page.getByText('Concluída: Entregar ficha 3')).toBeVisible()
  await page.reload()
  const reopen = page.getByRole('checkbox', { name: 'Reabrir: Entregar ficha 3' })
  await expect(reopen).toBeChecked()
  await reopen.uncheck()
  await expect(page.getByText('Reaberta: Entregar ficha 3')).toBeVisible()
})

test('postponing hides a task and counts the postponement', async ({ page }) => {
  await login(page)
  await page.goto('/painel')
  await page.getByRole('button', { name: 'Ações: Ler artigo sobre TCP' }).click()
  await page.getByRole('menuitem', { name: /Adiar: Amanhã às 9h/ }).click()
  await expect(page.getByText(/Adiada \(amanhã às 9h\): Ler artigo sobre TCP/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Ler artigo sobre TCP', exact: true })).toHaveCount(0)
  await page.goto('/tarefas')
  await expect(page.getByText('adiada 1 vez')).toBeVisible()
})

test('keyboard shortcuts navigate and create a task', async ({ page }) => {
  await login(page)
  // The shortcut listener attaches just after the first paint: retry the keys instead of racing it.
  await expect(async () => {
    await go(page, 'gt')
    await expect(page).toHaveURL('/tarefas', { timeout: 300 })
  }).toPass()
  await go(page, 'gp')
  await expect(page).toHaveURL('/prazos')
  await page.keyboard.press('n')
  const dialog = page.getByRole('dialog', { name: 'Nova tarefa' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Título').fill('Estudar para o teste de BD')
  await dialog.getByLabel('Cadeira').selectOption({ label: 'Bases de Dados' })
  await dialog.getByLabel('Prioridade').selectOption({ label: 'Alta' })
  await page.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByText('Tarefa criada')).toBeVisible()
  await go(page, 'gt')
  await expect(page.getByRole('button', { name: 'Estudar para o teste de BD', exact: true })).toBeVisible()
})

test('task search, board view and moving between columns', async ({ page }) => {
  await login(page)
  await page.goto('/tarefas')
  await expect(page.getByLabel('Procurar tarefas')).toBeVisible()
  // The shortcut listener attaches just after the first paint: retry the key instead of racing it.
  await expect(async () => {
    await page.keyboard.press('/')
    await expect(page.getByLabel('Procurar tarefas')).toBeFocused({ timeout: 300 })
  }).toPass()
  await page.keyboard.type('sockets')
  await expect(page.getByRole('button', { name: 'Relatório de sockets', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Projeto final de BD', exact: true })).toHaveCount(0)
  await page.getByLabel('Procurar tarefas').fill('')
  await page.getByRole('tab', { name: 'Quadro' }).click()
  await page.getByRole('button', { name: 'Mover Projeto final de BD' }).click()
  await page.getByRole('menuitem', { name: 'Em curso' }).click()
  await expect(page.getByRole('region', { name: 'Em curso' }).getByText('Projeto final de BD')).toBeVisible()
})

test('calendar switches views and filters by kind', async ({ page }) => {
  await login(page)
  await page.goto('/calendario')
  await expect(page.getByRole('button', { name: /RC \(P1\)/ }).first()).toBeVisible()
  await page.getByRole('button', { name: /RC \(P1\)/ }).first().click()
  await expect(page.getByRole('dialog', { name: 'RC (P1)' })).toContainText('L2.1')
  await page.keyboard.press('Escape')
  await page.getByRole('tab', { name: 'Mês' }).click()
  await expect(page).toHaveURL(/view=month/)
  await page.getByRole('tab', { name: 'Dia' }).click()
  await expect(page).toHaveURL(/view=day/)
  await page.getByRole('button', { name: 'Aulas', exact: true }).click()
  await expect(page.getByRole('button', { name: /RC \(P1\)/ })).toHaveCount(0)
})

test('calendar: create, edit and delete an event', async ({ page }) => {
  await login(page)
  await page.goto('/calendario?view=day')
  await page.getByRole('button', { name: 'Evento' }).click()
  await page.getByLabel('Título').fill('Reunião de grupo')
  await page.getByRole('button', { name: 'Criar' }).click()
  await expect(page.getByText('Evento criado')).toBeVisible()
  await page.getByRole('button', { name: /Reunião de grupo/ }).click()
  await page.getByRole('button', { name: 'Editar' }).click()
  await page.getByLabel('Título').fill('Reunião de grupo (sala 2)')
  await page.getByLabel('Tipo').selectOption('exam')
  await page.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByText('Evento guardado')).toBeVisible()
  await page.getByRole('button', { name: /Reunião de grupo \(sala 2\)/ }).click()
  await expect(page.getByRole('dialog', { name: 'Reunião de grupo (sala 2)' })).toContainText('Avaliação')
  await page.getByRole('button', { name: 'Apagar' }).click()
  await page.getByRole('button', { name: 'Confirmar: apagar' }).click()
  await expect(page.getByText('Evento apagado')).toBeVisible()
  await expect(page.getByRole('button', { name: /Reunião de grupo/ })).toHaveCount(0)
})

test('calendar: double-click or drag on free space creates, dragging an event moves it', async ({ page }) => {
  await login(page)
  await page.goto('/calendario?view=day')
  const column = page.locator('[data-day="0"]')
  await column.evaluate((el) => el.closest('.overflow-y-auto')!.scrollTo(0, 0))
  const fresh = (await column.boundingBox())!
  const at = (hour: number) => fresh.y + (hour - 8) * 52 + 2 // the grid starts at 08:00, 52 px per hour

  await page.mouse.dblclick(fresh.x + 20, at(9))
  await expect(page.getByRole('dialog', { name: 'Novo evento' })).toBeVisible()
  await expect(page.getByRole('dialog').getByLabel('Início', { exact: true })).toHaveValue(/T09:00$/)
  await expect(page.getByRole('dialog').getByLabel('Fim', { exact: true })).toHaveValue(/T10:00$/)
  await page.getByLabel('Título').fill('Estudo em grupo')
  await page.getByRole('button', { name: 'Criar' }).click()
  await expect(page.getByText('Evento criado')).toBeVisible()

  await page.mouse.move(fresh.x + 20, at(11))
  await page.mouse.down()
  await page.mouse.move(fresh.x + 20, at(12), { steps: 5 })
  await page.mouse.move(fresh.x + 20, at(12.5), { steps: 5 })
  await page.mouse.up()
  await expect(page.getByRole('dialog').getByLabel('Início', { exact: true })).toHaveValue(/T11:00$/)
  await expect(page.getByRole('dialog').getByLabel('Fim', { exact: true })).toHaveValue(/T12:45$/)
  await page.getByRole('button', { name: 'Cancelar' }).click()

  const block = page.getByRole('button', { name: /Estudo em grupo/ })
  const b = (await block.boundingBox())!
  await page.mouse.move(b.x + 10, b.y + 5)
  await page.mouse.down()
  await page.mouse.move(b.x + 10, b.y + 5 + 52 * 2, { steps: 10 })
  await page.mouse.up()
  await expect(page.getByText(/Estudo em grupo: .*11:00/)).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0) // a drag is not a click
})

test('calendar: Delete over an event asks first, then deletes', async ({ page }) => {
  await login(page)
  await page.goto('/calendario?view=day')
  await page.getByRole('button', { name: 'Evento' }).click()
  await page.getByLabel('Título').fill('Para apagar')
  await page.getByRole('button', { name: 'Criar' }).click()
  await expect(page.getByText('Evento criado')).toBeVisible()
  const block = page.getByRole('button', { name: /Para apagar/ })
  await block.hover()
  await page.keyboard.press('Delete')
  await expect(page.getByRole('dialog', { name: 'Apagar evento?' })).toContainText('«Para apagar»')
  await page.getByRole('button', { name: 'Cancelar' }).click()
  await expect(block).toBeVisible()
  await block.hover()
  await page.keyboard.press('Delete')
  await page.getByRole('dialog', { name: 'Apagar evento?' }).getByRole('button', { name: 'Apagar' }).click()
  await expect(page.getByText('Evento apagado')).toBeVisible()
  await expect(block).toHaveCount(0)
})

test('holo hands: switch, and gestures act like the mouse and keyboard', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('peace-holo-test', '1'))
  await login(page)
  // The switch: button and H key, kept by the server.
  const button = page.getByRole('button', { name: 'Holo Hands' })
  await expect(button).toHaveAttribute('aria-pressed', 'false')
  expect((await page.request.put('/api/v1/holo', { data: { on: false } })).ok()).toBe(true)

  type G = Record<string, unknown>
  const holo = async (g: G) => {
    await page.waitForFunction(() => 'peaceHolo' in window)
    await page.evaluate((x) => (window as unknown as { peaceHolo: (g: G) => void }).peaceHolo(x), g)
  }
  const at = async (name: RegExp | string) => {
    const b = (await page.getByRole('button', { name }).first().boundingBox())!
    const v = page.viewportSize()!
    return { x: (b.x + b.width / 2) / v.width, y: (b.y + b.height / 2) / v.height }
  }

  // Double pinch opens (a click), a fist closes (Esc).
  await page.goto('/calendario?view=day')
  await holo({ type: 'open', ...(await at('Evento')) })
  await expect(page.getByRole('dialog', { name: 'Novo evento' })).toBeVisible()
  await holo({ type: 'escape' })
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Pinch-and-hold drags: move an event one hour down.
  await page.getByRole('button', { name: 'Evento' }).click()
  await page.getByLabel('Título').fill('Mãos no ar')
  await page.getByRole('button', { name: 'Criar' }).click()
  await expect(page.getByText('Evento criado')).toBeVisible()
  const from = await at(/Mãos no ar/)
  const hour = 52 / page.viewportSize()!.height
  await holo({ type: 'dragStart', x: from.x, y: from.y - 0.005 })
  for (let i = 1; i <= 5; i++) await holo({ type: 'dragMove', x: from.x, y: from.y - 0.005 + (hour * i) / 5 })
  await holo({ type: 'dragEnd', x: from.x, y: from.y - 0.005 + hour })
  await expect(page.getByText(/Mãos no ar: .*15:00/)).toBeVisible()

  // Peace sign: calendar back to today's week; swipe back: previous page.
  await page.goto('/calendario?view=month&date=2020-01-15')
  await holo({ type: 'reset' })
  await expect(page).toHaveURL(/view=week/)
  await page.goto('/tarefas')
  await holo({ type: 'back' })
  await expect(page).toHaveURL(/calendario/)

  // The H key turns it on for real (fake camera): the model loads, it stays on, the server keeps the switch.
  await page.waitForFunction(() => 'peaceHolo' in window) // 'back' reloaded the page: wait until it is live
  await page.keyboard.press('h')
  await expect(button).toHaveAttribute('aria-pressed', 'true')
  await expect(button.locator('svg')).not.toHaveClass(/animate-pulse/, { timeout: 30_000 }) // started
  await page.waitForTimeout(1500)
  await expect(button).toHaveAttribute('aria-pressed', 'true') // no error switched it off
  await expect.poll(async () => (await (await page.request.get('/api/v1/holo')).json()).on).toBe(true)
  await page.keyboard.press('h')
  await expect(button).toHaveAttribute('aria-pressed', 'false')
})

test('holo hands on the graph: orbit, zoom, peace sign and fist', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('peace-holo-test', '1'))
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page)
  await page.goto('/conhecimento')
  await expect(page.getByTestId('knowledge-graph').locator('canvas')).toHaveCount(1)
  await page.waitForFunction(() => 'peaceHolo' in window)
  type G = Record<string, unknown>
  const holo = (g: G) => page.evaluate((x) => (window as unknown as { peaceHolo: (g: G) => void }).peaceHolo(x), g)
  const shot = () => page.getByTestId('knowledge-graph').locator('canvas').screenshot()
  await page.waitForTimeout(2500) // the layout settles and frames itself
  const before = await shot()
  await holo({ type: 'dragStart', x: 0.4, y: 0.5 })
  for (let i = 1; i <= 6; i++) await holo({ type: 'dragMove', x: 0.4 + i * 0.03, y: 0.5 })
  await holo({ type: 'dragEnd', x: 0.58, y: 0.5 })
  for (let i = 0; i < 4; i++) await holo({ type: 'zoom', factor: 1.3 })
  await page.waitForTimeout(300)
  expect(Buffer.compare(before, await shot())).not.toBe(0) // the view moved
  await holo({ type: 'reset' })
  await page.getByLabel('Procurar no grafo').fill('slides')
  await page.getByRole('list', { name: 'Resultados' }).getByRole('button', { name: /Slides: Introdução/ }).click()
  await expect(page.getByRole('heading', { name: 'Slides: Introdução' })).toBeVisible()
  await page.getByLabel('Procurar no grafo').blur()
  await holo({ type: 'escape' })
  await expect(page.getByRole('heading', { name: 'Slides: Introdução' })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('google calendar: connect, status, sessions toggle and disconnect', async ({ page }) => {
  let state = { available: true, connected: false, email: null as string | null, sessions: false, last_sync: null as string | null, error: null, readonly_calendars: 0 }
  await page.route('/api/v1/google/calendar*', async (route) => {
    const method = route.request().method()
    if (method === 'PUT') state = { ...state, sessions: route.request().postDataJSON().sessions }
    if (method === 'DELETE') state = { ...state, connected: false }
    await route.fulfill(method === 'DELETE' ? { status: 204 } : { json: state })
  })
  await login(page)
  await page.goto('/definicoes?separador=integracoes')
  await expect(page.getByRole('link', { name: 'Ligar Google Calendar' })).toHaveAttribute('href', '/auth/google/start?calendar=1')
  state = { ...state, connected: true, email: 'ana@gmail.com', last_sync: new Date().toISOString(), readonly_calendars: 2 }
  await page.goto('/definicoes?google=ligado')
  await expect(page.getByText(/Google Calendar ligado/)).toBeVisible()
  await expect(page.getByText(/Ligado a ana@gmail.com/)).toBeVisible()
  await expect(page.getByText(/2 outro\(s\) calendário/)).toBeVisible()
  await page.getByRole('checkbox', { name: /sessões de estudo no Google Calendar/ }).click()
  await expect(page.getByText('Preferência guardada')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /sessões de estudo no Google Calendar/ })).toBeChecked()
  await page.getByRole('button', { name: 'Desligar o Google Calendar' }).click()
  await page.getByRole('button', { name: 'Desligar', exact: true }).click()
  await expect(page.getByText(/Os teus dados na PEACE ficaram/)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Ligar Google Calendar' })).toBeVisible()
})

test('deadlines timeline and horizon', async ({ page }) => {
  await login(page)
  await page.goto('/prazos')
  await expect(page.getByRole('heading', { name: 'Em atraso' })).toBeVisible()
  await expect(page.getByText('BD (Teste 1)')).toBeVisible()
  await page.getByRole('tab', { name: '7 dias' }).click()
  await expect(page.getByText('Projeto final de BD')).toBeVisible()
})

test('course page lists materials with an Obsidian link', async ({ page }) => {
  await login(page)
  await page.goto('/cadeiras')
  await page.getByRole('link', { name: /Redes de Computadores/ }).first().click()
  await expect(page.getByText('Slides: Introdução')).toBeVisible()
  await expect(page.getByText('1-intro.pdf · 2,4 MB')).toBeVisible()
  const obsidian = page.getByRole('link', { name: 'Abrir Slides: Introdução no Obsidian' })
  await expect(obsidian).toHaveAttribute(
    'href',
    'obsidian://open?vault=Peace%20Vault&file=' + encodeURIComponent('02 - University/Courses/Redes de Computadores/Materiais/Teóricas/1-intro.pdf'),
  )
})

test('knowledge graph: 3D view, search, details with Obsidian links, filters', async ({ page }) => {
  await login(page)
  await page.goto('/conhecimento')
  await expect(page.getByTestId('knowledge-graph').locator('canvas')).toHaveCount(1)
  await expect(page.getByText(/^\d+ nós · \d+ ligações/)).toBeVisible()
  const search = page.getByLabel('Procurar no grafo')
  await search.fill('slides intro')
  await expect(page.getByRole('list', { name: 'Resultados' })).toHaveCount(0) // words must match in order
  await search.fill('slides')
  await page.getByRole('list', { name: 'Resultados' }).getByRole('button', { name: /Slides: Introdução/ }).click()
  await expect(page.getByRole('heading', { name: 'Slides: Introdução' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Abrir no Obsidian' })).toHaveAttribute('href', /file=02%20-%20University%2FCourses%2FRedes/)
  await expect(page.getByRole('link', { name: 'Nota do grafo' })).toHaveAttribute('href', /file=07%20-%20Generated%2FGraphify%2FSlides%20Introdu/)
  await expect(page.getByRole('list', { name: 'Ligações' }).getByText('Redes de Computadores')).toBeVisible()
  // Hiding files hides the selection too.
  const files = page.getByRole('button', { name: 'Ficheiros' })
  await files.click()
  await expect(files).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('heading', { name: 'Slides: Introdução' })).toHaveCount(0)
  await expect(page.getByText('Como usar')).toBeVisible()
})

test('planning the week creates sessions that can be completed', async ({ page }) => {
  await login(page)
  await page.goto('/estudo')
  await page.getByRole('button', { name: 'Planear semana' }).click()
  await expect(page.getByText(/sess(ão|ões) planead/)).toBeVisible()
  // Sessions name the file to study, chosen from its content.
  const study = page.getByRole('list', { name: 'Material para estudar' }).getByRole('link', { name: 'Slides: Introdução' }).first()
  await expect(study).toBeVisible()
  await expect(study).toHaveAttribute('title', /^fala de .*socket/)
  await expect(study).toHaveAttribute('href', /^obsidian:\/\/open\?vault=Peace%20Vault&file=02%20-%20University/)
  await page.getByRole('button', { name: 'Feita' }).first().click()
  await expect(page.getByText('Sessão concluída')).toBeVisible()
  await expect(page.getByText('feita', { exact: true }).first()).toBeVisible()
})

test('settings save planner preferences and switch theme', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=estudo')
  await page.getByLabel('Duração de cada sessão (min)').fill('45')
  await page.getByRole('button', { name: 'Guardar preferências' }).click()
  await expect(page.getByText(/Preferências guardadas/)).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Duração de cada sessão (min)')).toHaveValue('45')
  await page.goto('/definicoes?separador=notificacoes')
  await page.getByLabel('Silêncio a partir de').fill('22:30')
  await page.getByRole('checkbox', { name: 'Novidades nos sites' }).uncheck()
  await page.getByRole('button', { name: 'Guardar notificações' }).click()
  await expect(page.getByText('Notificações guardadas')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Silêncio a partir de')).toHaveValue('22:30')
  await expect(page.getByRole('checkbox', { name: 'Novidades nos sites' })).not.toBeChecked()
  await page.goto('/definicoes?separador=aparencia')
  await page.getByRole('tab', { name: 'Claro' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.goto('/painel')
  await expect(page.getByText('Recomendado agora')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.screenshot({ path: 'test-results/home-light.png', fullPage: true, animations: 'disabled' })
  await page.goto('/definicoes?separador=aparencia')
  await page.getByRole('tab', { name: 'Escuro' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
})

test('appearance: accent colour previews live, persists and adapts the hologram', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=aparencia')
  const accent = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim())
  await expect.poll(accent).toBe('#ffb547')
  await page.getByLabel('O teu nome').fill('Ana')
  await page.getByRole('button', { name: 'Ciano' }).click()
  await expect.poll(accent).toBe('#6fd3ff') // live preview before saving
  await expect(page.getByLabel('Cor em HEX')).toHaveValue('#6fd3ff')
  await page.getByLabel('Componente R').fill('0')
  await expect(page.getByLabel('Cor em HEX')).toHaveValue('#00d3ff')
  await expect(page.getByText(/Contraste do destaque: \d+,?\.?\d*:1/)).toBeVisible()
  await page.getByRole('button', { name: 'Guardar aparência' }).click()
  await expect(page.getByText('Aparência guardada')).toBeVisible()
  await page.reload()
  await expect.poll(accent).toBe('#00d3ff')
  await page.goto('/')
  await expect(page.getByText('Boa tarde, Ana').or(page.getByText('Bom dia, Ana')).or(page.getByText('Boa noite, Ana'))).toBeVisible()
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--holo').trim())).toBe('#00d3ff')
  // Unsaved previews are undone when leaving Settings.
  await page.goto('/definicoes?separador=aparencia')
  await page.getByRole('button', { name: 'Rosa' }).click()
  await expect.poll(accent).toBe('#f472b6')
  await page.getByRole('link', { name: 'Início' }).first().click()
  await expect.poll(accent).toBe('#00d3ff')
  await page.goto('/definicoes?separador=aparencia')
  await page.getByRole('button', { name: 'Repor dourado' }).click()
  await page.getByLabel('O teu nome').fill('')
  await page.getByRole('button', { name: 'Guardar aparência' }).click()
  await expect(page.getByText('Aparência guardada')).toBeVisible()
})

test('voice settings: push-to-talk key and spoken replies persist; only real voices are offered', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=voz')
  await expect(page.getByText('O agente do PC está desligado')).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Voz', exact: true })).toBeDisabled()
  await expect(page.getByLabel('Microfone', { exact: true })).toHaveValue('')
  await expect(page.getByLabel('Saída de áudio', { exact: true })).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Testar microfone (5 s)' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Testar transcrição (5 s)' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Ouvir voz de teste' })).toBeDisabled()
  await page.getByLabel('Teclas modificadoras').selectOption('SUPER + SHIFT')
  await page.getByLabel('Tecla', { exact: true }).selectOption('D')
  await page.getByRole('checkbox', { name: /Responder em voz alta/ }).uncheck()
  await expect(page.getByLabel('Língua da resposta falada')).toHaveValue('en')
  await page.getByLabel('Língua da resposta falada').selectOption('pt')
  await page.getByRole('button', { name: 'Guardar voz' }).click()
  await expect(page.getByText(/Voz guardada/)).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Teclas modificadoras')).toHaveValue('SUPER + SHIFT')
  await expect(page.getByLabel('Tecla', { exact: true })).toHaveValue('D')
  await expect(page.getByRole('checkbox', { name: /Responder em voz alta/ })).not.toBeChecked()
  await expect(page.getByLabel('Língua da resposta falada')).toHaveValue('pt')
})

test('phone: a secret topic is shown and the choice is kept', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=notificacoes')
  const topic = page.locator('code').filter({ hasText: /^peace-/ })
  await expect(topic).toBeVisible()
  const name = await topic.textContent()
  await page.getByRole('checkbox', { name: 'Enviar lembretes para o telemóvel' }).check()
  await page.getByLabel('Quando enviar para o telemóvel').selectOption('always')
  await page.getByRole('button', { name: 'Guardar telemóvel' }).click()
  await expect(page.getByText('Telemóvel guardado')).toBeVisible()
  await page.reload()
  await expect(page.locator('code').filter({ hasText: /^peace-/ })).toHaveText(name!)
  await expect(page.getByLabel('Quando enviar para o telemóvel')).toHaveValue('always')
})

test('integrations: a source can be switched off and its password is never shown back', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=integracoes')
  const health = page.locator('ul').filter({ hasText: 'Moodle' }).first()
  await expect(health).toContainText('Inforestudante')
  await page.getByRole('checkbox', { name: 'Ligar Inforestudante' }).uncheck()
  await expect(page.getByLabel('Endereço').nth(1)).toHaveValue('https://moodle.example.pt/moodle')
  await page.getByLabel('Utilizador').nth(1).fill('a2020@example.pt')
  await page.getByLabel('Palavra-passe').nth(1).fill('segredo-e2e')
  await page.getByRole('button', { name: 'Guardar integrações' }).click()
  await expect(page.getByText('Integrações guardadas')).toBeVisible()
  await expect(health).not.toContainText('Inforestudante')
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Ligar Inforestudante' })).not.toBeChecked()
  await expect(page.getByLabel('Utilizador').nth(1)).toHaveValue('a2020@example.pt')
  await expect(page.getByLabel('Palavra-passe').nth(1)).toHaveValue('')
  await expect(page.getByText('Guardada. Deixa vazio para manter.').first()).toBeVisible() // Moodle's; the seeded email has one too
  await expect(page.locator('body')).not.toContainText('segredo-e2e')
})

test('loading and error states', async ({ page }) => {
  await login(page)
  await page.route('**/api/v1/today', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'falha simulada' }) }),
  )
  await page.goto('/painel')
  await expect(page.getByRole('alert')).toContainText('falha simulada')
  await page.unroute('**/api/v1/today')
  await page.route('**/api/v1/today', async (route) => {
    await new Promise((r) => setTimeout(r, 1200))
    await route.continue()
  })
  await page.goto('/painel')
  await expect(page.locator('.animate-pulse').first()).toBeVisible()
  await expect(page.getByText('Recomendado agora')).toBeVisible()
})

test('mobile layout: drawer navigation and no horizontal scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)
  await page.getByRole('button', { name: 'Abrir menu' }).click()
  await page.getByRole('link', { name: 'Prazos', exact: true }).click()
  await expect(page).toHaveURL('/prazos')
  for (const path of ['/', '/painel', '/calendario', '/tarefas', '/estudo', '/conhecimento', '/ficheiros', '/rever', '/correio']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow, path).toBeLessThanOrEqual(0)
  }
  await page.goto('/')
  await page.screenshot({ path: 'test-results/home-mobile.png', fullPage: true, animations: 'disabled' })
})

test('logging out ends the session', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=geral')
  await page.getByRole('button', { name: 'Terminar sessão' }).click()
  await expect(page).toHaveURL('/entrar')
  await page.goto('/')
  await expect(page).toHaveURL('/entrar')
})

test('diagnostics: every part of PEACE is listed with its state and code', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes?separador=avancado')
  const card = page.getByRole('heading', { name: 'Diagnóstico' }).locator('xpath=ancestor::section[1]')
  await expect(card.getByText('Base de dados', { exact: true })).toBeVisible()
  await expect(card.getByText('[agent_offline]')).toBeVisible()
  await expect(card.getByText(/Modelo local/)).toBeVisible()
})

test('login: Google first, token as the offline fallback, clear errors', async ({ page }) => {
  await page.route('/auth/google', (route) => route.fulfill({ json: { available: true } }))
  await page.goto('/entrar?erro=conta')
  await expect(page.getByRole('alert')).toHaveText(/pertence a outra conta Google/)
  await expect(page.getByRole('link', { name: 'Entrar com Google' })).toHaveAttribute('href', '/auth/google/start')
  await expect(page.getByLabel('Token de acesso')).toHaveCount(0)
  await page.getByRole('button', { name: /Usar token de acesso/ }).click()
  await page.getByLabel('Token de acesso').fill(TOKEN)
  await page.getByRole('button', { name: 'Entrar com token' }).click()
  await expect(page).toHaveURL('/')
})

test('a desktop action shows what the agent verified, step by step', async ({ page, request }) => {
  // Last on purpose: its heartbeat makes the desktop look connected for 90 s, and earlier tests expect it off.
  // The test plays the desktop agent through its API; the real desktop is never involved.
  const auth = { Authorization: `Bearer ${TOKEN}` }
  await login(page)
  await page.getByLabel('Pergunta à PEACE').fill('abre o obsidian')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByText('O agente do PC está desligado, por isso não mexi no ambiente de trabalho.')).toBeVisible()

  await request.post('/api/v1/desktop/heartbeat', { headers: auth, data: { capabilities: {} } })
  await page.getByLabel('Pergunta à PEACE').fill('muda para a área de trabalho 3')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByText('A tratar disso no PC: mudar para a área de trabalho 3.')).toBeVisible()
  await expect(page.getByText('À espera do agente do PC…')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancelar' })).toBeVisible()
  const job = await (await request.get('/api/v1/desktop/jobs/next', { headers: auth })).json()
  expect(job.steps).toEqual([{ tool: 'workspaces.switch', args: { workspace: 3 } }])
  await request.post(`/api/v1/desktop/jobs/${job.id}/progress`, { headers: auth, data: { final: true, results: [
    { tool: 'workspaces.switch', ok: true, verified: false, code: null, message: 'Estou na área de trabalho 3.' }] } })
  await expect(page.getByText('Feito, mas há passos que não consegui confirmar. Estou na área de trabalho 3.')).toBeVisible()
  await expect(page.getByLabel('Feito, sem confirmação')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancelar' })).toHaveCount(0)
})

test('obsidian: pick a vault the desktop found; links open it', async ({ page }) => {
  await login(page)
  const vaults = [
    { name: 'Peace Vault', path: '/home/ana/Peace Vault' },
    { name: 'Estudo', path: '/home/ana/Estudo' },
  ]
  expect((await page.request.post('/api/v1/desktop/heartbeat', { data: { capabilities: { vaults } } })).status()).toBe(204)
  await page.goto('/definicoes?separador=integracoes')
  await expect(page.getByText(/no cofre «Peace Vault»/)).toBeVisible()
  await page.getByLabel('Cofre do Obsidian').selectOption('/home/ana/Estudo')
  await expect(page.getByText('Cofre guardado')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Abrir no Obsidian' })).toHaveAttribute('href', 'obsidian://open?vault=Estudo')
  await page.getByLabel('Cofre do Obsidian').selectOption('')
  await expect(page.getByRole('link', { name: 'Abrir no Obsidian' })).toHaveAttribute('href', 'obsidian://open?vault=Peace%20Vault')
})

test('rever: a moved deadline is previewed, applied, undone; the rules are kept', async ({ page }) => {
  await login(page)
  await go(page, 'gr')
  await expect(page).toHaveURL('/rever')
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Re-planear «Projeto final de BD»' }) })
  await expect(card).toContainText('Prazo antecipado 2 dias.')
  await expect(card).toContainText('Origem: Moodle')
  // Earlier tests already planned the project's work, so only the session after the new deadline leaves.
  await expect(card.getByText('Sai do plano')).toBeVisible()
  await card.getByRole('button', { name: 'Aplicar' }).click()
  await expect(page.getByText('Plano atualizado')).toBeVisible()
  await expect(card).toHaveCount(0)
  const row = page.locator('section').filter({ hasText: 'Histórico' }).locator('li').filter({ hasText: 'Re-planear «Projeto final de BD»' })
  await expect(row).toContainText('aplicada')
  await row.getByRole('button', { name: 'Desfazer' }).click()
  await expect(page.getByText('Plano reposto como estava')).toBeVisible()
  await expect(row).toContainText('desfeita')

  await page.goto('/definicoes?separador=avancado')
  await page.getByRole('checkbox', { name: 'Um prazo é adiado' }).check()
  await page.getByRole('button', { name: 'Guardar regras' }).click()
  await expect(page.getByText('Regras guardadas')).toBeVisible()
  await page.reload()
  await expect(page.getByRole('checkbox', { name: 'Um prazo é adiado' })).toBeChecked()
})
