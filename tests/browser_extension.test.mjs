// The PEACE Brave extension's message handling, against a fake chrome API (node --test; run by test_browser_bridge.py).
import assert from 'node:assert/strict'
import test from 'node:test'
import { check, connect, handle, safeUrl, webUrl } from '../desktop/brave/extension/background.js'

function fakeChrome() {
  const calls = []
  const windows = [
    { id: 10, type: 'normal', incognito: false, tabs: [
      { id: 1, windowId: 10, index: 0, active: true, pinned: false, groupId: -1, title: 'Moodle', url: 'https://moodle.example/mod/resource/view.php?id=7&token=SECRET#x' },
      { id: 2, windowId: 10, index: 1, active: false, pinned: false, groupId: -1, title: 'Extensões', url: 'chrome://extensions/' },
    ] },
    { id: 20, type: 'normal', incognito: true, tabs: [{ id: 5, windowId: 20, index: 0, title: 'privado', url: 'https://private.example/' }] },
  ]
  const record = (name, result) => async (...args) => { calls.push([name, ...args]); return typeof result === 'function' ? result(...args) : result }
  return {
    calls,
    windows: {
      getAll: record('windows.getAll', () => windows),
      getLastFocused: record('windows.getLastFocused', windows[0]),
      get: record('windows.get', (id) => windows.find((w) => w.id === id)),
      update: record('windows.update', {}),
    },
    tabs: {
      create: record('tabs.create', (o) => ({ id: 9, windowId: o.windowId, index: 2, active: true, groupId: -1, title: '', url: '', pendingUrl: o.url })),
      update: record('tabs.update', (id) => ({ ...windows[0].tabs.find((t) => t.id === id), active: true })),
      move: record('tabs.move', (ids) => ids.map((id) => windows[0].tabs.find((t) => t.id === id))),
      group: record('tabs.group', 33),
      ungroup: record('tabs.ungroup', undefined),
      remove: record('tabs.remove', undefined),
    },
    tabGroups: { query: record('tabGroups.query', []), update: record('tabGroups.update', {}) },
  }
}

test('messages are validated before any browser call', async () => {
  const api = fakeChrome()
  for (const bad of [null, [], 'x', { id: 'a', op: 'eval', args: {} }, { id: 'a b', op: 'list', args: {} },
    { id: 'a', op: 'list', args: { extra: 1 } }, { id: 'a', op: 'open', args: { url: 'javascript:alert(1)' } },
    { id: 'a', op: 'open', args: { url: 'file:///etc/passwd' } }, { id: 'a', op: 'close', args: { tabIds: [] } },
    { id: 'a', op: 'close', args: { tabIds: [1.5] } }, { id: 'a', op: 'group', args: { tabIds: [1], title: 'x', color: 'black' } },
    { id: 'a', op: 'move', args: { tabIds: [1], index: 9999 } }, { id: 'a', op: '__proto__', args: {} }]) {
    const reply = await handle(api, bad)
    assert.equal(reply.ok, false, JSON.stringify(bad))
  }
  assert.deepEqual(api.calls, [])
  assert.equal(check({ id: 'abc_1', op: 'list', args: {} }), null)
})

test('tabs are listed from normal windows only, without query strings or fragments', async () => {
  const reply = await handle(fakeChrome(), { id: 'r1', op: 'list', args: {} })
  assert.equal(reply.ok, true)
  assert.deepEqual(reply.result.map((t) => t.id), [1, 2])
  assert.equal(reply.result[0].url, 'https://moodle.example/mod/resource/view.php')
  assert.equal(reply.result[1].url, 'chrome:')
  assert.equal(safeUrl('not a url'), '')
  assert.equal(webUrl('https://a.example/x?y=1'), 'https://a.example/x?y=1')
})

test('only tabs of normal windows can be changed', async () => {
  const api = fakeChrome()
  const reply = await handle(api, { id: 'r2', op: 'close', args: { tabIds: [1, 5] } })
  assert.deepEqual([reply.ok, reply.error], [false, 'separador desconhecido'])
  assert.ok(!api.calls.some(([name]) => name === 'tabs.remove'))
  assert.equal((await handle(api, { id: 'r3', op: 'move', args: { tabIds: [1], index: 0, windowId: 20 } })).error, 'janela não permitida')
})

test('open, focus, move, group, ungroup and close call the tabs API', async () => {
  const api = fakeChrome()
  const opened = await handle(api, { id: 'o', op: 'open', args: { url: 'https://moodle.example/course/view.php?id=4' } })
  assert.deepEqual([opened.ok, opened.result.id, opened.result.url], [true, 9, 'https://moodle.example/course/view.php'])
  assert.equal((await handle(api, { id: 'f', op: 'focus', args: { tabId: 1 } })).ok, true)
  assert.equal((await handle(api, { id: 'm', op: 'move', args: { tabIds: [2], index: 0 } })).ok, true)
  const grouped = await handle(api, { id: 'g', op: 'group', args: { tabIds: [1, 2], title: 'SO', color: 'blue' } })
  assert.deepEqual([grouped.ok, grouped.result], [true, 33])
  assert.equal((await handle(api, { id: 'u', op: 'ungroup', args: { tabIds: [2] } })).ok, true)
  assert.equal((await handle(api, { id: 'c', op: 'close', args: { tabIds: [2] } })).ok, true)
  assert.deepEqual(api.calls.filter(([n]) => n.startsWith('tab')).map(([n]) => n),
    ['tabs.create', 'tabs.update', 'tabs.move', 'tabGroups.query', 'tabs.group', 'tabGroups.update', 'tabs.ungroup', 'tabs.remove'])
  assert.deepEqual(api.calls.find(([n]) => n === 'tabGroups.update').slice(1), [33, { title: 'SO', color: 'blue' }])
})

test('API failures are reported with the request id', async () => {
  const api = fakeChrome()
  api.tabs.remove = async () => { throw new Error('No tab with id: 2.') }
  assert.deepEqual(await handle(api, { id: 'x', op: 'close', args: { tabIds: [2] } }), { id: 'x', ok: false, error: 'No tab with id: 2.' })
})

test('the native port answers each message and reconnects when it drops', async () => {
  const listeners = { message: [], disconnect: [] }
  const posted = []
  const api = fakeChrome()
  let connects = 0
  api.runtime = { connectNative: (name) => {
    connects++
    assert.equal(name, 'com.peace.browser')
    return { postMessage: (m) => posted.push(m), onMessage: { addListener: (f) => listeners.message.push(f) },
      onDisconnect: { addListener: (f) => listeners.disconnect.push(f) } }
  } }
  connect(api, 1)
  await listeners.message[0]({ id: 'p1', op: 'list', args: {} })
  assert.equal(posted[0].id, 'p1')
  listeners.disconnect[0]()
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(connects, 2)
})
