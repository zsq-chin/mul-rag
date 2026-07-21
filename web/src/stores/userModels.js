import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { chatApi } from '@/apis/auth_api'

const sortModels = models => [...models].sort((left, right) => {
  const leftTime = left.last_used_at ? Date.parse(left.last_used_at) : 0
  const rightTime = right.last_used_at ? Date.parse(right.last_used_at) : 0
  return rightTime - leftTime || left.display_name.localeCompare(right.display_name, 'zh-CN')
})

export const useUserModelsStore = defineStore('userModels', () => {
  const models = ref([])
  const loading = ref(false)
  const selectedId = ref(null)
  const error = ref('')
  const selectedModel = computed(() => models.value.find(model => model.id === selectedId.value) || null)

  const reset = () => {
    models.value = []
    selectedId.value = null
    error.value = ''
    loading.value = false
  }

  const load = async () => {
    loading.value = true
    error.value = ''
    models.value = []
    try {
      const data = await chatApi.getUserModels()
      models.value = sortModels(Array.isArray(data) ? data : [])
      return models.value
    } catch (requestError) {
      error.value = requestError.message || '加载个人模型失败'
      throw requestError
    } finally {
      loading.value = false
    }
  }

  const create = async payload => {
    const model = await chatApi.createUserModel(payload)
    models.value = sortModels([...models.value, model])
    return model
  }

  const update = async (modelId, payload) => {
    const updated = await chatApi.updateUserModel(modelId, payload)
    models.value = sortModels(models.value.map(model => model.id === modelId ? updated : model))
    return updated
  }

  const remove = async modelId => {
    await chatApi.deleteUserModel(modelId)
    models.value = models.value.filter(model => model.id !== modelId)
    if (selectedId.value === modelId) selectedId.value = null
  }

  const validate = payload => chatApi.validateUserModel(payload)

  const select = async modelId => {
    const selected = await chatApi.selectUserModel(modelId)
    selectedId.value = modelId
    models.value = sortModels(models.value.map(model => model.id === modelId ? selected : model))
    return selected
  }

  return {
    models,
    loading,
    selectedId,
    selectedModel,
    error,
    reset,
    load,
    create,
    update,
    remove,
    validate,
    select,
  }
})
