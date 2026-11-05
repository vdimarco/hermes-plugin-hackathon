// Vouch: desktop half.
//
// A status-bar chip with the verdicts on the focused chat's latest reply:
// ✓ verified, ⚠ unverified or stale, ✗ contradicted. Clicking it lists the claims
// that need attention. When Vouch sends the agent back to fix a claim, a toast says
// so. The Python half pushes `plugin.vouch.report` events (hermes_cli.plugin_events);
// this file only listens. Plain ESM: no JSX, no build step.
import { host, useValue, atom, haptic, STATUSBAR_AREAS, StatusDot } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'vouch'
const MAX_SESSIONS = 20

const $reports = atom({})
const $lastSession = atom('')

export function rememberReport(storage, report) {
  if (!report || typeof report !== 'object' || typeof report.session !== 'string' || !report.session || !report.counts) {
    return false
  }
  const next = { ...$reports.get(), [report.session]: report }
  const keys = Object.keys(next).sort((a, b) => (next[a].ts || 0) - (next[b].ts || 0))
  while (keys.length > MAX_SESSIONS) {
    delete next[keys.shift()]
  }
  $reports.set(next)
  $lastSession.set(report.session)
  storage.set('reports', next)
  storage.set('lastSession', report.session)
  return true
}

export function pickReport(all, focusedStored, focusedRuntime, last) {
  return (focusedStored && all[focusedStored]) || (focusedRuntime && all[focusedRuntime]) || all[last] || null
}

export function summarize(report) {
  const c = report.counts || {}
  const verified = c.verified || 0
  const soft = (c.unverified || 0) + (c.stale || 0)
  const bad = c.contradicted || 0
  const tone = bad ? 'bad' : soft ? 'warn' : 'good'
  return { verified, soft, bad, tone, text: `vouch ✓${verified}${soft ? ` ⚠${soft}` : ''}${bad ? ` ✗${bad}` : ''}` }
}

export function problemLines(report) {
  return (report.problems || []).map(p => `${p.status === 'contradicted' ? '✗' : '⚠'} ${p.label}: ${p.reason}`)
}

function showDetails(report) {
  const lines = problemLines(report)
  host.notify({
    kind: lines.length ? 'warning' : 'success',
    title: 'Vouch',
    message: lines.length ? `${lines.length} claim${lines.length === 1 ? '' : 's'} not backed by the tool record` : 'Every claim in the last reply is backed by the tool record.',
    detail: lines.length ? lines.join('\n') : undefined
  })
}

export function VouchChip() {
  const all = useValue($reports)
  const last = useValue($lastSession)
  const focusedStored = useValue(host.state.focusedStoredSessionId)
  const focusedRuntime = useValue(host.state.focusedSessionId)
  const report = pickReport(all, focusedStored, focusedRuntime, last)
  if (!report) {
    return null
  }
  const s = summarize(report)
  return jsxs('button', {
    type: 'button',
    className: 'flex items-center gap-1 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)',
    title: 'Vouch: claims in the last reply checked against the tool record',
    onClick: () => {
      haptic('tap')
      showDetails(report)
    },
    children: [jsx(StatusDot, { tone: s.tone }), s.text]
  })
}

export default {
  id: ID,
  name: 'Vouch',
  register(ctx) {
    const saved = ctx.storage.get('reports', {})
    $reports.set(saved && typeof saved === 'object' ? saved : {})
    $lastSession.set(ctx.storage.get('lastSession', ''))

    ctx.onEvent(`plugin.${ID}.report`, event => {
      const report = event && event.payload
      if (!rememberReport(ctx.storage, report)) {
        return
      }
      if (report.stage === 'draft') {
        host.notify({
          kind: 'info',
          title: 'Vouch',
          message: 'Sent the agent back: its draft reply claimed something the tool record does not show.',
          detail: problemLines(report).join('\n') || undefined
        })
      }
    })

    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 139,
      render: () => jsx(VouchChip, {})
    })
  }
}
