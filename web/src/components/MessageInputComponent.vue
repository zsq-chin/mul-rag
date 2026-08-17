<template>
  <div class="input-box" :class="customClasses" @click="focusInput">
    <div class="input-area">
      <a-textarea
        ref="inputRef"
        class="user-input"
        v-model:value="inputValue"
        @keydown="handleKeyPress"
        :placeholder="placeholder"
        :disabled="disabled"
        :auto-size="autoSize"
      />
    </div>
    <div class="input-options">
      <div class="options__left">
        <slot name="options-left"></slot>
      </div>
      <div class="options__right">
        <a-tooltip :title="isLoading ? '停止回答' : ''">
          <a-button
            @click="handleSendOrStop"
            :disabled="sendButtonDisabled"
            type="link"
          >
          <template #icon>
            <component :is="getIcon" class="send-btn"/>
          </template>
          </a-button>
        </a-tooltip>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, toRefs, onMounted } from 'vue';
import {
  SendOutlined,
  ArrowUpOutlined,
  LoadingOutlined,
  PauseOutlined
} from '@ant-design/icons-vue';


const inputRef = ref(null);
const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '输入问题...'
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  },
  sendButtonDisabled: {
    type: Boolean,
    default: false
  },
  autoSize: {
    type: Object,
    default: () => ({ minRows: 2, maxRows: 6 })
  },
  sendIcon: {
    type: String,
    default: 'ArrowUpOutlined'
  },
  customClasses: {
    type: Object,
    default: () => ({})
  }
});

const emit = defineEmits(['update:modelValue', 'send', 'keydown']);

// 图标映射
const iconComponents = {
  'SendOutlined': SendOutlined,
  'ArrowUpOutlined': ArrowUpOutlined,
  'PauseOutlined': PauseOutlined
};

// 根据传入的图标名动态获取组件
const getIcon = computed(() => {
  if (props.isLoading) {
    return PauseOutlined;
  }
  return iconComponents[props.sendIcon] || ArrowUpOutlined;
});

// 创建本地引用以进行双向绑定
const inputValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
});

// 处理键盘事件
const handleKeyPress = (e) => {
  emit('keydown', e);
};

// 处理发送按钮点击
const handleSendOrStop = () => {
  emit('send');
};

// 聚焦输入框
const focusInput = () => {
  if (inputRef.value && !props.disabled) {
    inputRef.value.focus();
  }
};

// Wait for component to mount before setting up onStartTyping
onMounted(() => {
  // Use the template ref element for onStartTyping
  setTimeout(() => {
    if (inputRef.value) {
      inputRef.value.focus();
    }
  }, 100);
});

</script>

<style lang="less" scoped>
.input-box {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: auto;
  margin: 0 auto;
  padding: 0.4rem 0.75rem;
  border: 1px solid var(--gray-500);
  border-radius: 0.8rem;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  transition: all 0.3s ease;

  &:focus-within {
    border-color: var(--main-500);
    background: var(--surface);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }

  .input-area {
    display: flex;
    align-items: flex-end;
    gap: 8px;
  }

  .user-input {
    flex: 1;
    min-height: 44px;
    padding: 0;
    background-color: transparent;
    border: none;
    margin: 0;
    color: var(--text-primary);
    font-size: 14px;
    outline: none;
    resize: none;
    line-height: 1.6;

    &:focus {
      outline: none;
      box-shadow: none;
    }

    &:active {
      outline: none;
    }

    &::placeholder {
      color: #888888;
    }
  }

  .input-options {
    display: flex;
    padding: 8px 0 0;
    border-top: 1px solid var(--gray-100);

    .options__left,
    .options__right {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .options__right {
      width: fit-content;
      flex-shrink: 0;
    }

    .options__left {
      flex: 1;
      min-width: 0;
      flex-wrap: nowrap;
      overflow-x: auto;
      overflow-y: hidden;
      padding-bottom: 4px;
      // 溢出时显示浅白色发光细滚动条，可直接拖动（Chrome/Edge）
      &::-webkit-scrollbar {
        height: 6px;
      }
      &::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.7);
        border-radius: 3px;
        box-shadow: 0 0 8px rgba(255, 255, 255, 0.6); // 发光效果
      }
      &::-webkit-scrollbar-track {
        background: transparent;
      }
      // Firefox 浅白色细滚动条
      scrollbar-width: thin;
      scrollbar-color: rgba(255, 255, 255, 0.7) transparent;

      :deep(.opt-item) {
        flex-shrink: 0; // 选项不被压缩变形
        white-space: nowrap;
        border-radius: 12px;
        border: 1px solid var(--gray-300);
        padding: 5px 10px;
        cursor: pointer;
        font-size: 14px;
        // color: var(--gray-700);
        transition: all 0.2s ease;

        &:hover {
          background-color: var(--main-10);
          color: var(--main-600);
        }

        &.active {
          color: var(--main-600);
          border: 1px solid var(--main-500);
          background-color: var(--main-10);
        }
      }
    }
  }
}

button.ant-btn-icon-only {
  height: 32px;
  width: 32px;
  cursor: pointer;
  background-color: var(--main-500);
  border-radius: 50%;
  border: none;
  transition: all 0.2s ease;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
  color: white;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;

  &:hover {
    background-color: var(--main-600);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.15);
    color: white;
  }

  &:active {
    transform: translateY(0);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  }

  &:disabled {
    background-color: var(--gray-400);
    cursor: not-allowed;
    transform: none;
    box-shadow: none;
  }
}

@media (max-width: 520px) {
  .input-box {
    border-radius: 15px;
    padding: 0.625rem 0.875rem;
  }

  .input-options {
    flex-wrap: wrap;
    overflow-x: hidden;

    .options__left {
      flex-wrap: wrap;
      min-width: 0;
      overflow: hidden;
    }
  }
}
</style>