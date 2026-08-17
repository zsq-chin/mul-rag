import test from 'node:test'
import assert from 'node:assert/strict'
import {
  navigationForRole,
  canAccessRoute,
  canUseGraph,
  canUseKnowledgeRetrieval,
  filterUsers,
  rolesForRoute,
  DEFAULT_ROUTE,
} from '../src/utils/access.mjs'

test('ordinary user sees chat and knowledge dictionary', () => {
  assert.deepEqual(navigationForRole('user').map(item => item.key), ['chat', 'dictionary'])
})

test('admin sees chat and dictionary only', () => {
  assert.deepEqual(navigationForRole('admin').map(item => item.key), ['chat', 'dictionary'])
})

test('superadmin sees managed features', () => {
  const keys = navigationForRole('superadmin').map(item => item.key)
  assert.ok(keys.includes('graph'))
  assert.ok(keys.includes('database'))
  assert.ok(keys.includes('evaluation'))
  assert.ok(keys.includes('operations'))
  assert.ok(keys.includes('multimodal'))
})

test('superadmin-only pages never appear in user or admin nav', () => {
  const adminKeys = navigationForRole('admin').map(item => item.key)
  const userKeys = navigationForRole('user').map(item => item.key)
  for (const key of ['evaluation', 'operations', 'graph', 'database', 'statistics', 'multimodal']) {
    assert.ok(!adminKeys.includes(key), `admin must not see ${key}`)
    assert.ok(!userKeys.includes(key), `user must not see ${key}`)
  }
})

test('route roles are exact', () => {
  assert.equal(canAccessRoute('admin', ['superadmin']), false)
  assert.equal(canAccessRoute('superadmin', ['superadmin']), true)
})

test('chat and knowledge dictionary routes are shared by all authenticated roles', () => {
  assert.deepEqual(rolesForRoute('/chat'), ['user', 'admin', 'superadmin'])
  assert.deepEqual(rolesForRoute('/knowledge-dictionaries'), ['user', 'admin', 'superadmin'])

  for (const path of [
    '/agent', '/agent/:agent_id', '/guide', '/writer', '/item', '/datamining',
    '/setting', '/test', '/search', '/liaohe', '/exam/:id', '/graph',
    '/database', '/statistics', '/evaluation', '/operations', '/multimodal-kb',
  ]) {
    assert.deepEqual(rolesForRoute(path), ['superadmin'], path)
  }
})

test('user drawer search matches username, role, or id', () => {
  const users = [
    { id: 12, username: 'DrillAdmin', role: 'admin' },
    { id: 37, username: 'field-user', role: 'user' },
  ]

  assert.deepEqual(filterUsers(users, 'FIELD'), [users[1]])
  assert.deepEqual(filterUsers(users, 'admin'), [users[0]])
  assert.deepEqual(filterUsers(users, '37'), [users[1]])
  assert.deepEqual(filterUsers(users, '  '), users)
})

test('knowledge retrieval is shared while graph retrieval remains superadmin-only', () => {
  for (const role of ['user', 'admin', 'superadmin']) {
    assert.equal(canUseKnowledgeRetrieval(role), true)
  }
  assert.equal(canUseKnowledgeRetrieval(''), false)
  assert.equal(canUseGraph('user'), false)
  assert.equal(canUseGraph('admin'), false)
  assert.equal(canUseGraph('superadmin'), true)
})

test('DEFAULT_ROUTE is /chat and accessible to every authenticated role', () => {
  assert.equal(DEFAULT_ROUTE, '/chat')
  const roles = rolesForRoute(DEFAULT_ROUTE)
  assert.deepEqual(roles, ['user', 'admin', 'superadmin'])
  for (const role of roles) {
    assert.equal(canAccessRoute(role, roles), true, `role ${role} must reach DEFAULT_ROUTE`)
  }
})
