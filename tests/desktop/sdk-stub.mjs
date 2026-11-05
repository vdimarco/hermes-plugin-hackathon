// The slice of @hermes/plugin-sdk the plugins use, with the same shapes.
export function atom(value) {
  const listeners = new Set()
  return {
    get: () => value,
    set: next => { value = next; listeners.forEach(fn => fn(value)) },
    listen: fn => { listeners.add(fn); return () => listeners.delete(fn) }
  }
}
export const useValue = a => a.get()
export const notifications = []
export const host = {
  state: {
    focusedStoredSessionId: atom(null),
    focusedSessionId: atom(null),
    activeSessionId: atom(null)
  },
  notify: n => { notifications.push(n); return notifications.length }
}
export const haptic = () => {}
export const PANES_AREA = 'panes'
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const PALETTE_AREA = 'palette'
function component(name) {
  const c = props => ({ type: name, props, key: undefined })
  Object.defineProperty(c, 'name', { value: name })
  return c
}
export const Badge = component('Badge')
export const StatusDot = component('StatusDot')
export const EmptyState = component('EmptyState')
export const CopyButton = component('CopyButton')
