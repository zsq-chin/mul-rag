import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

// §9.3.2：生产前端不得残留 console.log/console.debug 调试语句。
// demo/、第三方压缩资源、以及显式的 DebugComponent 组件不在清理范围。
const SRC = fileURLToPath(new URL('../src/', import.meta.url))
const SKIP_SEGMENTS = ['demo', 'assets']
const SKIP_FILES = new Set(['DebugComponent.vue'])

function walk(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (!SKIP_SEGMENTS.includes(entry.name)) walk(p, out)
    } else if (entry.isFile()) {
      if (SKIP_FILES.has(entry.name)) continue
      if (/\.(vue|js|mjs)$/.test(entry.name)) out.push(p)
    }
  }
  return out
}

const files = walk(SRC)
assert.ok(files.length > 10, 'sanity: production source tree should have many files')

test('production web sources contain no live console.log / console.debug', () => {
  const offenders = []
  for (const file of files) {
    const lines = readFileSync(file, 'utf8').split(/\r?\n/)
    lines.forEach((raw, idx) => {
      const line = raw.trim()
      if (!line || line.startsWith('//') || line.startsWith('<!--') || line.startsWith('*')) return
      if (line.includes('console.log(') || line.includes('console.debug(')) {
        offenders.push(`${relative(SRC, file)}:${idx + 1}: ${line.slice(0, 90)}`)
      }
    })
  }
  assert.deepEqual(offenders, [], 'live console.log/debug found in production sources:\n' + offenders.join('\n'))
})
