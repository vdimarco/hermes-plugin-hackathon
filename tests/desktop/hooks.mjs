// Resolve the three specifiers a Desktop plugin may import to local stand-ins.
const STUBS = {
  '@hermes/plugin-sdk': new URL('./sdk-stub.mjs', import.meta.url).href,
  react: new URL('./react-stub.mjs', import.meta.url).href,
  'react/jsx-runtime': new URL('./react-stub.mjs', import.meta.url).href
}

export async function resolve(specifier, context, next) {
  if (STUBS[specifier]) {
    return { url: STUBS[specifier], shortCircuit: true }
  }
  if (!specifier.startsWith('.') && !specifier.startsWith('/') && !specifier.startsWith('file:') && !specifier.startsWith('node:')) {
    throw new Error(`unsupported import in a desktop plugin: ${specifier}`)
  }
  return next(specifier, context)
}
