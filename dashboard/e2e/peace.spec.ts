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
  await page.goto('/definicoes')
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
  await expect(page.getByText('PEACE', { exact: true }).first()).toBeVisible()
  for (const name of ['Hoje', 'Próximos prazos', 'Recomendado agora', 'Calendário', 'Estado da PEACE', 'Modo foco'])
    await expect(page.getByRole('region', { name, exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Hoje', exact: true }).getByText('RC (P1)')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Próximos prazos' }).getByText('BD (Teste 1)')).toBeVisible()
  // Only real states: no model in this test server, no desktop agent, so no voice.
  const status = page.getByRole('region', { name: 'Estado da PEACE' })
  await expect(status.getByText('Online')).toBeVisible()
  await expect(status.getByText('offline')).toBeVisible()
  await expect(status.getByText('desligado')).toBeVisible()
  await expect(status.getByText('indisponível')).toBeVisible()
  await expect(status.getByRole('listitem').filter({ hasText: 'Em espera' })).toHaveAttribute('aria-current', 'true')

  await page.getByRole('button', { name: 'Prazos da semana' }).click()
  await expect(page.getByText(/^Nos próximos 7 dias: \d+ prazos? e 1 avaliação\./)).toBeVisible()
  await page.getByLabel('Pergunta à PEACE').fill('liga o modo foco 25 min')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByText(/^Modo foco ligado até às \d\d:\d\d/)).toBeVisible()
  const focus = page.getByRole('region', { name: 'Modo foco' })
  await expect(focus.getByText(/^Ativo até/)).toBeVisible()
  await focus.getByRole('button', { name: 'Terminar' }).click()
  await expect(focus.getByText('Desativado')).toBeVisible()

  // Panels collapse, and stay collapsed after a reload.
  const today = page.getByRole('region', { name: 'Hoje', exact: true })
  await today.getByRole('button', { name: /Hoje/ }).click()
  await expect(today.getByText('RC (P1)')).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('region', { name: 'Hoje', exact: true }).getByRole('button', { name: /Hoje/ })).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('region', { name: 'Hoje', exact: true }).getByRole('button', { name: /Hoje/ }).click()
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
  for (const path of ['/', '/painel', '/tarefas', '/calendario', '/estudo', '/ficheiros']) {
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
  await go(page, 'gt')
  await expect(page).toHaveURL('/tarefas')
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
  await page.goto('/definicoes')
  await page.getByLabel('Duração de cada sessão (min)').fill('45')
  await page.getByRole('button', { name: 'Guardar preferências' }).click()
  await expect(page.getByText(/Preferências guardadas/)).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Duração de cada sessão (min)')).toHaveValue('45')
  await page.getByLabel('Silêncio a partir de').fill('22:30')
  await page.getByRole('checkbox', { name: 'Novidades nos sites' }).uncheck()
  await page.getByRole('button', { name: 'Guardar notificações' }).click()
  await expect(page.getByText('Notificações guardadas')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Silêncio a partir de')).toHaveValue('22:30')
  await expect(page.getByRole('checkbox', { name: 'Novidades nos sites' })).not.toBeChecked()
  await page.getByRole('tab', { name: 'Claro' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.goto('/painel')
  await expect(page.getByText('Recomendado agora')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.screenshot({ path: 'test-results/home-light.png', fullPage: true, animations: 'disabled' })
  await page.goto('/definicoes')
  await page.getByRole('tab', { name: 'Escuro' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
})

test('appearance: accent colour previews live, persists and adapts the hologram', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes')
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
  await page.goto('/definicoes')
  await page.getByRole('button', { name: 'Rosa' }).click()
  await expect.poll(accent).toBe('#f472b6')
  await page.getByRole('link', { name: 'Início' }).first().click()
  await expect.poll(accent).toBe('#00d3ff')
  await page.goto('/definicoes')
  await page.getByRole('button', { name: 'Repor dourado' }).click()
  await page.getByLabel('O teu nome').fill('')
  await page.getByRole('button', { name: 'Guardar aparência' }).click()
  await expect(page.getByText('Aparência guardada')).toBeVisible()
})

test('voice settings: push-to-talk key and spoken replies persist; only real voices are offered', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes')
  await expect(page.getByText('O agente do PC está desligado')).toBeVisible()
  await expect(page.getByLabel('Voz', { exact: true })).toBeDisabled()
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

test('integrations: a source can be switched off and its password is never shown back', async ({ page }) => {
  await login(page)
  await page.goto('/definicoes')
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
  await expect(page.getByText('Guardada. Deixa vazio para manter.')).toBeVisible()
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
  for (const path of ['/', '/painel', '/calendario', '/tarefas', '/estudo', '/conhecimento', '/ficheiros']) {
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
  await page.goto('/definicoes')
  await page.getByRole('button', { name: 'Terminar sessão' }).click()
  await expect(page).toHaveURL('/entrar')
  await page.goto('/')
  await expect(page).toHaveURL('/entrar')
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
