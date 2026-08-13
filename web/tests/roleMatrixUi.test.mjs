import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// 阶段 7：权限与用户管理——路由/菜单/角色刷新/空状态的源码级回归守卫。
// 与既有前端测试一致使用 readFileSync + assert.match 模式（本项目既定测试范式）；
// 后端三角色真实 HTTP 权限矩阵另由 test/test_role_matrix_api.py（TestClient）覆盖。

const router = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
const userStore = readFileSync(new URL('../src/stores/user.js', import.meta.url), 'utf8')
const forbiddenView = readFileSync(new URL('../src/views/ForbiddenView.vue', import.meta.url), 'utf8')
const userMgmt = readFileSync(
  new URL('../src/components/UserManagementComponent.vue', import.meta.url),
  'utf8',
)

test('router registers a /forbidden route', () => {
  assert.match(router, /path:\s*'\/forbidden'/)
  assert.match(router, /ForbiddenView\.vue/)
})

test('unauthorized navigation redirects to /forbidden instead of silently bouncing to chat', () => {
  assert.match(router, /next\(\{\s*path:\s*'\/forbidden'/)
  assert.match(router, /没有权限访问该功能/)
})

test('guard force-hydrates the role on every navigation (7.2.4)', () => {
  assert.match(router, /await\s+userStore\.hydrate\(true\)/)
})

test('forbidden page shows a 403 message without relying on a redirect', () => {
  assert.match(forbiddenView, /403/)
  assert.match(forbiddenView, /没有权限访问该功能/)
  assert.match(forbiddenView, /forbidden-card/)
})

test('store hydrate accepts a force flag', () => {
  assert.match(userStore, /async function hydrate\(force\s*=\s*false\)/)
  assert.match(userStore, /\(hydrated\s*&&\s*!force\)\s*\|\|\s*!token\.value/)
})

test('editing your own user record refreshes the local role immediately', () => {
  assert.match(userStore, /userId\s*===\s*userStore\.userId\s*&&\s*updated\.role/)
  assert.match(userStore, /localStorage\.setItem\('user_role',\s*updated\.role\)/)
})

test('user management table shows a designed empty state', () => {
  assert.match(userMgmt, /#emptyText/)
  assert.match(userMgmt, /暂无用户/)
})

test('admin actor never sees the role selector', () => {
  // 7.3.3：管理员表单不出现角色下拉，且提交时强制 effectiveRole = 'user'
  assert.match(userMgmt, /v-if="props\.actorRole === 'superadmin'"/)
  assert.match(userMgmt, /const effectiveRole = props\.actorRole === 'admin' \? 'user' : userManagement\.form\.role/)
})

test('delete button is disabled for the current user and for superadmin accounts of non-superadmins', () => {
  assert.match(
    userMgmt,
    /:disabled="record\.id === userStore\.userId \|\| \(record\.role === 'superadmin' && userStore\.userRole !== 'superadmin'\)"/,
  )
})
