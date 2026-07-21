import { apiGet, apiPost, apiDelete, apiPatch, apiPut } from './base'
import { useUserStore } from '@/stores/user'

/**
 * 需要用户认证的API模块
 * 用户必须登录才能访问的API
 * 权限要求: 任何已登录用户（普通用户、管理员、超级管理员）
 */

// 聊天相关API
export const chatApi = {
  /**
   * 发送聊天消息
   * @param {Object} params - 聊天参数
   * @returns {Promise} - 聊天响应流
   */
  sendMessage: (params) => {
    return fetch('/api/chat/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...useUserStore().getAuthHeaders()
      },
      body: JSON.stringify(params),
    })
  },

  /**
   * 发送可中断的聊天消息
   * @param {Object} params - 聊天参数
   * @param {AbortSignal} signal - 用于中断请求的信号控制器
   * @returns {Promise} - 聊天响应流
   */
  sendMessageWithAbort: (params, signal) => {
    return fetch('/api/chat/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...useUserStore().getAuthHeaders()
      },
      body: JSON.stringify(params),
      signal // 添加 signal 用于中断请求
    })
  },

  /**
   * 发送聊天消息到指定智能体（流式响应）
   * @param {string} agentId - 智能体ID
   * @param {Object} data - 聊天数据
   * @returns {Promise} - 聊天响应流
   */
  sendAgentMessage: (agentId, data) => {
    return fetch(`/api/chat/agent/${agentId}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...useUserStore().getAuthHeaders()
      },
      body: JSON.stringify(data)
    })
  },

  /**
   * 简单聊天调用（非流式）
   * @param {string} query - 查询内容
   * @returns {Promise} - 聊天响应
   */
  simpleCall: (query) => apiPost('/api/chat/call', { query }, {}, true),

  getMultimodalKbs: () => apiGet('/api/chat/multimodal/kbs', {}, true),

  getKnowledgeBases: () => apiGet('/api/data/', {}, true),

  getUserModels: () => apiGet('/api/chat/user-models', {}, true),
  createUserModel: (payload) => apiPost('/api/chat/user-models', payload, {}, true),
  updateUserModel: (modelId, payload) => apiPatch(`/api/chat/user-models/${modelId}`, payload, {}, true),
  deleteUserModel: (modelId) => apiDelete(`/api/chat/user-models/${modelId}`, {}, true),
  selectUserModel: (modelId) => apiPost(`/api/chat/user-models/${modelId}/select`, {}, {}, true),
  validateUserModel: (payload) => apiPost('/api/chat/user-models/validate', payload, {}, true),

  /**
   * 获取默认智能体
   * @returns {Promise} - 默认智能体信息
   */
  getDefaultAgent: () => apiGet('/api/chat/default_agent', {}, true),

  /**
   * 获取智能体列表
   * @returns {Promise} - 智能体列表
   */
  getAgents: () => apiGet('/api/chat/agent', {}, true),

  /**
   * 获取单个智能体详情
   * @param {string} agentId - 智能体ID
   * @returns {Promise} - 智能体详情
   */
  getAgentDetail: (agentId) => apiGet(`/api/chat/agent/${agentId}`, {}, true),

  /**
   * 获取智能体历史消息
   * @param {string} agentName - 智能体名称
   * @param {string} threadId - 会话ID
   * @returns {Promise} - 历史消息
   */
  getAgentHistory: (agentName, threadId) => apiGet(`/api/chat/agent/${agentName}/history?thread_id=${threadId}`, {}, true),

  /**
   * 获取可用工具列表
   * @returns {Promise} - 工具列表
   */
  getTools: () => apiGet('/api/chat/tools', {}, true),

  /**
   * 获取模型提供商的模型列表
   * @param {string} provider - 模型提供商
   * @returns {Promise} - 模型列表
   */
  getProviderModels: (provider) => {
    return fetch(`/api/chat/models?model_provider=${provider}`, {
      headers: {
        ...useUserStore().getAuthHeaders()
      }
    }).then(response => response.json())
  },

  /**
   * 更新模型提供商的模型列表
   * @param {string} provider - 模型提供商
   * @param {Array} models - 选中的模型列表
   * @returns {Promise} - 更新结果
   */
  updateProviderModels: (provider, models) => {
    return fetch(`/api/chat/models/update?model_provider=${provider}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...useUserStore().getAuthHeaders()
      },
      body: JSON.stringify(models)
    }).then(response => response.json())
  }
}

// 用户设置API
export const userSettingsApi = {
  /**
   * 获取用户设置
   * @returns {Promise} - 用户设置
   */
  getSettings: () => apiGet('/api/user/settings', {}, true),

  /**
   * 更新用户设置
   * @param {Object} settings - 新设置
   * @returns {Promise} - 更新结果
   */
  updateSettings: (settings) => apiPost('/api/user/settings', settings, {}, true),
}

// 对话线程相关API
export const threadApi = {
  /**
   * 获取对话线程列表
   * @param {string} agentId - 智能体ID
   * @returns {Promise} - 对话线程列表
   */
  getThreads: (agentId) => {
    const url = agentId ? `/api/chat/threads?agent_id=${agentId}` : '/api/chat/threads';
    return apiGet(url, {}, true);
  },

  /**
   * 创建新对话线程
   * @param {string} agentId - 智能体ID
   * @param {string} title - 对话标题
   * @param {Object} metadata - 元数据
   * @returns {Promise} - 创建结果
   */
  createThread: (agentId, title, metadata) => apiPost('/api/chat/thread', {
    agent_id: agentId,
    title: title || '新对话',
    metadata: metadata || {}
  }, {}, true),

  /**
   * 更新对话线程
   * @param {string} threadId - 对话线程ID
   * @param {string} title - 对话标题
   * @param {string} description - 对话描述
   * @returns {Promise} - 更新结果
   */
  updateThread: (threadId, title, description) => apiPut(`/api/chat/thread/${threadId}`, {
    title,
    description
  }, {}, true),

  /**
   * 删除对话线程
   * @param {string} threadId - 对话线程ID
   * @returns {Promise} - 删除结果
   */
  deleteThread: (threadId) => apiDelete(`/api/chat/thread/${threadId}`, {}, true)
};


// 聊天记录相关API
export const chatRecordApi = {
  /**
   * 获取当前用户的所有聊天记录
   * @returns {Promise}
   */
  getChatRecords: () => apiGet('/api/chat/records', {}, true),

  /**
   * 保存聊天记录（覆盖当前用户记录）
   * @param {Array} records
   * @returns {Promise}
   */
  saveChatRecords: (record) => apiPost('/api/chat/records', record, {}, true),

  /**
   * 删除所有聊天记录
   * @returns {Promise}
   */
  deleteChatRecord: (record_id) => apiDelete(`/api/chat/records/${record_id}`, {}, true),
}



// 决策引导记录相关API
export const guideRecordApi = {
  /**
   * 获取当前用户的所有决策引导记录
   * @returns {Promise}
   */
  getGuideRecords: () => apiGet('/api/chat/guide', {}, true),

  /**
   * 保存记录（覆盖当前用户记录）
   * @param {Array} records
   * @returns {Promise}
   */
  saveGuideRecords: (record) => apiPost('/api/chat/guide', record, {}, true),

  /**
   * 删除所有记录
   * @returns {Promise}
   */
  deleteGuideRecord: (guide_id) => apiDelete(`/api/chat/guide/${guide_id}`, {}, true),
}


// 写作达人聊天记录相关API
export const writerRecordApi = {
  /**
   * 获取当前用户的所有聊天记录
   * @returns {Promise}
   */
  getChatRecords: () => apiGet('/api/chat/writer', {}, true),

  /**
   * 保存聊天记录（覆盖当前用户记录）
   * @param {Array} records
   * @returns {Promise}
   */
  saveChatRecords: (record) => apiPost('/api/chat/writer', record, {}, true),

  /**
   * 删除所有聊天记录
   * @returns {Promise}
   */
  deleteChatRecord: (writer_id) => apiDelete(`/api/chat/writer/${writer_id}`, {}, true),
}

// 题目生成记录相关API
export const itemRecordApi = {
  /**
   * 获取当前用户的所有题目生成记录
   * @returns {Promise}
   */
  getItemRecords: () => apiGet('/api/chat/item', {}, true),

  /**
   * 保存记录（覆盖当前用户记录）
   * @param {Array} records
   * @returns {Promise}
   */
  saveItemRecords: (record) => apiPost('/api/chat/item', record, {}, true),

  /**
   * 删除所有记录
   * @returns {Promise}
   */
  deleteItemRecord: (item_id) => apiDelete(`/api/chat/item/${item_id}`, {}, true),
}

// 试卷生成记录相关API
export const examRecordApi = {
  /**
   * 获取当前用户的所有题目生成记录
   * @returns {Promise}
   */
  getExamRecords: () => apiGet('/api/chat/exam', {}, true),

  /**
   * 保存记录（覆盖当前用户记录）
   * @param {Array} records
   * @returns {Promise}
   */
  saveExamRecord: (record) => apiPost('/api/chat/exam', record, {}, true),

    /**
   * 保存记录（覆盖当前用户记录）
   * @param {Array} records
   * @returns {Promise}
   */
  saveExamSubmissionRecord: (record) => apiPost('/api/chat/exam_sub', record, {}, true),

  /**
   * 删除记录
   * @returns {Promise}
   */
  deleteExamRecord: (exam_paper_id) => apiDelete(`/api/chat/exam/${exam_paper_id}`, {}, true),
}
