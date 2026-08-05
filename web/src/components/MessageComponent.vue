<template>
  <div class="message-box" :class="[message.role, customClasses]">
    <!-- 用户消息 -->
    <p v-if="message.role === 'user' || message.role === 'sent'" class="message-text">{{ message.content }}</p>

    <!-- 助手消息 -->
    <div v-else-if="message.role === 'assistant' || message.role === 'received'" class="assistant-message">
      <!-- 推理过程 (ChatComponent特有) -->
      <p v-if="debugMode">
        {{ message.status }}
      </p>
      <div v-if="message.reasoning_content" class="reasoning-box">
        <a-collapse v-model:activeKey="reasoningActiveKey" :bordered="true">
          <template #expandIcon="{ isActive }">
            <caret-right-outlined :rotate="isActive ? 90 : 0" />
          </template>
          <a-collapse-panel key="show" :header="message.status=='reasoning' ? '正在思考...' : '推理过程'" class="reasoning-header">
            <p class="reasoning-content">{{ message.reasoning_content.trim() }}</p>
          </a-collapse-panel>
        </a-collapse>
      </div>

      <!-- 加载中状态 -->
      <div v-if="isEmptyAndLoading" class="loading-dots">
        <div></div>
        <div></div>
        <div></div>
      </div>

      <!-- 检索中状态 (ChatComponent特有) -->
      <div v-else-if="message.status === 'searching' && isProcessing" class="searching-msg">
        <i>{{ message.message || '正在检索……' }}</i>
      </div>

      <!-- 生成中状态 (ChatComponent特有) -->
      <div v-else-if="message.status === 'generating' && isProcessing" class="searching-msg">
        <i>正在生成……</i>
      </div>

      <div v-else-if="message.status === 'error'" class="err-msg" @click="$emit('retry')">
        请求错误，请重试。{{ message.message }}
      </div>

      <!-- 消息内容 -->
      <!-- <div v-else-if="message.content" v-html="renderMarkdown(message)" class="message-md"></div> -->
      <MdPreview v-else-if="message.content" ref="editorRef"
        :editorId="editorId"
        previewTheme="github"
        :theme="themeStore.mode"
        :showCodeRowNumber="false"
        :modelValue="message.content"
        :key="message.id"
        class="message-md"/>

      <div v-else-if="message.reasoning_content"  class="empty-block"></div>

      <!-- 工具调用 (AgentView特有) -->
      <slot v-else-if="message.toolCalls && Object.keys(message.toolCalls).length > 0" name="tool-calls"></slot>

      <div v-else-if="!isProcessing" class="err-msg" @click="$emit('retry')">
        请求错误，请重试。{{ message.message }}
      </div>

      <div v-if="message.isStoppedByUser" class="retry-hint">
        你停止生成了本次回答
        <span class="retry-link" @click="emit('retryStoppedMessage', message.id)">重新编辑问题</span>
      </div>


      <div v-if="(message.role=='received' || message.role=='assistant') && message.status=='finished' && showRefs">
        <RefsComponent :message="message" :show-refs="showRefs" :is-latest-message="isLatestMessage" @retry="emit('retry')" @openRefs="emit('openRefs', $event)" />
      </div>
      <div v-if="(message.role === 'received' || message.role === 'assistant') && message.related_questions && message.related_questions.length > 0" class="related-questions">
        <div class="rec-title">
          <span class="icon">💡</span> 猜你想问：
        </div>
        <div class="rec-list">
          <div 
            v-for="(q, idx) in message.related_questions" 
            :key="idx" 
            class="rec-item"
            @click="$emit('select-question', q)"
          >
            {{ q }}
          </div>
        </div>
      </div>
      </div>
    <slot></slot>
  </div>
</template>

<script setup>
import { computed, ref, useId } from 'vue';
import { CaretRightOutlined } from '@ant-design/icons-vue';
import RefsComponent from '@/components/RefsComponent.vue'
import { useThemeStore } from '@/stores/theme'


import { MdPreview } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css';

const themeStore = useThemeStore()

const props = defineProps({
  // 消息角色：'user'|'assistant'|'sent'|'received'
  message: {
    type: Object,
    required: true
  },
  // 是否正在处理中
  isProcessing: {
    type: Boolean,
    default: false
  },
  // 自定义类
  customClasses: {
    type: Object,
    default: () => ({})
  },
  // 是否显示推理过程
  showRefs: {
    type: [Array, Boolean],
    default: () => false
  },
  debugMode: {
    type: Boolean,
    default: false
  },
  // 是否为最新消息
  isLatestMessage: {
    type: Boolean,
    default: false
  }
});

const editorRef = ref()
const fallbackEditorId = useId().replace(/[^a-zA-Z0-9_-]/g, '-')
const editorId = computed(() => {
  const messageId = String(props.message.id ?? fallbackEditorId).replace(/[^a-zA-Z0-9_-]/g, '-')
  return `preview-${messageId}`
})
const statusDefination = {
  init: '初始化',
  loading: '加载中',
  reasoning: '推理中',
  generating: '生成中',
  error: '错误'
}

const emit = defineEmits(['retry', 'retryStoppedMessage', 'openRefs']);

// 推理面板展开状态
const reasoningActiveKey = ref(['show']);


// 计算属性：内容为空且正在加载
const isEmptyAndLoading = computed(() => {
  const isEmpty = !props.message.content || props.message.content.length === 0;
  const isLoading = props.message.status === 'init' && props.isProcessing
  return isEmpty && isLoading;
});
</script>

<style lang="less" scoped>
.message-box {
  display: inline-block;
  border-radius: 1.5rem;
  margin: 0.8rem 0;
  padding: 0.625rem 1.25rem;
  user-select: text;
  word-break: break-word;
  word-wrap: break-word;
  font-size: 15px;
  line-height: 24px;
  box-sizing: border-box;
  color: var(--text-primary);
  max-width: 100%;
  position: relative;
  letter-spacing: .25px;

  &.user, &.sent {
    max-width: 95%;
    color: var(--text-primary);
    background-color: var(--main-10);
    align-self: flex-end;
    border-radius: .5rem;
    padding: 0.5rem 1rem;
  }

  &.assistant, &.received {
    color: var(--text-primary);
    width: 100%;
    text-align: left;
    margin: 0 0 16px 0;
    padding: 0px;
    background-color: transparent;
    border-radius: 0;
  }

  .message-text {
    max-width: 100%;
    margin-bottom: 0;
    white-space: pre-line;
  }

  .err-msg {
    color: #d15252;
    border: 1px solid #f19999;
    padding: 0.5rem 1rem;
    border-radius: 8px;
    text-align: left;
    background: color-mix(in srgb, var(--danger) 8%, var(--surface));
    margin-bottom: 10px;
    cursor: pointer;
  }

  .searching-msg {
    color: var(--gray-700);
    animation: colorPulse 1s infinite ease-in-out;
  }

  .reasoning-box {
    margin-top: 10px;
    margin-bottom: 15px;
    border-radius: 8px;
    border: 1px solid var(--main-light-3);

    .reasoning-content {
      font-size: 13px;
      color: var(--gray-800);
      white-space: pre-wrap;
      margin: 0;
    }
  }

  .assistant-message {
    width: 100%;
  }

  .status-info {
    display: block;
    background-color: var(--gray-50);
    color: var(--gray-700);
    padding: 10px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 12px;
    font-family: monospace;
    max-height: 200px;
    overflow-y: auto;
  }

  :deep(.tool-calls-container) {
    width: 100%;
    margin-top: 10px;

    .tool-call-container {
      margin-bottom: 10px;

      &:last-child {
        margin-bottom: 0;
      }
    }
  }

  :deep(.tool-call-display) {
    background-color: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    overflow: hidden;

    .tool-header {
      padding: 10px 12px;
      background-color: var(--gray-100);
      font-size: 14px;
      font-weight: 500;
      color: var(--gray-800);
      border-bottom: 1px solid var(--gray-200);
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      user-select: none;
      position: relative;

      .anticon {
        color: #FF0000; /* 修改为红色箭头 */
      }

      .step-badge {
        margin-left: auto;
        background-color: var(--gray-200);
        color: var(--gray-700);
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 500;
      }
    }

    .tool-content {
      transition: all 0.3s ease;
      .tool-params {
        padding: 10px 12px;
        background-color: var(--gray-50);

        .tool-params-header {
          background-color: var(--gray-100);
          font-size: 13px;
          color: var(--gray-800);
        }

        .tool-params-content {
          margin: 0;
          font-size: 13px;
          background-color: var(--gray-100);
          border-radius: 4px;
          padding: 8px;
          overflow-x: auto;
        }
      }
    }

    &.is-collapsed {
      .tool-header {
        border-bottom: none;
      }
    }
  }
}

.retry-hint {
  margin-top: 8px;
  padding: 8px 16px;
  color: var(--text-secondary);
  font-size: 14px;
  text-align: left;
}

.retry-link {
  color: #1890ff;
  cursor: pointer;
  margin-left: 4px;

  &:hover {
    text-decoration: underline;
  }
}

.ant-btn-icon-only {
  &:has(.anticon-stop) {
    background-color: #ff4d4f !important;

    &:hover {
      background-color: #ff7875 !important;
    }
  }
}


.loading-dots {
  display: inline-flex;
  align-items: center;
  justify-content: center;

  div {
    width: 8px;
    height: 8px;
    margin: 0 4px;
    background-color: var(--gray-700);
    border-radius: 50%;
    opacity: 0.3;
    animation: pulse 0.5s infinite ease-in-out both;

    &:nth-child(1) {
      animation-delay: -0.32s;
    }

    &:nth-child(2) {
      animation-delay: -0.16s;
    }
  }
}

/* ==================== 新增样式：猜你想问 ==================== */
.related-questions {
  margin-top: 10px;
  padding: 10px 12px;
  background-color: var(--gray-50); 
  border-radius: 8px;
  border: 1px dashed var(--gray-300);
  text-align: left;

  .rec-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--gray-700);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .rec-list {
    display: flex;
    flex-direction: column;
    gap: 6px;

    .rec-item {
      background-color: var(--surface-raised);
      border: 1px solid var(--gray-200);
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 13px;
      color: var(--main-500); 
      cursor: pointer;
      transition: all 0.2s ease;
      width: fit-content;
      max-width: 100%;

      &:hover {
        background-color: var(--main-10);
        border-color: var(--main-300);
      }
    }
  }
}

@keyframes colorPulse {
  0% { color: var(--gray-700); }
  50% { color: var(--gray-300); }
  100% { color: var(--gray-700); }
}

@keyframes pulse {
  0%, 80%, 100% {
    transform: scale(0.8);
    opacity: 0.3;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>

<style lang="less">
.message-md.md-editor {
  --md-bk-color: transparent;
  --md-color: var(--text-primary);
  background-color: transparent;
  color: var(--text-primary);
}

.message-md .md-editor-preview {
  --md-theme-color: var(--text-primary);
  color: var(--text-primary);
}

.message-md .md-editor-preview-wrapper {
  --md-bk-color: transparent;
  color: var(--text-primary);
  max-width: 100%;
  padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', 'Hiragino Sans GB', 'Source Han Sans CN', 'Courier New', monospace;

  [id$='-preview'] {
    font-size: 15px;
  }

  table {
    display: block;
    max-width: 100%;
    overflow-x: auto;
  }

  img {
    max-width: 100%;
    height: auto;
  }

  h1, h2 {
    font-size: 1.2rem;
  }

  h3, h4 {
    font-size: 1.1rem;
  }

  h5, h6 {
    font-size: 1rem;
  }

  // li > p, ol > p, ul > p {
  //   margin: 0.25rem 0;
  // }

  // ol, ul {
  //   padding-left: 1rem;
  // }

  a {
    color: var(--main-700);
  }

  code {
    font-size: 13px;
    font-family: 'Menlo', 'Monaco', 'Consolas', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', 'Hiragino Sans GB', 'Source Han Sans CN', 'Courier New', monospace;
    line-height: 1.5;
    letter-spacing: 0.025em;
    tab-size: 4;
    -moz-tab-size: 4;
    background-color: var(--gray-100);
  }
}

.chat-box.font-smaller .message-md [id$='-preview'] {
  font-size: 14px;

  h1, h2 {
    font-size: 1.1rem;
  }

  h3, h4 {
    font-size: 1rem;
  }
}

.chat-box.font-larger .message-md [id$='-preview'] {
  font-size: 16px;

  h1, h2 {
    font-size: 1.3rem;
  }

  h3, h4 {
    font-size: 1.2rem;
  }

  h5, h6 {
    font-size: 1.1rem;
  }

  code {
    font-size: 14px;
  }
}
</style>
