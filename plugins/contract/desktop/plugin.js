// Contract: desktop half.
//
// Shows the definition-of-done checklist of the focused chat in a side pane and a
// status-bar chip, and toasts when the lock blocks an edit. The Python half pushes
// `plugin.contract.state` and `plugin.contract.blocked` events (hermes_cli.plugin_events);
// this file only listens. The last snapshot per chat is kept in ctx.storage so the
// pane is not empty after a reload. Plain ESM: no JSX, no build step.
import {
  host,
  useValue,
  atom,
  PANES_AREA,
  STATUSBAR_AREAS,
  PALETTE_AREA,
  Badge,
  StatusDot,
  EmptyState,
  CopyButton
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'contract'
const MAX_SESSIONS = 20
const TONE = { pass: 'good', fail: 'bad', stale: 'warn', pending: 'muted' }
const STATE_LABEL = { pass: 'pass', fail: 'fail', stale: 'stale', pending: 'not run' }
const STATUS_BADGE = { proposed: 'warn', active: 'default', met: 'success' }
const STATUS_LABEL = { proposed: 'proposed: run /contract approve', active: 'locked', met: 'met' }

const $snapshots = atom({})
const $lastSession = atom('')

export function rememberSnapshot(storage, snap) {
  if (!snap || typeof snap !== 'object' || typeof snap.session !== 'string' || !snap.session) {
    return false
  }
  const next = { ...$snapshots.get(), [snap.session]: snap }
  const keys = Object.keys(next).sort((a, b) => (next[a].ts || 0) - (next[b].ts || 0))
  while (keys.length > MAX_SESSIONS) {
    delete next[keys.shift()]
  }
  $snapshots.set(next)
  $lastSession.set(snap.session)
  storage.set('snapshots', next)
  storage.set('lastSession', snap.session)
  return true
}

export function pickSnapshot(all, focusedStored, focusedRuntime, last) {
  const snap = (focusedStored && all[focusedStored]) || (focusedRuntime && all[focusedRuntime]) || all[last] || null
  return snap && snap.status !== 'none' ? snap : null
}

function useCurrent() {
  const all = useValue($snapshots)
  const last = useValue($lastSession)
  const focusedStored = useValue(host.state.focusedStoredSessionId)
  const focusedRuntime = useValue(host.state.focusedSessionId)
  return pickSnapshot(all, focusedStored, focusedRuntime, last)
}

function CheckRow({ check }) {
  return jsxs('div', {
    className: 'flex flex-col gap-0.5 py-1.5',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(StatusDot, { tone: TONE[check.state] || 'muted' }),
          jsx('span', { className: 'truncate text-xs font-medium', children: `${check.id} ${check.name}` }),
          jsx('span', {
            className: 'ml-auto shrink-0 text-[0.65rem] text-(--ui-text-tertiary)',
            children: STATE_LABEL[check.state] || check.state
          })
        ]
      }),
      jsx('code', {
        className: 'truncate pl-3.5 font-mono text-[0.65rem] text-(--ui-text-secondary)',
        title: check.command,
        children: check.command
      }),
      check.reason
        ? jsx('div', {
            className: 'pl-3.5 text-[0.65rem] text-(--ui-text-tertiary)',
            children: check.state === 'stale' ? `${check.reason}, then files changed` : check.reason
          })
        : null
    ]
  })
}

export function ContractPane() {
  const snap = useCurrent()
  if (!snap) {
    return jsx(EmptyState, {
      title: 'No contract in this chat',
      description: 'Ask the agent to write a contract before a coding task, or type /contract add <command>.'
    })
  }
  const checks = Array.isArray(snap.checks) ? snap.checks : []
  const receipt = snap.receipt && snap.receipt.hash ? snap.receipt : null
  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-y-auto p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'font-medium leading-snug', children: snap.title }),
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(Badge, { variant: STATUS_BADGE[snap.status] || 'muted', children: STATUS_LABEL[snap.status] || snap.status }),
              jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: snap.summary })
            ]
          })
        ]
      }),
      jsx('div', {
        className: 'flex flex-col divide-y divide-(--ui-stroke-secondary)',
        children: checks.map(check => jsx(CheckRow, { check }, check.id))
      }),
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('div', { className: 'text-[0.65rem] uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Locked' }),
          jsx('div', {
            className: 'flex flex-wrap gap-1',
            children: (snap.locked || []).map(lock => jsx(Badge, { variant: 'outline', size: 'xs', children: lock }, lock))
          })
        ]
      }),
      receipt
        ? jsxs('div', {
            className: 'flex items-center gap-2 text-xs',
            children: [
              jsx('span', { className: 'text-(--ui-text-tertiary)', children: receipt.stale ? 'Old receipt' : 'Receipt' }),
              jsx('code', { className: 'font-mono', children: receipt.hash.slice(0, 16) }),
              jsx(CopyButton, { text: receipt.hash, appearance: 'icon', label: 'Copy receipt hash' }),
              receipt.tree
                ? jsx('span', { className: 'ml-auto font-mono text-[0.65rem] text-(--ui-text-quaternary)', title: 'git tree id of the checked code', children: `tree ${receipt.tree.slice(0, 10)}` })
                : null
            ]
          })
        : null
    ]
  })
}

export function ContractChip() {
  const snap = useCurrent()
  if (!snap) {
    return null
  }
  const checks = Array.isArray(snap.checks) ? snap.checks : []
  const tone = snap.status === 'met' ? 'good' : checks.some(c => c.state === 'fail') ? 'bad' : snap.status === 'proposed' ? 'warn' : 'muted'
  return jsxs('span', {
    className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    title: `Contract “${snap.title}”: ${snap.summary}`,
    children: [jsx(StatusDot, { tone }), `contract ${snap.summary.split(' ')[0]}`]
  })
}

export default {
  id: ID,
  name: 'Contract',
  register(ctx) {
    const saved = ctx.storage.get('snapshots', {})
    $snapshots.set(saved && typeof saved === 'object' ? saved : {})
    $lastSession.set(ctx.storage.get('lastSession', ''))

    ctx.onEvent(`plugin.${ID}.state`, event => {
      rememberSnapshot(ctx.storage, event && event.payload)
    })
    ctx.onEvent(`plugin.${ID}.blocked`, event => {
      const p = (event && event.payload) || {}
      host.notify({
        kind: 'warning',
        title: 'Contract lock',
        message: `Blocked a ${p.tool || 'tool'} edit to ${p.path || 'a locked file'}`,
        detail: p.lock ? `Locked by “${p.lock}” in “${p.title || 'the contract'}”.` : undefined
      })
    })

    ctx.registerMany([
      {
        id: 'pane',
        area: PANES_AREA,
        title: 'Contract',
        data: { placement: 'right', width: '300px' },
        render: () => jsx(ContractPane, {})
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 140,
        render: () => jsx(ContractChip, {})
      },
      {
        id: 'copy-receipt',
        area: PALETTE_AREA,
        data: {
          id: 'contract.copyReceipt',
          label: 'Contract: copy receipt hash',
          keywords: ['contract', 'receipt', 'hash'],
          run: () => {
            const snap = pickSnapshot($snapshots.get(), host.state.focusedStoredSessionId.get(), host.state.focusedSessionId.get(), $lastSession.get())
            const hash = snap && snap.receipt && snap.receipt.hash
            if (!hash) {
              host.notify({ kind: 'info', message: 'No contract receipt in this chat yet.' })
              return
            }
            void ctx.os.writeClipboard(hash).then(ok =>
              host.notify({ kind: ok ? 'success' : 'error', message: ok ? 'Receipt hash copied.' : 'Could not copy the receipt hash.' })
            )
          }
        }
      }
    ])
  }
}
