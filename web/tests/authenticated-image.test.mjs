import test from 'node:test'
import assert from 'node:assert/strict'
import { fetchAuthenticatedBlob } from '../src/utils/authenticated-image.mjs'

test('authenticated image requests send the bearer token without changing the URL', async () => {
  const calls = []
  const expectedBlob = new Blob(['image-bytes'], { type: 'image/png' })
  const fetchImpl = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, blob: async () => expectedBlob }
  }

  const blob = await fetchAuthenticatedBlob('/api/chat/multimodal/image?kbId=kb-1', 'secret-token', fetchImpl)

  assert.equal(blob, expectedBlob)
  assert.deepEqual(calls, [{
    url: '/api/chat/multimodal/image?kbId=kb-1',
    options: { headers: { Authorization: 'Bearer secret-token' }, signal: undefined },
  }])
})

test('authenticated image requests reject failed responses', async () => {
  await assert.rejects(
    fetchAuthenticatedBlob('/protected.png', 'token', async () => ({ ok: false, status: 401 })),
    /401/,
  )
})

test('authenticated image requests never send the token to another origin', async () => {
  let receivedHeaders
  await fetchAuthenticatedBlob(
    'https://images.example.test/result.png',
    'secret-token',
    async (_url, options) => {
      receivedHeaders = options.headers
      return { ok: true, blob: async () => new Blob([]) }
    },
    undefined,
    'https://knowledge.example.test',
  )

  assert.deepEqual(receivedHeaders, {})
})
