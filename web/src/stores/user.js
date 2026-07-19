import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useUserStore = defineStore('user', () => {
  // 状态
  const token = ref(localStorage.getItem('user_token') || '')
  const userId = ref(parseInt(localStorage.getItem('user_id') || '0') || null)
  const username = ref(localStorage.getItem('username') || '')
  const userRole = ref(localStorage.getItem('user_role') || '')

  // 计算属性
  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => userRole.value === 'admin' || userRole.value === 'superadmin')
  const isSuperAdmin = computed(() => userRole.value === 'superadmin')

  // 动作
  async function login(credentials) {
    try {
      const formData = new FormData()
      formData.append('username', credentials.username)
      formData.append('password', credentials.password)

      const response = await fetch('/api/auth/token', {
        method: 'POST',
        body: formData
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '登录失败')
      }

      const data = await response.json()

      // 更新状态
      token.value = data.access_token
      userId.value = data.user_id
      username.value = data.username
      userRole.value = data.role

      // 保存到本地存储
      localStorage.setItem('user_token', data.access_token)
      localStorage.setItem('user_id', data.user_id)
      localStorage.setItem('username', data.username)
      localStorage.setItem('user_role', data.role)

      return true
    } catch (error) {
      console.error('登录错误:', error)
      throw error
    }
  }

  // CAS单点登录
  async function casLogin() {
    try {
      // 获取CAS登录URL
      const response = await fetch('/api/auth/cas/login')
      if (!response.ok) {
        throw new Error('获取CAS登录地址失败')
      }

      const data = await response.json()
      
      // 跳转到CAS登录页面
      window.location.href = data.login_url
      
      return true
    } catch (error) {
      console.error('CAS登录错误:', error)
      throw error
    }
  }

  async function logout() {
    try {
      // 如果是CAS用户，先调用CAS登出
      const isCasUser = localStorage.getItem('is_cas_user') === 'true'
      if (isCasUser) {
        const response = await fetch('/api/auth/cas/logout', {
          headers: {
            ...getAuthHeaders()
          }
        })
        
        if (response.ok) {
          const data = await response.json()
          // 跳转到CAS登出页面进行全局登出，CAS登出后会通过service参数跳转回我们的登录页面
          if (data.logout_url) {
            window.location.href = data.logout_url
            performLocalLogout()
            return // 不继续执行下面的清理逻辑，因为页面会跳转
          }
        }
      }
    } catch (error) {
      console.error('CAS登出失败:', error)
      // 即使CAS登出失败，也要继续执行本地登出
    }

    // 对于非CAS用户或CAS登出失败的情况，执行本地登出并跳转到登录页
    performLocalLogout()
    window.location.href = '/login'
  }

  // 本地登出清理函数
  function performLocalLogout() {
    console.log('Performing local logout cleanup')
    // 清除状态
    token.value = ''
    userId.value = null
    username.value = ''
    userRole.value = ''

    // 清除本地存储
    localStorage.removeItem('user_token')
    localStorage.removeItem('user_id')
    localStorage.removeItem('username')
    localStorage.removeItem('user_role')
    localStorage.removeItem('is_cas_user')
  }

  async function initialize(admin) {
    try {
      const response = await fetch('/api/auth/initialize', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(admin)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '初始化管理员失败')
      }

      const data = await response.json()

      // 更新状态
      token.value = data.access_token
      userId.value = data.user_id
      username.value = data.username
      userRole.value = data.role

      // 保存到本地存储
      localStorage.setItem('user_token', data.access_token)
      localStorage.setItem('user_id', data.user_id)
      localStorage.setItem('username', data.username)
      localStorage.setItem('user_role', data.role)

      return true
    } catch (error) {
      console.error('初始化管理员错误:', error)
      throw error
    }
  }

  async function checkFirstRun() {
    try {
      const response = await fetch('/api/auth/check-first-run')
      const data = await response.json()
      return data.first_run
    } catch (error) {
      console.error('检查首次运行状态错误:', error)
      return false
    }
  }

  // 用于API请求的授权头
  function getAuthHeaders() {
    return {
      'Authorization': `Bearer ${token.value}`
    }
  }

  // 用户管理功能
  async function getUsers() {
    try {
      const response = await fetch('/api/auth/users', {
        headers: {
          ...getAuthHeaders()
        }
      })

      if (!response.ok) {
        throw new Error('获取用户列表失败')
      }

      return await response.json()
    } catch (error) {
      console.error('获取用户列表错误:', error)
      throw error
    }
  }

  async function createUser(userData) {
    try {
      const response = await fetch('/api/auth/users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(userData)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '创建用户失败')
      }

      return await response.json()
    } catch (error) {
      console.error('创建用户错误:', error)
      throw error
    }
  }

  async function updateUser(userId, userData) {
    try {
      const response = await fetch(`/api/auth/users/${userId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        },
        body: JSON.stringify(userData)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '更新用户失败')
      }

      return await response.json()
    } catch (error) {
      console.error('更新用户错误:', error)
      throw error
    }
  }

  async function deleteUser(userId) {
    try {
      const response = await fetch(`/api/auth/users/${userId}`, {
        method: 'DELETE',
        headers: {
          ...getAuthHeaders()
        }
      })
      console.log('deleteUser response:', response.ok)
      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '删除用户失败')
      }

      return await response.json()
    } catch (error) {
      console.error('删除用户错误:', error)
      throw error
    }
  }

  return {
    // 状态
    token,
    userId,
    username,
    userRole,

    // 计算属性
    isLoggedIn,
    isAdmin,
    isSuperAdmin,

    // 方法
    login,
  casLogin,
    logout,
    initialize,
    checkFirstRun,
    getAuthHeaders,
    getUsers,
    createUser,
    updateUser,
    deleteUser
  }
})
