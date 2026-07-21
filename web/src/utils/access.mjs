export const DEFAULT_ROUTE = '/chat'

export const NAVIGATION = [
  { key: 'chat', name: '智能问答', path: '/chat', roles: ['user', 'admin', 'superadmin'] },
  { key: 'users', name: '用户管理', action: 'users', roles: ['admin', 'superadmin'] },
  { key: 'graph', name: '知识图谱', path: '/graph', roles: ['superadmin'] },
  { key: 'database', name: '知识库', path: '/database', roles: ['superadmin'] },
  { key: 'statistics', name: '问答统计', path: '/statistics', roles: ['superadmin'] },
  { key: 'multimodal', name: '多模态知识库', path: '/multimodal-kb', roles: ['superadmin'] },
]

export const navigationForRole = role => NAVIGATION.filter(item => item.roles.includes(role))
export const canAccessRoute = (role, roles = []) => roles.length === 0 || roles.includes(role)
export const canUseKnowledgeRetrieval = role => ['user', 'admin', 'superadmin'].includes(role)
export const canUseGraph = role => role === 'superadmin'

const AUTHENTICATED_ROLES = Object.freeze(['user', 'admin', 'superadmin'])
const SUPERADMIN_ROLES = Object.freeze(['superadmin'])

export const rolesForRoute = path => (
  path === '/chat' ? [...AUTHENTICATED_ROLES] : [...SUPERADMIN_ROLES]
)

export const filterUsers = (users, query) => {
  const term = String(query || '').trim().toLowerCase()
  if (!term) return users

  return users.filter(user => [user.id, user.username, user.role]
    .some(value => String(value ?? '').toLowerCase().includes(term)))
}
