import { existsSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'

// A careless first-time user on the Estudo (timer, hand entries) and Rever pages: double clicks, reloads in the
// middle, impossible times, the wrong button, a phone, only the keyboard. Same seeded server as peace.spec.ts.
test.describe.configure({ mode: 'serial' })
const TOKEN = 'e2e-token-' + 'x'.repeat(32)
const AUTH = { Authorization: `Bearer ${TOKEN}` }

async function login(page: Page) {
  await page.goto('/entrar')
  await page.getByLabel('Token de acesso').fill(TOKEN)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL('/')
}

/** "2026-10-28T14:05" in Lisbon, the format a datetime-local input takes. */
const localInput = (d: Date) => d.toLocaleString('sv-SE', { timeZone: 'Europe/Lisbon' }).replace(' ', 'T').slice(0, 16)
const timerCard = (page: Page) => page.locator('section').filter({ has: page.getByRole('heading', { name: 'Cronómetro' }) })
const timerState = async (page: Page) => (await (await page.request.get('/api/v1/timer', { headers: AUTH })).json()).state

test('timer: a double click starts one timer; reloading keeps it running or paused', async ({ page }) => {
  await login(page)
  await page.goto('/estudo')
  const card = timerCard(page)
  await card.getByRole('button', { name: 'Começar' }).dblclick()
  await expect(card.getByRole('timer')).toBeVisible()
  const report = await (await page.request.get('/api/v1/study-time?period=day', { headers: AUTH })).json()
  expect(report.logs.filter((l: { ends_at: string | null }) => !l.ends_at)).toHaveLength(1)
  await expect(page.getByText('Já há um cronómetro ligado')).toHaveCount(0) // the second click is absorbed, not an error

  await page.reload()
  await expect(card.getByText('A contar')).toBeVisible()
  await card.getByRole('button', { name: 'Pausar' }).click()
  await page.reload()
  await expect(card.getByText('Em pausa')).toBeVisible()
  await card.getByRole('button', { name: 'Continuar' }).click()
  await expect(card.getByText('A contar')).toBeVisible()
  await card.getByRole('button', { name: 'Terminar', exact: true }).click()
  await expect(page.getByText(/^Registados/)).toBeVisible()
  await expect(card.getByRole('button', { name: 'Começar' })).toBeVisible()
  expect(await timerState(page)).toBe('idle')
})

test('hand entries: empty, backwards, future and twice are all refused with a reason', async ({ page }) => {
  await login(page)
  await page.goto('/estudo')
  await timerCard(page).getByRole('button', { name: 'Registar à mão' }).click()
  const dialog = page.getByRole('dialog', { name: 'Registar tempo de estudo' })
  await dialog.getByRole('button', { name: 'Registar' }).click() // nothing filled in
  await expect(dialog).toBeVisible()

  const now = Date.now()
  const fill = async (start: number, end: number) => {
    await dialog.getByLabel('Comecei').fill(localInput(new Date(start)))
    await dialog.getByLabel('Acabei').fill(localInput(new Date(end)))
    await page.getByRole('button', { name: 'Registar', exact: true }).click()
  }
  await fill(now - 60 * 60_000, now - 2 * 60 * 60_000)
  await expect(page.getByText('O fim tem de ser depois do início.')).toBeVisible()
  await fill(now + 60 * 60_000, now + 2 * 60 * 60_000)
  await expect(page.getByText('Só podes registar tempo que já passou.')).toBeVisible()
  await dialog.getByLabel('Tarefa').selectOption({ label: 'Ler artigo sobre TCP · Redes de Computadores' })
  await fill(now - 2 * 60 * 60_000, now - 60 * 60_000)
  await expect(page.getByText('Tempo registado')).toBeVisible()
  await expect(dialog).toBeHidden()

  await timerCard(page).getByRole('button', { name: 'Registar à mão' }).click()
  await fill(now - 90 * 60_000, now - 30 * 60_000) // overlaps the hour just entered
  await expect(page.getByText(/não se pode contar duas vezes/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancelar' }).click()

  const time = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Tempo de estudo' }) })
  await time.getByRole('tab', { name: 'Mês' }).click()
  await expect(time).toContainText('Redes de Computadores')
  await time.getByText(/^Registos/).click()
  await time.getByRole('button', { name: 'Apagar registo' }).first().click()
  await expect(page.getByText('Registo apagado')).toBeVisible()
})

test('a session started from the plan; another one cannot start on top; finishing marks it done', async ({ page }) => {
  const clock = new Date()
  test.skip(clock.getHours() === 23 && clock.getMinutes() >= 30, 'its sessions would fall on tomorrow')
  await login(page)
  const tasks = await (await page.request.get('/api/v1/tasks?q=artigo', { headers: AUTH })).json()
  const at = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString()
  const make = async (from: number, to: number) =>
    (await (await page.request.post('/api/v1/sessions', { headers: AUTH, data: { task_id: tasks[0].id, starts_at: at(from), ends_at: at(to) } })).json()).id
  const ids = [await make(1, 11), await make(12, 22)] // close together: both still today late at night
  try {
    await page.goto('/estudo')
    const rows = page.locator('li').filter({ hasText: 'Ler artigo sobre TCP' }).filter({ has: page.getByRole('button', { name: 'Começar' }) })
    await expect(rows).toHaveCount(2)
    await rows.first().getByRole('button', { name: 'Começar' }).click()
    await expect(timerCard(page)).toContainText('Ler artigo sobre TCP')
    await page.locator('li').filter({ hasText: 'Ler artigo sobre TCP' }).getByRole('button', { name: 'Começar' }).last().click()
    await expect(page.getByText('Já há um cronómetro ligado; termina-o primeiro.')).toBeVisible()
    await timerCard(page).getByRole('button', { name: 'Terminar e marcar feita' }).click()
    await expect(page.getByText(/^Sessão feita/)).toBeVisible()
    await expect(page.locator('li').filter({ hasText: 'Ler artigo sobre TCP' }).getByText('feita', { exact: true })).toHaveCount(1)
  } finally {
    for (const id of ids) await page.request.delete(`/api/v1/sessions/${id}`, { headers: AUTH })
  }
})

test('rever: impatient double clicks apply once and undo once', async ({ page }) => {
  await login(page)
  await page.goto('/rever')
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Re-planear «BD (Teste 1)»' }) })
  await expect(card).toContainText('Origem: Inforestudante')
  await card.getByRole('button', { name: 'Aplicar' }).dblclick()
  await expect(card).toHaveCount(0)
  await expect(page.getByText('Esta sugestão já foi decidida.')).toHaveCount(0)
  const row = page.locator('section').filter({ hasText: 'Histórico' }).locator('li').filter({ hasText: 'BD (Teste 1)' })
  await expect(row).toHaveCount(1)
  await expect(row).toContainText('aplicada')
  await row.getByRole('button', { name: 'Desfazer' }).dblclick()
  await expect(row).toContainText('desfeita')
  await page.reload()
  await expect(row).toHaveCount(1)
  await expect(row.getByRole('button', { name: 'Desfazer' })).toHaveCount(0)
})

test('on a phone and with only the keyboard', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page)
  for (const path of ['/estudo', '/rever']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), path).toBeLessThanOrEqual(0)
  }
  await page.goto('/estudo')
  const card = timerCard(page)
  await card.getByRole('button', { name: 'Começar' }).focus()
  await page.keyboard.press('Enter')
  await expect(card.getByRole('timer')).toBeVisible()
  await card.getByRole('button', { name: 'Terminar', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(card.getByRole('button', { name: 'Começar' })).toBeVisible()
})

test('recuperar: an impatient double click offers once; apply puts the missed work back, undo takes it out', async ({ page }) => {
  await login(page)
  const tasks = await (await page.request.get('/api/v1/tasks?q=artigo', { headers: AUTH })).json()
  const yesterday = Date.now() - 26 * 60 * 60_000
  const missed = await page.request.post('/api/v1/sessions', {
    headers: AUTH,
    data: { task_id: tasks[0].id, starts_at: new Date(yesterday).toISOString(), ends_at: new Date(yesterday + 60 * 60_000).toISOString() },
  })
  const missedId = (await missed.json()).id
  try {
    await page.goto('/rever')
    await page.getByRole('button', { name: 'Recuperar estudo em falta' }).dblclick()
    const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Recuperar no horário habitual' }) })
    await expect(card).toHaveCount(1)
    await expect(card).toContainText('Ficaram por fazer')
    await expect(card).toContainText('Ler artigo sobre TCP') // each new session says what it is for
    await card.getByRole('button', { name: 'Aplicar' }).click()
    await expect(card).toHaveCount(0)
    const row = page.locator('section').filter({ hasText: 'Histórico' }).locator('li').filter({ hasText: 'Recuperar no horário habitual' })
    await expect(row).toContainText('aplicada')
    await row.getByRole('button', { name: 'Desfazer' }).click()
    await expect(row).toContainText('desfeita')
  } finally {
    // Other recovery options offered alongside (e.g. on a late-night run) must not leak into later tests.
    for (const p of await (await page.request.get('/api/v1/proposals', { headers: AUTH })).json())
      if (p.kind.startsWith('recover_')) await page.request.post(`/api/v1/proposals/${p.id}/reject`, { headers: AUTH })
    await page.request.delete(`/api/v1/sessions/${missedId}`, { headers: AUTH })
  }
})

test('correio: search, open, then a cleanup that keeps what was unticked, applied with a double click and undone', async ({ page }) => {
  await login(page)
  await page.goto('/correio')
  await expect(page.getByText(/de 100 MB/)).toBeVisible()
  await page.getByRole('button', { name: /BD: enunciado do projeto final/ }).click()
  await expect(page.getByRole('dialog')).toContainText('20 de novembro')
  await page.keyboard.press('Escape')
  await page.getByLabel('Procurar nos emails').fill('projeto final')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Resultados para «projeto final»')).toBeVisible()
  await expect(page.getByRole('button', { name: /BD: enunciado do projeto final/ })).toHaveCount(1)

  await page.goto('/rever')
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: /^Limpar 2 emails/ }) })
  await expect(card).toContainText('Novo aviso no fórum')
  await card.getByRole('checkbox', { name: 'Limpar «Promoções de outono»' }).uncheck()
  await card.getByRole('button', { name: 'Aplicar' }).dblclick()
  await expect(card).toHaveCount(0)
  const row = page.locator('section').filter({ hasText: 'Histórico' }).locator('li').filter({ hasText: /^Limpar 2 emails/ })
  await expect(row).toContainText('aplicada')

  await page.goto('/correio')
  await page.getByRole('tab', { name: 'Limpos pela PEACE' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: 'Novo aviso no fórum' }).getByRole('button', { name: 'Repor' })).toBeVisible()
  await expect(page.getByText('Promoções de outono')).toHaveCount(0) // unticked: never moved
  await page.getByRole('tab', { name: 'Caixa de entrada' }).click()
  await expect(page.getByText('Promoções de outono')).toBeVisible()

  await page.goto('/rever')
  await row.getByRole('button', { name: 'Desfazer' }).click()
  await expect(row).toContainText('desfeita')
})

test('a date from an email: shown with its quote, applied with a double click, undone', async ({ page }) => {
  await login(page)
  const before = (await (await page.request.get('/api/v1/tasks?q=Projeto final', { headers: AUTH })).json())[0].due_at
  await page.goto('/rever')
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Nova data para «Projeto final de BD»' }) })
  await expect(card).toContainText('Origem: Email · «O projeto final entrega-se a 20 de novembro.»')
  await expect(card).toContainText('A data do Moodle é a oficial')
  await card.getByRole('button', { name: 'Aplicar' }).dblclick()
  await expect(card).toHaveCount(0)
  const task = async () => (await (await page.request.get('/api/v1/tasks?q=Projeto final', { headers: AUTH })).json())[0]
  expect((await task()).due_at).not.toBe(before)
  const row = page.locator('section').filter({ hasText: 'Histórico' }).locator('li').filter({ hasText: 'Nova data para «Projeto final de BD»' })
  await row.getByRole('button', { name: 'Desfazer' }).click()
  await expect(row).toContainText('desfeita')
  expect((await task()).due_at).toBe(before)
})

test('entrega: from the task to a checked requirement, with impatient clicks and wrong folders', async ({ page }) => {
  await login(page)
  await page.goto('/tarefas')
  await page.getByRole('listitem').filter({ hasText: 'Projeto final de BD' }).getByRole('link', { name: 'Requisitos' }).click()
  await expect(page.getByRole('heading', { name: 'Projeto final de BD', level: 1 })).toBeVisible()
  await expect(page.getByLabel('Ficheiro do enunciado')).toHaveValue(/\d+/) // the suggested brief is preselected
  await page.getByRole('button', { name: 'Ler requisitos' }).dblclick()
  const reqs = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Requisitos' }) })
  await expect(reqs.getByLabel('Requisito', { exact: true })).toHaveCount(3)
  await expect(reqs).toContainText('Projeto_BD.pdf · p. 2')

  await page.getByLabel('Novo requisito').fill('Pôr os nomes no relatório')
  await page.getByRole('button', { name: 'Juntar', exact: true }).click()
  await expect(reqs.getByLabel('Requisito', { exact: true })).toHaveCount(4)
  await page.getByLabel('Estado: Pôr os nomes no relatório').selectOption('Feito')
  await expect(page.getByText('1 feitos')).toBeVisible()

  const folder = page.getByLabel('Pasta do projeto')
  for (const bad of ['/etc', '~/.ssh', 'projeto-bd']) {
    await folder.fill(bad)
    await page.getByRole('button', { name: 'Guardar', exact: true }).click()
    await expect(page.getByText(/Essa pasta não existe|dentro da tua pasta pessoal|caminho completo/).first()).toBeVisible()
  }
  await folder.fill('~/projeto-bd')
  await page.getByRole('button', { name: 'Guardar', exact: true }).click()
  await expect(page.getByText('Pasta guardada')).toBeVisible()

  await page.getByLabel('Nome da verificação').fill('Corre')
  await page.getByLabel('Comando').fill('python3 main.py')
  await page.getByLabel('Requisito que verifica').selectOption({ label: 'Validar o NIF de cada cliente' })
  await page.getByRole('button', { name: 'Juntar verificação' }).click()
  await page.getByRole('button', { name: 'Correr' }).dblclick()
  await expect(page.getByText('passou', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Estado: Validar o NIF de cada cliente')).toHaveValue('verified')
  await page.getByRole('button', { name: 'Guardar no Obsidian' }).click()
  await expect(page.getByText(/Relatório guardado/)).toBeVisible()

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
})

test('conceitos: proposed, some unticked, kept with no mastery yet; a loop is refused', async ({ page }) => {
  await login(page)
  const courses = await (await page.request.get('/api/v1/courses', { headers: AUTH })).json()
  const bd = courses.find((c: { name: string }) => c.name === 'Bases de Dados')
  await page.goto(`/cadeiras/${bd.id}`)
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Conceitos e domínio' }) })
  await card.getByRole('button', { name: 'Propor conceitos' }).dblclick()
  await expect(card.getByText('Há conceitos propostos em')).toBeVisible()

  await page.goto('/rever')
  const proposal = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Conceitos de Bases de Dados' }) })
  await expect(proposal).toContainText('Dos temas: normalização')
  await proposal.getByRole('checkbox', { name: 'Ficar com «Normalização»' }).uncheck()
  await proposal.getByRole('button', { name: 'Aplicar' }).dblclick()
  await expect(proposal).toHaveCount(0)

  await page.goto(`/cadeiras/${bd.id}`)
  await expect(card.getByText('Modelo E/R', { exact: true })).toBeVisible()
  await expect(card.getByText('Normalização', { exact: true })).toHaveCount(0) // unticked
  await expect(card.getByText('sem dados')).toHaveCount(2) // no graded answers yet: never guessed
  await expect(card).toContainText('precisa de «Modelo E/R»(inferido)')
  await card.getByRole('button', { name: 'confirmar' }).click()
  await expect(card).not.toContainText('(inferido)')
  await card.getByLabel('O que achas que sabes: Chaves').selectOption({ label: '4 · bem' })
  await expect(card.getByText('sem dados')).toHaveCount(2) // what you say is shown, never mixed in
  await card.getByLabel('Juntar dependência a Modelo E/R').selectOption({ label: 'Chaves' })
  await expect(page.getByText(/faria um círculo/)).toBeVisible()
})

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC', 'base64')

test('exames e treino: photos read, a question corrected, confirmed; practice graded and counted for mastery', async ({ page }) => {
  test.skip(!existsSync('../tests/fixtures/past_exam_reads.json'), 'real exam text: not in the public copy')
  await login(page)
  const courses = await (await page.request.get('/api/v1/courses', { headers: AUTH })).json()
  const bd = courses.find((c: { name: string }) => c.name === 'Bases de Dados')
  const concepts = await (await page.request.get(`/api/v1/courses/${bd.id}/concepts`, { headers: AUTH })).json()
  if (!concepts.concepts.length) {
    await page.request.post(`/api/v1/courses/${bd.id}/concepts/propose`, { headers: AUTH })
    const p = (await (await page.request.get('/api/v1/proposals', { headers: AUTH })).json()).find((x: { kind: string }) => x.kind === 'concepts')
    await page.request.post(`/api/v1/proposals/${p.id}/apply`, { headers: AUTH, data: {} })
  }

  await page.goto(`/cadeiras/${bd.id}`)
  const exams = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Exames antigos' }) })
  await exams.getByLabel('Nome do exame').fill('Teste SQL 2024/25')
  await exams.getByLabel('Fotos ou PDF do exame').setInputFiles({ name: 'IMG_2041.png', mimeType: 'image/png', buffer: PNG })
  await exams.getByRole('button', { name: 'Enviar e ler' }).dblclick()
  await expect(page).toHaveURL(/\/exames\/\d+$/)
  await expect(page.getByRole('heading', { name: 'Pergunta 5' })).toBeVisible()
  await expect(page.getByRole('img', { name: /Página 1 do exame/ })).toBeVisible()
  await expect(page.getByLabel('Pergunta 4: tipo')).toHaveValue('sql_ddl')

  await page.getByLabel('Pergunta 1: o que pede').fill('Um SELECT com junções, ordenado.')
  const save = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Pergunta 1' }) }).getByRole('button', { name: 'Guardar' })
  await save.dblclick()
  await expect(save).toBeDisabled() // saved: nothing left to save
  await page.getByRole('button', { name: 'Confirmar exame' }).dblclick()
  await expect(page.getByText('confirmado', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Ler outra vez' })).toHaveCount(0) // would erase the corrections

  await page.goto(`/cadeiras/${bd.id}`)
  await expect(exams).toContainText('SQL: consultas')
  await expect(exams).toContainText('80 % · em 1 de 1')
  const practice = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Treino' }) })
  await practice.getByRole('button', { name: 'Gerar perguntas' }).dblclick()
  await expect(practice).toContainText('deitadas fora por não passarem a verificação')
  await practice.getByRole('button', { name: /^Começar/ }).dblclick()
  await expect(page).toHaveURL(/\/treino\/\d+$/)
  await page.getByText('A morada').click() // wrong
  await page.getByRole('button', { name: 'Responder' }).dblclick()
  await expect(page.getByText('Errado.')).toBeVisible()
  await expect(page.getByLabel('resposta certa')).toBeVisible()
  await page.getByRole('button', { name: 'Terminar agora' }).click() // the code and written questions left unanswered
  await expect(page.getByText(/^0 de 1/)).toBeVisible()
  await expect(page.getByText(/Rever: /)).toBeVisible()

  await page.goto(`/cadeiras/${bd.id}`)
  const card = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Conceitos e domínio' }) })
  await expect(card.getByText(/^fraco/).first()).toBeVisible() // one wrong answer: evidence, not a guess
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
})

test('treino: wrong ones retaken, a timed exam handed in early, code run in the sandbox, an estimated grade corrected', async ({ page }) => {
  await login(page)
  const courses = await (await page.request.get('/api/v1/courses', { headers: AUTH })).json()
  const bd = courses.find((c: { name: string }) => c.name === 'Bases de Dados')
  await page.goto(`/cadeiras/${bd.id}`)
  const practice = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Treino' }) })
  await practice.getByRole('tab', { name: 'Repetir erradas' }).click()
  await practice.getByRole('button', { name: /^Começar/ }).dblclick()
  await expect(page.getByRole('heading', { name: 'Repetir erradas' })).toBeVisible()
  await expect(page.getByText('Pergunta 1 de 1')).toBeVisible() // only the one answered wrong before
  await page.getByText('O NIF').click()
  await page.getByRole('button', { name: 'Responder' }).dblclick()
  await expect(page.getByText('Certo.')).toBeVisible()

  await page.goto(`/cadeiras/${bd.id}`)
  await practice.getByRole('tab', { name: 'Exame cronometrado' }).click()
  await practice.getByLabel(/Minutos/).fill('5x') // letters are ignored
  await practice.getByRole('button', { name: /^Começar/ }).dblclick()
  await expect(page.getByRole('timer')).toContainText(/[45]:\d\d/)
  await page.getByText('A morada').click()
  await page.getByRole('button', { name: 'Guardar resposta' }).dblclick()
  await expect(page.getByText('Resposta guardada; vês a nota no fim.')).toBeVisible()
  await expect(page.getByLabel('resposta certa')).toHaveCount(0) // no grade during the exam
  await page.getByText('O NIF').click() // changes its mind
  await page.getByRole('button', { name: 'Guardar alteração' }).click()
  await page.getByRole('button', { name: 'Seguinte' }).click()
  await expect(page.getByText('SQL (SQLite)')).toBeVisible()
  await page.getByLabel('O teu código').fill('SELECT nome FROM aluno WHERE nota > 9;')
  await page.getByRole('button', { name: 'Guardar resposta' }).click()
  await page.getByRole('button', { name: 'Seguinte' }).click()
  await page.getByLabel('A tua resposta').fill('CREATE TABLE cliente (nif CHAR(9) PRIMARY KEY);')
  await page.getByRole('button', { name: 'Guardar resposta' }).click()
  await expect(page.getByText('Resposta guardada; vês a nota no fim.')).toBeVisible()
  await page.getByRole('button', { name: 'Entregar agora' }).dblclick()
  await expect(page.getByRole('heading', { name: 'Resultado' })).toBeVisible()
  await expect(page.getByRole('timer')).toHaveCount(0)

  await page.getByRole('button', { name: /^Pergunta 1,/ }).click()
  await expect(page.getByLabel('resposta certa')).toBeVisible()
  await expect(page.getByLabel('a tua resposta')).toHaveCount(0) // graded on the changed answer
  await page.getByRole('button', { name: /^Pergunta 2,/ }).click()
  await expect(page.getByText('Passou em todos os testes.')).toBeVisible()
  await page.getByRole('button', { name: /^Pergunta 3,/ }).click()
  await expect(page.getByText('nota estimada pelo modelo')).toBeVisible()
  await expect(page.getByText('6 em 10')).toBeVisible()
  await page.getByLabel('A tua nota (0 a 10)').fill('11')
  await expect(page.getByRole('button', { name: 'Corrigir nota' })).toBeDisabled()
  await page.getByLabel('A tua nota (0 a 10)').fill('7,5')
  await page.getByRole('button', { name: 'Corrigir nota' }).dblclick()
  await expect(page.getByText('nota confirmada por ti')).toBeVisible()
  await expect(page.getByText('7,5 em 10')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
})
