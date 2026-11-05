// jsx() builds plain element objects; render() in the test expands function components.
export function jsx(type, props, key) {
  return { type, props: props || {}, key }
}
export const jsxs = jsx
export const Fragment = 'Fragment'
export default { createElement: (type, props, ...children) => jsx(type, { ...props, children }) }
