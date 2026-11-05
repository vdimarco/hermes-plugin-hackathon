// Multiverse: desktop half.
//
// A live race board. While the subagents work, each universe shows its status and how
// many tool calls it has made; after judging, the board shows checks passed, the diff
// and the winner. The Python half pushes `plugin.multiverse.run` events
// (hermes_cli.plugin_events); this file only listens. The latest runs are kept in
// ctx.storage. Plain ESM: no JSX, no build step.
import {
  host,
  useValue,
  atom,
  PANES_AREA,
  STATUSBAR_AREAS,
  Badge,
  StatusDot,
  EmptyState
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'multiverse'
const MAX_RUNS = 10
const RUN_BADGE = { open: 'default', judged: 'success', adopted: 'success', closed: 'muted' }
const RUN_LABEL = { open: 'racing', judged: 'judged', adopted: 'adopted', closed: 'closed' }

const $runs = atom({})
const $lastRun = atom('')

export function rememberRun(storage, run) {
  if (!run || typeof run !== 'object' || typeof run.id !== 'string' || !run.id || !Array.isArray(run.universes)) {
    return false
  }
  const next = { ...$runs.get(), [run.id]: { ...run, seen: Date.now() } }
  const ids = Object.keys(next).sort((a, b) => (next[a].seen || 0) - (next[b].seen || 0))
  while (ids.length > MAX_RUNS) {
    delete next[ids.shift()]
  }
  $runs.set(next)
  $lastRun.set(run.id)
  storage.set('runs', next)
  storage.set('lastRun', run.id)
  return true
}

export function pickRun(all, focusedSession, last) {
  const runs = Object.values(all)
  const mine = focusedSession ? runs.filter(r => r.session === focusedSession) : []
  if (mine.length) {
    return mine.sort((a, b) => (b.seen || 0) - (a.seen || 0))[0]
  }
  return all[last] || null
}

export function universeTone(u, run) {
  if (u.status === 'failed') return 'bad'
  if (u.status === 'running' || u.status === 'open') return 'muted'
  if (run.winner && u.label === run.winner) return 'good'
  if (u.total) return u.passed === u.total ? 'good' : u.passed ? 'warn' : 'bad'
  return 'muted'
}

export function universeDetail(u) {
  if (u.status === 'running' || u.status === 'open') {
    return `${u.status === 'open' ? 'waiting' : 'working'} · ${u.calls || 0} tool calls`
  }
  const parts = []
  if (u.total) parts.push(`${u.passed}/${u.total} checks`)
  if (u.files != null) parts.push(`+${u.insertions || 0} −${u.deletions || 0} in ${u.files} files`)
  if (u.duration_ms) parts.push(`${Math.round(u.duration_ms / 1000)}s`)
  if (u.status === 'failed') parts.push('subagent failed')
  return parts.join(' · ') || `${u.calls || 0} tool calls`
}

function useCurrent() {
  const all = useValue($runs)
  const last = useValue($lastRun)
  const focusedRuntime = useValue(host.state.focusedSessionId)
  const focusedStored = useValue(host.state.focusedStoredSessionId)
  return pickRun(all, focusedStored || focusedRuntime, last)
}

function UniverseRow({ u, run }) {
  const winner = run.winner && u.label === run.winner
  return jsxs('div', {
    className: 'flex flex-col gap-0.5 py-1.5',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(StatusDot, { tone: universeTone(u, run) }),
          jsx('span', { className: 'text-xs font-medium', children: winner ? `🏆 ${u.label}` : u.label }),
          u.model ? jsx(Badge, { variant: 'outline', size: 'xs', children: u.model }) : null,
          jsx('span', { className: 'ml-auto shrink-0 text-[0.65rem] text-(--ui-text-tertiary)', children: universeDetail(u) })
        ]
      }),
      jsx('div', { className: 'truncate pl-3.5 text-[0.65rem] text-(--ui-text-tertiary)', title: u.approach, children: u.approach })
    ]
  })
}

export function MultiversePane() {
  const run = useCurrent()
  if (!run) {
    return jsx(EmptyState, {
      title: 'No race yet',
      description: 'Ask the agent to race a hard fix: "Race three fixes for this bug; the check is pytest -q."'
    })
  }
  const universes = Array.isArray(run.universes) ? run.universes : []
  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-y-auto p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'line-clamp-3 font-medium leading-snug', title: run.goal, children: run.goal }),
          jsxs('div', {
            className: 'flex flex-wrap items-center gap-2 text-xs text-(--ui-text-tertiary)',
            children: [
              jsx(Badge, { variant: RUN_BADGE[run.status] || 'muted', children: RUN_LABEL[run.status] || run.status }),
              `${universes.length} universes · base ${run.base_kind || 'HEAD'} ${run.base || ''}`,
              run.adopted ? ` · adopted ${run.adopted}` : null
            ]
          })
        ]
      }),
      jsx('div', {
        className: 'flex flex-col divide-y divide-(--ui-stroke-secondary)',
        children: universes.map(u => jsx(UniverseRow, { u, run }, u.label))
      }),
      (run.checks || []).length
        ? jsxs('div', {
            className: 'flex flex-col gap-1',
            children: [
              jsx('div', { className: 'text-[0.65rem] uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Checks' }),
              ...run.checks.map(c => jsx('code', { className: 'truncate font-mono text-[0.65rem] text-(--ui-text-secondary)', title: c, children: c }, c))
            ]
          })
        : null
    ]
  })
}

export function MultiverseChip() {
  const run = useCurrent()
  if (!run || run.status !== 'open') {
    return null
  }
  const universes = Array.isArray(run.universes) ? run.universes : []
  const working = universes.filter(u => u.status === 'running').length
  const calls = universes.reduce((n, u) => n + (u.calls || 0), 0)
  return jsxs('span', {
    className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    title: run.goal,
    children: [jsx(StatusDot, { tone: 'muted' }), `racing ${working}/${universes.length} · ${calls} calls`]
  })
}

export default {
  id: ID,
  name: 'Multiverse',
  register(ctx) {
    const saved = ctx.storage.get('runs', {})
    $runs.set(saved && typeof saved === 'object' ? saved : {})
    $lastRun.set(ctx.storage.get('lastRun', ''))

    ctx.onEvent(`plugin.${ID}.run`, event => {
      const before = $runs.get()[event && event.payload && event.payload.id]
      if (!rememberRun(ctx.storage, event && event.payload)) {
        return
      }
      const run = event.payload
      if (run.status === 'judged' && (!before || before.status !== 'judged')) {
        host.notify({
          kind: run.winner ? 'success' : 'warning',
          title: 'Multiverse',
          message: run.winner ? `Winner: ${run.winner}. Ask the agent to adopt it.` : 'No universe passed every check.'
        })
      }
    })

    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Multiverse',
        data: { placement: 'right', width: '320px' },
        render: () => jsx(MultiversePane, {})
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 141,
        render: () => jsx(MultiverseChip, {})
      }
    ])
  }
}
