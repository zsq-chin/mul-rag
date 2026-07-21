<template>
  <a-dropdown :trigger="['click']">
    <button type="button" class="model-select" aria-label="切换模型">
      <Bot :size="15" />
      <span class="model-text">{{ currentLabel }}</span>
      <ChevronDown :size="14" />
    </button>
    <template #overlay>
      <a-menu class="model-menu">
        <a-menu-item key="system-default" @click="selectBuiltin(null, null)">
          系统默认模型
        </a-menu-item>

        <a-menu-item-group
          v-for="provider in modelKeys"
          :key="provider"
          :title="modelNames[provider]?.name"
        >
          <a-menu-item
            v-for="model in modelNames[provider]?.models"
            :key="`${provider}-${model}`"
            @click="selectBuiltin(provider, model)"
          >
            {{ model }}
          </a-menu-item>
        </a-menu-item-group>

        <template v-if="allowPersonal">
          <a-menu-divider />
          <a-menu-item-group title="我的模型">
            <a-menu-item v-if="userModelsStore.loading" key="personal-loading" disabled>
              加载中...
            </a-menu-item>
            <a-menu-item v-else-if="userModelsStore.models.length === 0" key="personal-empty" disabled>
              暂无个人模型
            </a-menu-item>
            <a-menu-item
              v-for="model in userModelsStore.models"
              :key="`personal-${model.id}`"
              @click="selectPersonal(model)"
            >
              <div class="personal-model-row">
                <span class="personal-model-name">{{ model.display_name }}</span>
                <span class="personal-model-actions">
                  <a-tooltip title="编辑模型">
                    <button type="button" aria-label="编辑模型" @click.stop="editModel(model)">
                      <Pencil :size="14" />
                    </button>
                  </a-tooltip>
                  <a-tooltip title="删除模型">
                    <button type="button" aria-label="删除模型" @click.stop="confirmRemove(model)">
                      <Trash2 :size="14" />
                    </button>
                  </a-tooltip>
                </span>
              </div>
            </a-menu-item>
          </a-menu-item-group>
          <a-menu-divider />
          <a-menu-item key="add-personal" @click="addModel">
            <Plus :size="14" class="menu-icon" />
            添加个人模型
          </a-menu-item>
        </template>
      </a-menu>
    </template>
  </a-dropdown>

  <UserModelEditor
    v-if="allowPersonal"
    v-model:open="editorOpen"
    :model="editingModel"
    @saved="handleSaved"
  />
</template>

<script setup>
import { computed, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { Bot, ChevronDown, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import { useConfigStore } from '@/stores/config'
import { useUserModelsStore } from '@/stores/userModels'
import UserModelEditor from '@/components/UserModelEditor.vue'

const props = defineProps({
  model_name: { type: String, default: '' },
  model_provider: { type: String, default: '' },
  selected: { type: Object, default: null },
  allowPersonal: { type: Boolean, default: false },
})
const emit = defineEmits(['select-model'])
const configStore = useConfigStore()
const userModelsStore = useUserModelsStore()
const editorOpen = ref(false)
const editingModel = ref(null)

const modelNames = computed(() => configStore.config?.model_names || {})
const modelStatus = computed(() => configStore.config?.model_provider_status || {})
const modelKeys = computed(() => Object.keys(modelStatus.value).filter(key => modelStatus.value[key]))
const currentLabel = computed(() => {
  if (props.selected?.kind === 'user') {
    return userModelsStore.models.find(model => model.id === props.selected.userModelId)?.display_name
      || props.selected.name
      || '个人模型'
  }
  return props.selected?.name || props.model_name || '系统默认模型'
})

const selectBuiltin = (provider, name) => {
  userModelsStore.selectedId = null
  emit('select-model', { kind: 'builtin', provider, name })
}

const selectPersonal = model => {
  emit('select-model', {
    kind: 'user',
    userModelId: model.id,
    name: model.display_name,
  })
}

const addModel = () => {
  editingModel.value = null
  editorOpen.value = true
}

const editModel = model => {
  editingModel.value = model
  editorOpen.value = true
}

const confirmRemove = model => {
  Modal.confirm({
    title: `删除“${model.display_name}”？`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const wasSelected = props.selected?.kind === 'user' && props.selected.userModelId === model.id
        await userModelsStore.remove(model.id)
        if (wasSelected) emit('select-model', { kind: 'builtin', provider: null, name: null })
        message.success('模型已删除')
      } catch (error) {
        message.error(error.message || '删除模型失败')
      }
    },
  })
}

const handleSaved = saved => {
  if (props.selected?.kind === 'user' && props.selected.userModelId === saved.id) {
    emit('select-model', {
      kind: 'user',
      userModelId: saved.id,
      name: saved.display_name,
    })
  }
}
</script>

<style lang="less" scoped>
.model-select {
  display: inline-flex;
  min-width: 0;
  max-width: 220px;
  height: 32px;
  align-items: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid var(--gray-300);
  border-radius: 6px;
  background: var(--gray-0, white);
  color: var(--gray-800);
  font: inherit;
  cursor: pointer;

  .model-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.personal-model-row {
  display: flex;
  min-width: 240px;
  align-items: center;
  gap: 10px;
}

.personal-model-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.personal-model-actions {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 2px;

  button {
    display: inline-flex;
    width: 26px;
    height: 26px;
    align-items: center;
    justify-content: center;
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--gray-600);
    cursor: pointer;
  }
}

.menu-icon {
  margin-right: 6px;
  vertical-align: -2px;
}
</style>

<style lang="less">
.model-menu {
  max-height: 360px;
  overflow-y: auto;
}
</style>
