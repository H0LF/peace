// PEACE tab bridge. The native host (com.peace.browser) relays requests from the PEACE desktop agent; every
// message is validated here again, only normal windows are touched, and pages are never read: a tab is its
// id, window, position, group, title and its address without query string or fragment (those can hold tokens).

export const HOST = 'com.peace.browser'
export const COLORS = ['grey', 'blue', 'red', 'yellow', 'green', 'pink', 'purple', 'cyan', 'orange']
const MAX_TABS = 50

const isId = (v) => Number.isInteger(v) && v >= 0 && v < 2 ** 31
const isIds = (v) => Array.isArray(v) && v.length >= 1 && v.length <= MAX_TABS && v.every(isId)
const isText = (v, max) => typeof v === 'string' && v.length >= 1 && v.length <= max

export function webUrl(v) {
  if (typeof v !== 'string' || v.length > 2000) return null
  try {
    const url = new URL(v)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

export function safeUrl(v) {
  try {
    const url = new URL(v)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin + url.pathname : url.protocol
  } catch {
    return ''
  }
}

// op -> argument check; anything not listed is refused.
const OPS = {
  list: (a) => Object.keys(a).length === 0,
  open: (a) => webUrl(a.url) !== null && Object.keys(a).length === 1,
  focus: (a) => isId(a.tabId) && Object.keys(a).length === 1,
  move: (a) => isIds(a.tabIds) && Number.isInteger(a.index) && a.index >= -1 && a.index <= 500
    && (a.windowId === undefined || isId(a.windowId)) && Object.keys(a).every((k) => ['tabIds', 'index', 'windowId'].includes(k)),
  group: (a) => isIds(a.tabIds) && isText(a.title, 40) && (a.color === undefined || COLORS.includes(a.color))
    && Object.keys(a).every((k) => ['tabIds', 'title', 'color'].includes(k)),
  ungroup: (a) => isIds(a.tabIds) && Object.keys(a).length === 1,
  close: (a) => isIds(a.tabIds) && Object.keys(a).length === 1,
}

export function check(msg) {
  if (!msg || typeof msg !== 'object' || Array.isArray(msg)) return 'mensagem inválida'
  if (!isText(msg.id, 64) || !/^[A-Za-z0-9_-]+$/.test(msg.id)) return 'id inválido'
  if (!Object.hasOwn(OPS, msg.op)) return 'operação desconhecida'
  const args = msg.args
  if (!args || typeof args !== 'object' || Array.isArray(args) || !OPS[msg.op](args)) return 'argumentos inválidos'
  return null
}

export function describe(tab) {
  return { id: tab.id, windowId: tab.windowId, index: tab.index, active: tab.active, pinned: tab.pinned,
    groupId: tab.groupId, title: (tab.title || '').slice(0, 200), url: safeUrl(tab.pendingUrl || tab.url || '') }
}

async function normalTabs(api) {
  const windows = await api.windows.getAll({ populate: true, windowTypes: ['normal'] })
  return windows.filter((w) => !w.incognito).flatMap((w) => w.tabs.map(describe))
}

// Requests may only name tabs that are in normal, non-incognito windows right now.
async function ownTabs(api, ids) {
  const known = new Set((await normalTabs(api)).map((t) => t.id))
  if (!ids.every((id) => known.has(id))) throw new Error('separador desconhecido')
  return ids
}

export async function perform(api, op, args) {
  switch (op) {
    case 'list':
      return normalTabs(api)
    case 'open': {
      const win = await api.windows.getLastFocused({ windowTypes: ['normal'] })
      const tab = await api.tabs.create({ url: webUrl(args.url), active: true, ...(win && !win.incognito ? { windowId: win.id } : {}) })
      return describe(tab)
    }
    case 'focus': {
      const [id] = await ownTabs(api, [args.tabId])
      const tab = await api.tabs.update(id, { active: true })
      await api.windows.update(tab.windowId, { focused: true })
      return describe(tab)
    }
    case 'move': {
      const ids = await ownTabs(api, args.tabIds)
      if (args.windowId !== undefined) {
        const win = await api.windows.get(args.windowId)
        if (win.type !== 'normal' || win.incognito) throw new Error('janela não permitida')
      }
      const moved = await api.tabs.move(ids, { index: args.index, ...(args.windowId !== undefined ? { windowId: args.windowId } : {}) })
      return [].concat(moved).map(describe)
    }
    case 'group': {
      const ids = await ownTabs(api, args.tabIds)
      const groups = await api.tabGroups.query({ title: args.title })
      const groupId = await api.tabs.group(groups.length ? { tabIds: ids, groupId: groups[0].id } : { tabIds: ids })
      await api.tabGroups.update(groupId, { title: args.title, ...(args.color ? { color: args.color } : {}) })
      return groupId
    }
    case 'ungroup':
      await api.tabs.ungroup(await ownTabs(api, args.tabIds))
      return null
    case 'close':
      await api.tabs.remove(await ownTabs(api, args.tabIds))
      return null
  }
  throw new Error('operação desconhecida')
}

export async function handle(api, msg) {
  const problem = check(msg)
  if (problem) return { id: typeof msg?.id === 'string' ? msg.id.slice(0, 64) : null, ok: false, error: problem }
  try {
    return { id: msg.id, ok: true, result: await perform(api, msg.op, msg.args) }
  } catch (e) {
    return { id: msg.id, ok: false, error: String(e?.message || e).slice(0, 200) }
  }
}

export function connect(api, delay = 1000) {
  const port = api.runtime.connectNative(HOST)
  port.onMessage.addListener(async (msg) => port.postMessage(await handle(api, msg)))
  // The host exits when Brave closes the port or it fails: reconnect, slower each time (at most every minute).
  port.onDisconnect.addListener(() => setTimeout(() => connect(api, Math.min(delay * 2, 60000)), delay))
  return port
}

if (globalThis.chrome?.runtime?.connectNative) {
  connect(globalThis.chrome)
}
