<template>
  <a-modal
    :open="open"
    :title="model ? '编辑个人模型' : '添加个人模型'"
    :confirm-loading="saving"
    :mask-closable="false"
    @ok="save"
    @cancel="close"
    @after-close="resetForm"
  >
    <a-form layout="vertical" autocomplete="off">
      <a-form-item label="显示名称" required>
        <a-input v-model:value="form.display_name" maxlength="100" placeholder="例如：生产问答模型" />
      </a-form-item>
      <a-form-item label="接口类型" required>
        <a-select v-model:value="form.provider">
          <a-select-option value="openai-compatible">OpenAI 兼容接口</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="模型名称" required>
        <a-input v-model:value="form.model_name" maxlength="200" placeholder="例如：qwen-plus" />
      </a-form-item>
      <a-form-item label="API 地址" required>
        <a-input v-model:value="form.api_base" maxlength="500" placeholder="https://example.com/v1" />
      </a-form-item>
      <a-form-item label="API Key" :required="!model">
        <a-input-password
          v-model:value="form.api_key"
          autocomplete="new-password"
          :placeholder="model ? '留空则保留原密钥' : '输入 API Key'"
        />
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="modal-footer">
        <a-button :loading="validating" :disabled="!canValidate" @click="validateConnection">
          <template #icon><ApiOutlined /></template>
          验证连接
        </a-button>
        <span class="footer-fill" />
        <a-button @click="close">取消</a-button>
        <a-button type="primary" :loading="saving" @click="save">保存</a-button>
      </div>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ApiOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { useUserModelsStore } from '@/stores/userModels'

const props = defineProps({
  open: { type: Boolean, default: false },
  model: { type: Object, default: null },
})
const emit = defineEmits(['update:open', 'saved'])
const userModelsStore = useUserModelsStore()
const saving = ref(false)
const validating = ref(false)
const form = reactive({
  display_name: '',
  provider: 'openai-compatible',
  model_name: '',
  api_base: '',
  api_key: '',
})

const canValidate = computed(() => Boolean(form.api_base.trim() && form.api_key.trim()))

watch(() => props.open, open => {
  if (!open) return
  form.display_name = props.model?.display_name || ''
  form.provider = props.model?.provider || 'openai-compatible'
  form.model_name = props.model?.model_name || ''
  form.api_base = props.model?.api_base || ''
  form.api_key = ''
})

const buildPayload = () => {
  const payload = {
    display_name: form.display_name.trim(),
    provider: form.provider,
    model_name: form.model_name.trim(),
    api_base: form.api_base.trim(),
  }
  if (form.api_key) payload.api_key = form.api_key
  return payload
}

const validateRequired = () => {
  if (!form.display_name.trim() || !form.model_name.trim() || !form.api_base.trim()) {
    message.warning('请填写完整的模型信息')
    return false
  }
  if (!props.model && !form.api_key.trim()) {
    message.warning('请输入 API Key')
    return false
  }
  return true
}

const validateConnection = async () => {
  if (!canValidate.value) return
  validating.value = true
  try {
    await userModelsStore.validate({ api_base: form.api_base.trim(), api_key: form.api_key })
    message.success('连接验证成功')
  } catch (error) {
    message.error(error.message || '连接验证失败')
  } finally {
    validating.value = false
  }
}

const save = async () => {
  if (!validateRequired()) return
  saving.value = true
  try {
    const payload = buildPayload()
    const saved = props.model
      ? await userModelsStore.update(props.model.id, payload)
      : await userModelsStore.create(payload)
    emit('saved', saved)
    emit('update:open', false)
    message.success('模型已保存')
  } catch (error) {
    message.error(error.message || '保存模型失败')
  } finally {
    saving.value = false
  }
}

const close = () => emit('update:open', false)

const resetForm = () => {
  form.display_name = ''
  form.provider = 'openai-compatible'
  form.model_name = ''
  form.api_base = ''
  form.api_key = ''
  saving.value = false
  validating.value = false
}
</script>

<style lang="less" scoped>
.modal-footer {
  display: flex;
  align-items: center;
  gap: 8px;
}

.footer-fill {
  flex: 1;
}
</style>
