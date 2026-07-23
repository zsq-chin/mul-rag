import assert from 'node:assert/strict'
import test from 'node:test'

import createViteConfig from '../vite.config.js'

test('Vite polling ignores the project-local pnpm store', () => {
  const config = createViteConfig({ mode: 'test' })

  assert.ok(
    config.server.watch.ignored.includes('**/.pnpm-store/**'),
    'polling a project-local pnpm store can saturate the Vite dev server',
  )
})
