import { apiGet } from './base'
import { useUserStore } from '@/stores/user'

const checkAdminPermission = () => {
  const userStore = useUserStore()
  if (!userStore.isAdmin) {
    throw new Error('需要管理员权限')
  }
  return true
}
/**
 * K80数据库查询API模块
 * 提供对K80数据库的只读查询接口
 */

export const k80Api = {
  /**
   * 查询用户列表
   * @param {Object} params - 查询参数
   * @param {string} [params.username] - 用户名筛选
   * @param {string} [params.role] - 角色筛选
   * @returns {Promise} - 用户列表
   */
  getUsers: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.username) queryParams.append('username', params.username)
    if (params.role) queryParams.append('role', params.role)
    
    return apiGet(`/api/k80/users?${queryParams.toString()}`, {}, true)
  },

  /**
   * 根据ID查询用户详情
   * @param {number} userId - 用户ID
   * @returns {Promise} - 用户详情
   */
  getUserById: (userId) => apiGet(`/api/k80/users/${userId}`, {}, true),

  /**
   * 查询指南记录列表
   * @param {Object} params - 查询参数
   * @param {string} [params.guide_id] - 指南ID筛选
   * @param {number} [params.user_id] - 用户ID筛选
   * @returns {Promise} - 指南记录列表
   */
  getGuideRecords: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.guide_id) queryParams.append('guide_id', params.guide_id)
    if (params.user_id) queryParams.append('user_id', params.user_id)
    
    return apiGet(`/api/k80/guide_records?${queryParams.toString()}`, {}, true)
  },

  /**
   * 根据ID查询指南记录详情
   * @param {number} recordId - 记录ID
   * @returns {Promise} - 指南记录详情
   */
  getGuideRecordById: (recordId) => apiGet(`/api/k80/guide_records/${recordId}`, {}, true),

  /**
   * 查询用户的所有指南记录
   * @param {number} userId - 用户ID
   * @returns {Promise} - 指南记录列表
   */
  getUserGuideRecords: (userId) => apiGet(`/api/k80/user_guide_records?user_id=${userId}`, {}, true)
}
