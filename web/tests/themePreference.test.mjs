import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveInitialTheme } from '../src/utils/themePreference.mjs'

test('saved theme wins over the system preference', () => {
  assert.equal(resolveInitialTheme('light', true), 'light')
  assert.equal(resolveInitialTheme('dark', false), 'dark')
})

test('the system preference is used when no valid value exists', () => {
  assert.equal(resolveInitialTheme(null, true), 'dark')
  assert.equal(resolveInitialTheme('invalid', false), 'light')
})
