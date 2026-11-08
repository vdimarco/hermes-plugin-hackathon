// Tests for the three desktop halves. Run: node --import ./tests/desktop/register.mjs tests/desktop/test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { host, notifications } from '@hermes/plugin-sdk'

const contract = await import('../../plugins/contract/desktop/plugin.js')
const multiverse = await import('../../plugins/multiverse/desktop/plugin.js')

// -- a tiny renderer: expands function components into plain element objects ---------------
function render(node) {
  if (node == null || node === false || node === true) return null
  if (Array.isArray(node)) return node.map(render)
  if (typeof node !== 'object') return node
  if (typeof node.type === 'function') {
    const out = node.type(node.props || {})
    // SDK stand-ins return {type: 'Name', props}; keep them as leaves with rendered children
    if (out && typeof out.type === 'string' && out.type === node.type.name) {
      return { type: out.type, props: { ...out.props, children: render(out.props.children) } }
    }
    return render(out)
  }
  return { type: node.type, props: { ...node.props, children: render(node.props && node.props.children) } }
}
function text(tree) {
  if (tree == null) return ''
  if (Array.isArray(tree)) return tree.map(text).join(' ')
  if (typeof tree !== 'object') return String(tree)
  const own = ['title', 'description'].map(k => (typeof tree.props[k] === 'string' ? tree.props[k] : '')).join(' ')
  return `${own} ${text(tree.props.children)}`.replace(/\s+/g, ' ')
}
function findAll(tree, type, out = []) {
  if (tree == null || typeof tree !== 'object') return out
  if (Array.isArray(tree)) { tree.forEach(t => findAll(t, type, out)); return out }
  if (tree.type === type) out.push(tree)
  findAll(tree.props.children, type, out)
  return out
}

// -- a fake PluginContext ---------------------------------------------------------------------
function makeCtx() {
  const store = new Map()
  const ctx = {
    contributions: [],
    listeners: {},
    storage: {
      get: (k, fallback) => (store.has(k) ? JSON.parse(store.get(k)) : fallback),
      set: (k, v) => store.set(k, JSON.stringify(v)),
      remove: k => store.delete(k)
    },
    os: { writeClipboard: async text => { ctx.clipboard = text; return true } },
    onEvent: (type, fn) => { (ctx.listeners[type] ||= []).push(fn); return () => {} },
    register: c => { ctx.contributions.push(c); return () => {} },
    registerMany: cs => { cs.forEach(c => ctx.contributions.push(c)); return () => {} },
    emit: (type, payload) => (ctx.listeners[type] || []).forEach(fn => fn({ type, payload, session_id: '' })),
    area: area => ctx.contributions.filter(c => c.area === area),
    _store: store
  }
  return ctx
}

const CONTRACT_STATE = {
  session: 's1', id: 'abc', title: 'Fix double discount', status: 'active', summary: '1/2 checks pass',
  checks: [
    { id: 'c1', name: 'unit tests', command: 'pytest -q', state: 'pass', reason: 'exit 0' },
    { id: 'c2', name: 'cart value', command: 'python -c "..."', state: 'fail', reason: 'exit 1, expected 0' }
  ],
  locked: ['tests/*', 'conftest.py'], approved_by: 'user', receipt: null, ts: 1
}

test('contract: registers a pane, two chips and a palette command', () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  assert.equal(contract.default.id, 'contract')
  assert.deepEqual(ctx.contributions.map(c => c.area).sort(), ['palette', 'panes', 'statusBar.right', 'statusBar.right'])
  assert.deepEqual(Object.keys(ctx.listeners).sort(), ['plugin.contract.blocked', 'plugin.contract.report', 'plugin.contract.state'])
})

test('contract: empty state, then the checklist from a state event', () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  const pane = () => render(ctx.area('panes')[0].render())
  assert.match(text(pane()), /No contract in this chat/)
  assert.equal(render(ctx.contributions.find(c => c.id === 'chip').render()), null)
  ctx.emit('plugin.contract.state', CONTRACT_STATE)
  const t = text(pane())
  assert.match(t, /Fix double discount/)
  assert.match(t, /1\/2 checks pass/)
  assert.match(t, /pytest -q/)
  assert.match(t, /tests\/\*/)
  assert.deepEqual(findAll(pane(), 'StatusDot').map(d => d.props.tone), ['good', 'bad'])
  const chip = render(ctx.contributions.find(c => c.id === 'chip').render())
  assert.match(text(chip), /contract 1\/2/)
  assert.equal(findAll(chip, 'StatusDot')[0].props.tone, 'bad')
})

test('contract: met state shows the receipt and the palette copies it', async () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  const met = { ...CONTRACT_STATE, ts: 2, status: 'met', summary: '2/2 checks pass',
    checks: CONTRACT_STATE.checks.map(c => ({ ...c, state: 'pass' })),
    receipt: { hash: 'f'.repeat(64), tree: 'a'.repeat(40), stale: false } }
  ctx.emit('plugin.contract.state', met)
  const tree = render(ctx.area('panes')[0].render())
  assert.match(text(tree), /Receipt/)
  assert.equal(findAll(tree, 'CopyButton')[0].props.text, 'f'.repeat(64))
  ctx.area('palette')[0].data.run()
  await new Promise(r => setTimeout(r, 0))
  assert.equal(ctx.clipboard, 'f'.repeat(64))
})

test('contract: focused chat wins over the latest event, and storage survives a reload', () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  ctx.emit('plugin.contract.state', CONTRACT_STATE)
  ctx.emit('plugin.contract.state', { ...CONTRACT_STATE, session: 's2', title: 'Other task', ts: 3 })
  assert.match(text(render(ctx.area('panes')[0].render())), /Other task/)
  host.state.focusedStoredSessionId.set('s1')
  assert.match(text(render(ctx.area('panes')[0].render())), /Fix double discount/)
  host.state.focusedStoredSessionId.set(null)
  const reloaded = makeCtx()
  reloaded._store.set('snapshots', ctx._store.get('snapshots'))
  reloaded._store.set('lastSession', ctx._store.get('lastSession'))
  contract.default.register(reloaded)
  assert.match(text(render(reloaded.area('panes')[0].render())), /Other task/)
})

test('contract: a dropped contract clears the pane, bad payloads are ignored', () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  ctx.emit('plugin.contract.state', CONTRACT_STATE)
  ctx.emit('plugin.contract.state', { session: 's1', status: 'none', ts: 9 })
  assert.match(text(render(ctx.area('panes')[0].render())), /No contract/)
  for (const bad of [null, 'x', {}, { session: 5 }]) ctx.emit('plugin.contract.state', bad)
})

test('contract: a blocked edit toasts', () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  const before = notifications.length
  ctx.emit('plugin.contract.blocked', { path: 'tests/test_cart.py', lock: 'tests/*', tool: 'patch', title: 'Fix' })
  const n = notifications[before]
  assert.equal(n.kind, 'warning')
  assert.match(n.message, /tests\/test_cart\.py/)
})

const RUN = {
  id: 'r1', status: 'open', winner: null, base_kind: 'HEAD', base: '1a2b3c', session: 'p1', goal: 'Fix the discount',
  checks: ['pytest -q'],
  universes: [
    { label: 'u1', model: '', approach: 'direct', status: 'running', calls: 4 },
    { label: 'u2', model: 'vendor/model-b', approach: 'smallest', status: 'open', calls: 0 },
    { label: 'u3', model: '', approach: 'tests first', status: 'running', calls: 7 }
  ]
}

test('multiverse: live race, then a judged board with a winner toast', () => {
  const ctx = makeCtx()
  multiverse.default.register(ctx)
  assert.equal(multiverse.default.id, 'multiverse')
  assert.match(text(render(ctx.area('panes')[0].render())), /No race yet/)
  ctx.emit('plugin.multiverse.run', RUN)
  let t = text(render(ctx.area('panes')[0].render()))
  assert.match(t, /racing/)
  assert.match(t, /working · 4 tool calls/)
  assert.match(t, /vendor\/model-b/)
  assert.match(text(render(ctx.area('statusBar.right')[0].render())), /racing 2\/3 · 11 calls/)
  const before = notifications.length
  ctx.emit('plugin.multiverse.run', { ...RUN, status: 'judged', winner: 'u2', universes: [
    { label: 'u2', model: 'vendor/model-b', approach: 'smallest', status: 'done', calls: 9, passed: 1, total: 1, files: 1, insertions: 0, deletions: 1, duration_ms: 42000 },
    { label: 'u1', model: '', approach: 'direct', status: 'done', calls: 12, passed: 1, total: 1, files: 1, insertions: 1, deletions: 1 },
    { label: 'u3', model: '', approach: 'tests first', status: 'failed', calls: 7, passed: 0, total: 1 }
  ] })
  const tree = render(ctx.area('panes')[0].render())
  t = text(tree)
  assert.match(t, /🏆 u2/)
  assert.match(t, /1\/1 checks · \+0 −1 in 1 files · 42s/)
  assert.match(t, /subagent failed/)
  assert.deepEqual(findAll(tree, 'StatusDot').map(d => d.props.tone), ['good', 'good', 'bad'])
  assert.equal(render(ctx.area('statusBar.right')[0].render()), null)
  assert.equal(notifications.length, before + 1)
  assert.match(notifications[before].message, /Winner: u2/)
  ctx.emit('plugin.multiverse.run', { ...RUN, status: 'judged', winner: 'u2', universes: [] })
  assert.equal(notifications.length, before + 1, 'no second toast for the same judged run')
})

test('multiverse: runs of the focused chat come first', () => {
  const ctx = makeCtx()
  multiverse.default.register(ctx)
  ctx.emit('plugin.multiverse.run', RUN)
  ctx.emit('plugin.multiverse.run', { ...RUN, id: 'r2', session: 'p2', goal: 'Another goal' })
  assert.match(text(render(ctx.area('panes')[0].render())), /Another goal/)
  host.state.focusedStoredSessionId.set('p1')
  assert.match(text(render(ctx.area('panes')[0].render())), /Fix the discount/)
  host.state.focusedStoredSessionId.set(null)
  for (const bad of [null, {}, { id: 'x' }]) ctx.emit('plugin.multiverse.run', bad)
})

test('vouch chip: shows counts, a draft nudge toasts, a click lists the problems', () => {
  const ctx = makeCtx()
  contract.default.register(ctx)
  const chip = () => render(ctx.contributions.find(c => c.id === 'vouch-chip').render())
  assert.equal(chip(), null)
  const before = notifications.length
  const report = { session: 's1', stage: 'draft', ts: 1, counts: { verified: 1, unverified: 1, stale: 0, contradicted: 1 },
    problems: [{ kind: 'tests', status: 'contradicted', label: 'tests pass', reason: 'the last test run exited 1', claim: 'All tests pass.' },
               { kind: 'push', status: 'unverified', label: 'changes pushed', reason: 'no git push command ran', claim: 'Pushed.' }] }
  ctx.emit('plugin.contract.report', report)
  assert.equal(notifications[before].kind, 'info')
  assert.match(notifications[before].detail, /✗ tests pass: the last test run exited 1/)
  const tree = chip()
  assert.match(text(tree), /vouch ✓1 ⚠1 ✗1/)
  assert.equal(findAll(tree, 'StatusDot')[0].props.tone, 'bad')
  tree.props.onClick()
  assert.match(notifications.at(-1).message, /2 claims not backed/)
  ctx.emit('plugin.contract.report', { ...report, stage: 'final', ts: 2, counts: { verified: 3 }, problems: [] })
  assert.match(text(chip()), /vouch ✓3\s*$/)
  assert.equal(findAll(chip(), 'StatusDot')[0].props.tone, 'good')
})
