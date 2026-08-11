import { createRouter, createWebHistory } from 'vue-router'
import { message } from 'ant-design-vue'
import AppLayout from '@/layouts/AppLayout.vue';
import { useUserStore } from '@/stores/user';
import { canAccessRoute, rolesForRoute, DEFAULT_ROUTE } from '@/utils/access.mjs';
import MultimodalKbView from '@/views/MultimodalKbView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: DEFAULT_ROUTE
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { requiresAuth: false }
    },
    {
      path: '/chat',
      name: 'chat',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ChatComp',
          component: () => import('../views/ChatView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/chat') }
        }
      ]
    },
    {
      path: '/statistics',
      name: 'statistics',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'StatisticsComp',
          component: () => import('../views/AnswerStatistics.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/statistics') }
        }
      ]
    },
    {
      path: '/agent',
      name: 'AgentMain',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'AgentComp',
          component: () => import('../views/AgentView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/agent') }
        }
      ]
    },
    {
      path: '/agent/:agent_id',
      name: 'AgentSinglePage',
      component: () => import('../views/AgentSingleView.vue'),
      meta: { requiresAuth: true, roles: rolesForRoute('/agent/:agent_id') }
    },
    {
      path: '/graph',
      name: 'graph',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'GraphComp',
          component: () => import('../views/GraphView.vue'),
          meta: { keepAlive: false, requiresAuth: true, roles: rolesForRoute('/graph') }
        }
      ]
    },
    {
      path: '/database',
      name: 'database',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'DatabaseComp',
          component: () => import('../views/DataBaseView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/database') }
        },
        {
          path: ':database_id',
          name: 'DatabaseInfoComp',
          component: () => import('../views/DataBaseInfoView.vue'),
          meta: { keepAlive: false, requiresAuth: true, roles: rolesForRoute('/database') }
        }
      ]
    },
    {
      path: '/guide',
      name: 'guide',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'GuideComp',
          // component: () => import('../views/EmptyView.vue'),
          component: () => import('../views/GuideView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/guide') }
        }
      ]
    },
    {
      path: '/writer',
      name: 'writer',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'WriterComp',
          component: () => import('../views/WriterView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/writer') }
        }
      ]
    },
    {
      path: '/multimodal-kb',
      name: 'multimodal-kb',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'MultimodalKb',
          component: MultimodalKbView,
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/multimodal-kb') }
        }
      ]
    },
    {
      path: '/item',
      name: 'item',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'TopicComp',
          component: () => import('../views/ItemView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/item') }
        }
      ]
    },
    {
      path: '/datamining',
      name: 'datamining',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'DataminingComp',
          // component: () => import('../views/CollegeView.vue'),
          component: () => import('../views/DataminingView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/datamining') }
        }
      ]
    },
    {
      path: '/setting',
      name: 'setting',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'SettingComp',
          component: () => import('../views/SettingView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/setting') }
        }
      ]
    },
    {
      path: '/test',
      name: 'test',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'TestComp',
          component: () => import('../views/GuideView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/test') }
        }
      ]
    },
    {
      path: '/evaluation',
      name: 'evaluation',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'EvaluationComp',
          component: () => import('../views/EvaluationView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/evaluation') }
        }
      ]
    },
    {
      path: '/search',
      name: 'search',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'SearchComp',
          component: () => import('../views/SearchView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/search') }
        }
      ]
    },
        {
      path: '/liaohe',
      name: 'liaohe',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ShihuiComp',
          component: () => import('../views/ShiHuiView.vue'),
          meta: { keepAlive: true, requiresAuth: true, roles: rolesForRoute('/liaohe') }
        }
      ]
    },
    {
      path: '/exam/:id',
      name: 'exam',
      component: () => import('../views/ExamView.vue'),
      meta: { requiresAuth: true, roles: rolesForRoute('/exam/:id') }
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('../views/EmptyView.vue'),
      meta: { requiresAuth: false }
    },
  ]
})

// 全局前置守卫 — 基于角色的访问控制
router.beforeEach(async (to, from, next) => {
  const requiresAuth = to.matched.some(record => record.meta.requiresAuth === true)
  const userStore = useUserStore()

  if (userStore.isLoggedIn) {
    await userStore.hydrate()
  }

  const isLoggedIn = userStore.isLoggedIn

  // 未登录用户只能访问公开页面
  if (requiresAuth && !isLoggedIn) {
    sessionStorage.setItem('redirect', to.fullPath)
    next('/login')
    return
  }

  // 已登录用户访问登录页时重定向到默认页面
  if (to.path === '/login' && isLoggedIn) {
    next(DEFAULT_ROUTE)
    return
  }

  // 检查角色权限
  const requiredRoles = to.matched.reduce((roles, record) => {
    if (record.meta.roles) return record.meta.roles
    return roles
  }, null)

  if (requiresAuth && requiredRoles && !canAccessRoute(userStore.userRole, requiredRoles)) {
    message.warning('没有权限访问该功能')
    next(DEFAULT_ROUTE)
    return
  }

  next()
})

export default router
