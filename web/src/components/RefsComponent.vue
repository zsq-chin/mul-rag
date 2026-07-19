<template>
  <div class="refs" v-if="showRefs">
    <div class="tags">
      <span class="item btn" @click="likeThisResponse(msg)"><LikeOutlined /></span>
      <span class="item btn" @click="dislikeThisResponse(msg)"><DislikeOutlined /></span>
      <span v-if="msg.meta?.server_model_name" class="item" @click="console.log(msg)">
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
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useClipboard } from '@vueuse/core'
import { message } from 'ant-design-vue'
import { saveAs } from 'file-saver' // 新增导入
import {
  GlobalOutlined,
  FileTextOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  BulbOutlined,
  ReloadOutlined,
  FireTwoTone,
  DownloadOutlined, // 新增图标
  LikeOutlined,      // 新增点赞图标
  DislikeOutlined,    // 新增点踩图标
} from '@ant-design/icons-vue'

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
  }
})

const msg = ref(props.message)

// 使用 useClipboard 实现复制功能
const { copy, isSupported } = useClipboard()

const showKey = (key) => {
  if (props.showRefs === true) {
    return true
  }
  return props.showRefs.includes(key)
}

// 定义 copy 方法
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

// 新增：下载为Word功能
const downloadAsWord = (content) => {
  try {
    // 构造HTML格式（Word可识别）
    const htmlContent = `
      <html>
        <body>
          <div style="font-size: 14px; line-height: 1.8;">
            ${content.replace(/\n/g, '<br>')} <!-- 换行符转换为HTML换行 -->
          </div>
        </body>
      </html>
    `
    // 创建Blob对象，指定MIME类型为Word格式
    const blob = new Blob([htmlContent], { type: 'application/msword;charset=utf-8' })
    // 下载文件，文件名默认“回答内容.doc”
    saveAs(blob, '回答内容.doc')
    message.success('下载已开始')
  } catch (error) {
    console.error('下载失败:', error)
    message.error('下载失败，请重试')
  }
}

const showRefs = computed(() => (msg.value.role=='received' || msg.value.role=='assistant') && msg.value.status=='finished')

// 打开全局refs抽屉
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

// 添加重新生成方法
const regenerateMessage = () => {
  emit('retry')
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
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
}
</style>
