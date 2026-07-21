import test from 'node:test'
import assert from 'node:assert/strict'
import { applyModelSelection } from '../src/utils/modelSelection.mjs'

test('personal selection sends only an opaque id', () => {
  const meta = applyModelSelection(
    { model_provider: 'openai', model_name: 'old-model', api_key: 'must-remove' },
    { kind: 'user', userModelId: 7, name: '生产模型' },
  )

  assert.equal(meta.user_model_id, 7)
  assert.equal('model_provider' in meta, false)
  assert.equal('model_name' in meta, false)
  assert.equal('api_key' in meta, false)
  assert.equal('api_base' in meta, false)
})

test('built-in selection clears the personal id', () => {
  const meta = applyModelSelection(
    { user_model_id: 7 },
    { kind: 'builtin', provider: 'openai', name: 'gpt-4.1' },
  )

  assert.equal(meta.user_model_id, undefined)
  assert.equal(meta.model_provider, 'openai')
  assert.equal(meta.model_name, 'gpt-4.1')
})

test('system default selection clears all explicit model fields', () => {
  const meta = applyModelSelection(
    { user_model_id: 7, model_provider: 'custom', model_name: 'old', api_base: 'secret' },
    { kind: 'builtin', provider: null, name: null },
  )

  for (const key of ['user_model_id', 'model_provider', 'model_name', 'api_key', 'api_base']) {
    assert.equal(key in meta, false, key)
  }
})
