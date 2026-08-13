<template>
  <div class="refs" v-if="showRefs">
    <div class="tags">
      <span
        class="item btn"
        :class="{ active: ratingFor(msg) === 'up', disabled: !canRate(msg) }"
        title="回答有帮助"
        @click="likeThisResponse(msg)"
      ><LikeOutlined /></span>
      <span
        class="item btn"
        :class="{ active: ratingFor(msg) === 'down', disabled: !canRate(msg) }"
        title="回答不理想"
        @click="dislikeThisResponse(msg)"
      ><DislikeOutlined /></span>
      <span v-if="msg.meta?.server_model_name" class="item">
        <!-- <BulbOutlined /> {{ msg.meta.server_model_name }} -->
        <FireTwoTone twoToneColor="red"/> &nbsp;辽河油田图谱大模型
      </span>
      <span
        v-if="showKey('copy')"
        class="item btn" @click="copyText(msg.content)" title="复制"><CopyOutlined /></span>
       <span
        class="item btn" @click="downloadAsWord(msg.content)" title="下载为Word"><DownloadOutlined /></span>
      <span
        v-if="showKey('regenerate')"
        class="item btn" @click="regenerateMessage()" title="重新生成"><ReloadOutlined /></span>
      <!-- 如果只要显示最后一条消息的refs sidebar，可以在v-if中加上isLatestMessage &&  -->
      <span
        v-if="showKey('subGraph') && hasSubGraphData(msg)"
        class="item btn"
        @click="openGlobalRefs('graph')"
      >
        <DeploymentUnitOutlined /> 关系图
      </span>
      <span
        class="item btn"
        v-if="showKey('webSearch') && msg.refs?.web_search?.results?.length > 0"
        @click="openGlobalRefs('webSearch')"
      >
        <GlobalOutlined /> 网页搜索 {{ msg.refs.web_search?.results.length }}
      </span>
      <span
        class="item btn"
        v-if="showKey('knowledgeBase') && hasKnowledgeBaseData(msg)"
        @click="openGlobalRefs('knowledgeBase')"
      >
        <FileTextOutlined /> 知识库
      </span>
      <span
        class="item btn"
        v-if="showKey('multimodalKnowledgeBase') && hasMultimodalKnowledgeBaseData(msg)"
        @click="openGlobalRefs('multimodalKnowledgeBase')"
      >
        <DatabaseOutlined /> 多模态知识库 {{ getMultimodalKbLabel(msg) }} {{ getMultimodalKbBadge(msg) }}
      </span>
    </div>

    <!-- 点踩可选原因：不阻塞评价提交 -->
    <a-modal
      v-model:open="reasonVisible"
      title="反馈原因（可选）"
      :ok-text="reasonSubmitting ? '提交中…' : '提交'"
      :confirm-loading="reasonSubmitting"
      @ok="submitReason"
      @cancel="cancelReason"
      :cancel-text="'跳过'"
    >
      <p class="reason-hint">你的点踩已记录，可以补充原因帮助我们改进。</p>
      <a-textarea
        v-model:value="reasonText"
        :rows="3"
        :maxlength="255"
        show-count
        placeholder="例如：证据不充分 / 回答偏离问题 / 内容过时……"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { message } from 'ant-design-vue'
import { saveAs } from 'file-saver'
import {
  GlobalOutlined,
  FileTextOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  BulbOutlined,
  ReloadOutlined,
  FireTwoTone,
  DownloadOutlined,
  LikeOutlined,
  DislikeOutlined,
} from '@ant-design/icons-vue'
import { feedbackApi } from '@/apis/local_features'

const emit = defineEmits(['retry', 'openRefs']);
const props = defineProps({
  message: Object,
  showRefs: {
    type: [Array, Boolean],
    default: () => false
  },
  isLatestMessage: {
    type: Boolean,
    default: false
  },
  conversationId: {
    type: String,
    default: ''
  }
})

const msg = ref(props.message)

const { copy, isSupported } = useClipboard()

const showKey = (key) => {
  if (props.showRefs === true) {
    return true
  }
  return props.showRefs.includes(key)
}

const copyText = async (text) => {
  if (isSupported) {
    try {
      await copy(text)
      message.success('文本已复制到剪贴板')
    } catch (error) {
      console.error('复制失败:', error)
      message.error('复制失败，请手动复制')
    }
  } else {
    console.warn('浏览器不支持自动复制')
    message.warning('浏览器不支持自动复制，请手动复制')
  }
}

// 下载为Word功能
const downloadAsWord = (content) => {
  try {
    const htmlContent = `
      <html>
        <body>
          <div style="font-size: 14px; line-height: 1.8;">
            ${content.replace(/\n/g, '<br>')}
          </div>
        </body>
      </html>
    `
    const blob = new Blob([htmlContent], { type: 'application/msword;charset=utf-8' })
    saveAs(blob, '回答内容.doc')
    message.success('下载已开始')
  } catch (error) {
    console.error('下载失败:', error)
    message.error('下载失败，请重试')
  }
}

const showRefs = computed(() => (msg.value.role=='received' || msg.value.role=='assistant') && msg.value.status=='finished')

const openGlobalRefs = (type) => {
  emit('openRefs', {
    type,
    refs: msg.value.refs
  })
}

const hasSubGraphData = (msg) => {
  return msg.refs?.graph_base?.results?.nodes?.length > 0;
}

const hasKnowledgeBaseData = (msg) => {
  return msg.refs?.knowledge_base?.results?.length > 0;
}

const hasMultimodalKnowledgeBaseData = (msg) => {
  const refs = msg.refs?.multimodal_knowledge_base;
  return !!(
    msg.refs?.meta?.use_multimodal_kb ||
    refs?.results?.length > 0 ||
    refs?.message ||
    refs?.kb_id
  );
}

const getMultimodalKbLabel = (msg) => {
  const refs = msg.refs?.multimodal_knowledge_base;
  return refs?.kb_name || msg.refs?.meta?.multimodal_kb_name || refs?.kb_id || '';
}

const getMultimodalKbBadge = (msg) => {
  const refs = msg.refs?.multimodal_knowledge_base;
  const count = refs?.results?.length || 0;
  if (count > 0) return count;
  if (refs?.status === 'error' || refs?.message) return '异常';
  return '0';
}

const regenerateMessage = () => {
  emit('retry')
}

// ============ 问答反馈：点赞 / 点踩 ============

// 每条消息的反馈状态：{ [messageId]: { rating, loading, submitting } }
const feedbackState = reactive({})

const canRate = (m) => {
  if (!m || !m.id) return false
  return (m.role === 'received' || m.role === 'assistant') && m.status === 'finished'
}

const ratingFor = (m) => (m && feedbackState[m.id] ? feedbackState[m.id].rating : '')

const stateOf = (m) => {
  if (!m || !m.id) return { rating: '', loading: false, submitting: false }
  if (!feedbackState[m.id]) feedbackState[m.id] = { rating: '', loading: false, submitting: false }
  return feedbackState[m.id]
}

const loadFeedback = async (m) => {
  if (!canRate(m)) return
  const st = stateOf(m)
  if (st.loading || st.rating) return
  st.loading = true
  try {
    const res = await feedbackApi.get(m.id)
    const data = res?.data
    if (data && data.rating) {
      st.rating = data.rating
      // 附带恢复服务端原因，供点踩弹窗使用
      st.reason = data.reason || ''
    }
  } catch (e) {
    // 404 等说明无记录，保持未评价状态；网络错误静默，不打断对话
    console.error('加载反馈状态失败', e)
  } finally {
    st.loading = false
  }
}

// 生成中 / 刚完成时同步状态；刷新后自动恢复选中态
watch(
  () => [msg.value?.id, msg.value?.status],
  ([id, status]) => {
    if (id && status === 'finished') loadFeedback(msg.value)
  },
  { immediate: true }
)

const likeThisResponse = async (m) => {
  if (!canRate(m)) return
  const st = stateOf(m)
  const prev = st.rating
  // 再次点击已选按钮 = 取消
  if (prev === 'up') {
    st.rating = ''
    try {
      await feedbackApi.remove(m.id)
    } catch (e) {
      st.rating = prev // 失败回滚界面状态
      console.error('取消点赞失败', e)
      message.error('操作失败，请稍后重试')
    }
    return
  }
  st.rating = 'up'
  try {
    await feedbackApi.upsert(m.id, {
      conversation_id: props.conversationId || undefined,
      rating: 'up'
    })
  } catch (e) {
    st.rating = prev // 失败回滚界面状态
    console.error('点赞失败', e)
    message.error('操作失败，请稍后重试')
  }
}

const dislikeThisResponse = async (m) => {
  if (!canRate(m)) return
  const st = stateOf(m)
  const prev = st.rating
  // 再次点击已选按钮 = 取消
  if (prev === 'down') {
    st.rating = ''
    st.reason = ''
    try {
      await feedbackApi.remove(m.id)
    } catch (e) {
      st.rating = prev // 失败回滚界面状态
      console.error('取消点踩失败', e)
      message.error('操作失败，请稍后重试')
    }
    return
  }
  st.rating = 'down'
  try {
    await feedbackApi.upsert(m.id, {
      conversation_id: props.conversationId || undefined,
      rating: 'down'
    })
    // 点踩已提交，弹出可选原因（不阻塞）
    reasonText.value = st.reason || ''
    reasonTarget.value = m
    reasonVisible.value = true
  } catch (e) {
    st.rating = prev // 失败回滚界面状态
    console.error('点踩失败', e)
    message.error('操作失败，请稍后重试')
  }
}

// 点踩原因弹窗
const reasonVisible = ref(false)
const reasonText = ref('')
const reasonTarget = ref(null)
const reasonSubmitting = ref(false)

const submitReason = async () => {
  const m = reasonTarget.value
  if (!m) return
  const reason = (reasonText.value || '').trim()
  if (!reason) {
    reasonVisible.value = false
    reasonTarget.value = null
    reasonText.value = ''
    return
  }
  reasonSubmitting.value = true
  try {
    await feedbackApi.upsert(m.id, {
      conversation_id: props.conversationId || undefined,
      rating: 'down',
      reason
    })
    if (feedbackState[m.id]) feedbackState[m.id].reason = reason
    message.success('已记录反馈原因')
  } catch (e) {
    console.error('提交原因失败', e)
    message.error('提交原因失败，请稍后重试')
    return // 保留弹窗以便重试
  } finally {
    reasonSubmitting.value = false
  }
  reasonVisible.value = false
  reasonTarget.value = null
  reasonText.value = ''
}

const cancelReason = () => {
  reasonVisible.value = false
  reasonTarget.value = null
  reasonText.value = ''
}
</script>

<style lang="less" scoped>
.refs {
  display: flex;
  margin-bottom: 20px;
  margin-top: 10px;
  color: var(--gray-500);
  font-size: 13px;
  gap: 10px;

  .item {
    background: var(--gray-100);
    color: var(--gray-700);
    padding: 2px 8px;
    border-radius: 8px;
    font-size: 13px;
    user-select: none;

    &.btn {
      cursor: pointer;
      &:hover {
        background: var(--gray-200);
      }
      &:active {
        background: var(--gray-300);
      }
    }

    // 选中态：点赞/点踩高亮
    &.active {
      background: var(--primary-color, #1677ff);
      color: #fff;
      &:hover {
        background: var(--primary-color, #1677ff);
        opacity: 0.85;
      }
    }

    // 生成中禁用
    &.disabled {
      opacity: 0.4;
      cursor: not-allowed;
      pointer-events: none;
    }
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
}

.reason-hint {
  margin-bottom: 8px;
  color: var(--gray-500);
}
</style>
