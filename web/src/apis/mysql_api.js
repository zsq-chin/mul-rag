import { apiGet, apiPost, apiPut, apiDelete } from './base'
import { useUserStore } from '@/stores/user'

const checkAdminPermission = () => {
  const userStore = useUserStore()
  if (!userStore.isAdmin) {
    throw new Error('需要管理员权限')
  }
  return true
}

export const chatbotApi = {
  /**
   * 添加用户
   * @param {{username: string, student_id: string}} userData
   */
  addUser: async (userData) => {
    checkAdminPermission()
    return apiPost('/api/chatbot/user/add', userData, {}, true)
  },

  /**
   * 添加会话
   * @param {{user_id: number, session_name: string}} sessionData
   */
  addSession: async (sessionData) => {
    checkAdminPermission()
    return apiPost('/api/chatbot/session/add', sessionData, {}, true)
  },

  /**
   * 添加消息
   * @param {{session_id: number, sender_type: string, content: string, parent_id?: number}} messageData
   */
  addMessage: async (messageData) => {
    checkAdminPermission()
    return apiPost('/api/chatbot/message/add', messageData, {}, true)
  },

  /**
   * 添加评价
   * @param {{message_id: number, user_id: number, evaluation: number}} evaluationData
   */
  addEvaluation: async (evaluationData) => {
    checkAdminPermission()
    return apiPost('/api/chatbot/evaluation/add', evaluationData, {}, true)
  },

  /**
   * 获取用户及关联数据
   * @param {number} userId
   */
  getUserWithData: async (userId) => {
    checkAdminPermission()
    return apiGet(`/api/chatbot/user/${userId}`, {}, true)
  },

  /**
   * 修改消息内容
   * @param {number} messageId
   * @param {{content: string}} updateData
   */
  updateMessageContent: async (messageId, updateData) => {
    checkAdminPermission()
    return apiPut(`/api/chatbot/message/${messageId}`, updateData, {}, true)
  },

  /**
   * 删除评价
   * @param {number} evaluationId
   */
  deleteEvaluation: async (evaluationId) => {
    checkAdminPermission()
    return apiDelete(`/api/chatbot/evaluation/${evaluationId}`, {}, true)
  },

  /**
   * 级联删除用户及相关数据
   * @param {number} userId
   */
  deleteUserCascade: async (userId) => {
    checkAdminPermission()
    return apiDelete(`/api/chatbot/user/${userId}/cascade`, {}, true)
  },
}
